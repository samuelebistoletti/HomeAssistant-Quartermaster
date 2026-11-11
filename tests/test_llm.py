"""Tests for the LLM tools and quartermaster.ask with a simulated agent."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Literal
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.auth.models import User
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events

from custom_components.quartermaster.const import CONF_WRITE_USERS, DOMAIN
from custom_components.quartermaster.core.inventory import InventoryService
from custom_components.quartermaster.core.voice import (
    EVENT_ASK_RESULT,
    reprompt_for,
    strip_tool_calls,
)
from custom_components.quartermaster.storage.models import Channel, MovementKind


@pytest.fixture
async def kitchen(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> InventoryService:
    """A small household: two cheeses in the fridge, milk, passata in the garage."""
    inventory: InventoryService = init_integration.runtime_data.inventory
    await inventory.async_add_zone("Fridge", aliases=["frigo"])
    await inventory.async_add_zone("Garage", aliases=["garage"])
    await inventory.async_add_product(
        "Milk",
        aliases=["latte"],
        default_zone="frigo",
        unit="bottle",
        essential=True,
        min_stock=1,
    )
    await inventory.async_add_product(
        "Parmesan", aliases=["parmigiano"], category="formaggi"
    )
    await inventory.async_add_product("Mozzarella", category="formaggi")
    await inventory.async_add_product("Passata", aliases=["passate"], unit="bottle")
    for product, zone, quantity in (
        ("milk", "fridge", 1),
        ("parmesan", "fridge", 1),
        ("mozzarella", "fridge", 2),
        ("passata", "garage", 4),
    ):
        await inventory.async_set_stock(
            product, quantity, zone_ref=zone, channel=Channel.SERVICE
        )
    await init_integration.runtime_data.coordinator.async_refresh()
    return inventory


async def _tool(
    hass: HomeAssistant, tool: str, context: Context | None = None, **args: Any
) -> dict[str, Any]:
    api = await llm.async_get_api(
        hass,
        llm.LLM_API_ASSIST,
        llm.LLMContext(
            platform="test",
            context=context or Context(),
            language="en",
            assistant=conversation.DOMAIN,
            device_id=None,
        ),
    )
    result = await api.async_call_tool(
        llm.ToolInput(tool_name=f"{DOMAIN}__{tool}", tool_args=args)
    )
    assert isinstance(result, llm.ToolResult)
    return dict(result.data)


async def _quantity(inventory: InventoryService, product: str, zone: str) -> float:
    found = await inventory.async_find_product(product)
    levels = await inventory.async_get_stock(product_id=found.id, zone_id=zone)
    return levels[0].quantity if levels else 0.0


# Tools


async def test_tools_are_offered(
    hass: HomeAssistant, kitchen: InventoryService
) -> None:
    """The Assist API carries every tool with the integration set."""
    api = await llm.async_get_api(
        hass,
        llm.LLM_API_ASSIST,
        llm.LLMContext("test", Context(), "en", conversation.DOMAIN, None),
    )
    names = {tool.name for tool in api.tools if tool.name.startswith(DOMAIN)}
    assert names == {
        f"{DOMAIN}__{name}"
        for name in (
            "list_zones",
            "get_stock",
            "get_missing",
            "get_suggestions",
            "get_expiring",
            "get_shopping_list",
            "add_to_shopping_list",
            "remove_from_shopping_list",
            "adjust_stock",
            "undo_last",
            "set_essential",
            "create_product",
            "confirm_pending",
            "check_zone",
            "arm_show_mode",
        )
    }
    assert all(
        tool.integration == DOMAIN for tool in api.tools if tool.name.startswith(DOMAIN)
    )
    assert "quartermaster__" in api.api_prompt


async def test_read_tools(hass: HomeAssistant, kitchen: InventoryService) -> None:
    """Zones, stock with age, missing, suggestions, expiring, shopping list."""
    zones = await _tool(hass, "list_zones")
    assert [z["zone"] for z in zones["zones"]] == ["Fridge", "Garage"]
    assert zones["zones"][0]["updated_age"] == "P0DT0H"

    cheese = await _tool(hass, "get_stock", product="formaggio")
    assert {row["product"] for row in cheese["stock"]} == {"Parmesan", "Mozzarella"}
    garage = await _tool(hass, "get_stock", zone="garage")
    assert [row["product"] for row in garage["stock"]] == ["Passata"]
    everything = await _tool(hass, "get_stock")
    assert len(everything["stock"]) == 4
    assert await _tool(hass, "get_stock", product="caviar") == {
        "found": False,
        "product": "caviar",
    }

    await kitchen.async_set_stock("milk", 0, channel=Channel.SERVICE)
    await hass.config_entries.async_entries(DOMAIN)[
        0
    ].runtime_data.coordinator.async_refresh()
    assert (await _tool(hass, "get_missing"))["missing"][0]["product"] == "Milk"
    assert (await _tool(hass, "get_suggestions"))["suggestions"] == []
    assert (await _tool(hass, "get_expiring", days=5))["expiring"] == []
    assert (await _tool(hass, "get_shopping_list"))["items"] == [
        {"item": "Milk", "quantity": 1.0, "unit": "bottle"}
    ]


async def test_shopping_list_tools(
    hass: HomeAssistant, kitchen: InventoryService
) -> None:
    """Add, then remove with ambiguity and not-found handling."""
    added = await _tool(hass, "add_to_shopping_list", item="Parmigiano", quantity=1)
    assert added == {"added": "Parmigiano", "quantity": 1.0, "is_product": True}
    await _tool(hass, "add_to_shopping_list", item="Paper towels")
    await _tool(hass, "add_to_shopping_list", item="Paper cups", from_suggestion=True)
    assert (await _tool(hass, "remove_from_shopping_list", item="paper"))[
        "candidates"
    ] == [
        "Paper towels",
        "Paper cups",
    ]
    assert await _tool(hass, "remove_from_shopping_list", item="paper cups") == {
        "removed": "Paper cups"
    }
    assert (await _tool(hass, "remove_from_shopping_list", item="bread"))[
        "found"
    ] is False
    items = [i["item"] for i in (await _tool(hass, "get_shopping_list"))["items"]]
    assert items == ["Parmigiano", "Paper towels"]


async def test_adjust_stock_rules(
    hass: HomeAssistant, kitchen: InventoryService
) -> None:
    """Spec §6.2: one candidate writes, several ask, none reports."""
    done = await _tool(hass, "adjust_stock", product="latte", action="finished")
    assert done["status"] == "done"
    assert (done["product"], done["quantity_in_zone"]) == ("Milk", 0)
    assert done["added_to_shopping_list"] is True

    ambiguous = await _tool(
        hass, "adjust_stock", product="formaggio", action="finished", zone="frigo"
    )
    assert ambiguous["status"] == "ambiguous"
    assert [c["product"] for c in ambiguous["candidates"]] == ["Mozzarella", "Parmesan"]
    assert await _quantity(kitchen, "mozzarella", "fridge") == 2

    taken = await _tool(
        hass, "adjust_stock", product="passate", action="consumed", quantity=2
    )
    assert (taken["zone"], taken["quantity_in_zone"]) == ("Garage", 2)

    assert (await _tool(hass, "adjust_stock", product="latte", action="consumed"))[
        "status"
    ] == "not_in_stock"
    assert (await _tool(hass, "adjust_stock", product="caviar", action="consumed")) == {
        "status": "not_found",
        "product": "caviar",
    }
    bought = await _tool(
        hass, "adjust_stock", product="latte", action="bought", quantity=2
    )
    assert (bought["zone"], bought["total"], bought["added_to_shopping_list"]) == (
        "Fridge",
        2,
        False,
    )
    opened = await _tool(hass, "adjust_stock", product="parmigiano", action="opened")
    assert opened["opened"] is True
    [level] = await kitchen.async_get_stock(
        product_id=(await kitchen.async_find_product("parmesan")).id
    )
    assert level.opened_at is not None

    assert (await _tool(hass, "adjust_stock", product="latte"))[
        "error"
    ] == "action_required"
    assert (
        await _tool(
            hass, "adjust_stock", product="latte", action="bought", zone="attic"
        )
    )["error"] == "zone_not_found"


async def test_adjust_stock_zone_ambiguity(
    hass: HomeAssistant, kitchen: InventoryService
) -> None:
    """A product kept in two zones asks which zone."""
    await kitchen.async_set_stock(
        "passata", 1, zone_ref="fridge", channel=Channel.SERVICE
    )
    result = await _tool(hass, "adjust_stock", product="passata", action="finished")
    assert result == {
        "status": "ambiguous_zone",
        "product": "Passata",
        "zones": ["Fridge", "Garage"],
    }


async def test_confirmations(hass: HomeAssistant, kitchen: InventoryService) -> None:
    """Large purchases, essentials and new products need a confirmation id."""
    large = await _tool(
        hass, "adjust_stock", product="latte", action="bought", quantity=30
    )
    assert large["status"] == "confirmation_required"
    assert await _quantity(kitchen, "milk", "fridge") == 1
    done = await _tool(
        hass, "adjust_stock", product="x", confirmation_id=large["confirmation_id"]
    )
    assert done["total"] == 31
    again = await _tool(
        hass, "adjust_stock", product="x", confirmation_id=large["confirmation_id"]
    )
    assert again["error"] == "confirmation_expired"

    essential = await _tool(hass, "set_essential", product="passata", min_stock=2)
    assert essential["status"] == "confirmation_required"
    assert not (await kitchen.async_find_product("passata")).essential
    # An id is only valid for the tool that issued it.
    wrong = await _tool(
        hass, "create_product", name="x", confirmation_id=essential["confirmation_id"]
    )
    assert wrong["error"] == "confirmation_expired"
    essential = await _tool(hass, "set_essential", product="passata", min_stock=2)
    applied = await _tool(
        hass, "set_essential", product="x", confirmation_id=essential["confirmation_id"]
    )
    assert (applied["essential"], applied["min_stock"]) == (True, 2)
    missing_min = await _tool(hass, "set_essential", product="mozzarella")
    assert missing_min["error"] == "min_stock_required"

    product = await _tool(
        hass, "create_product", name="Butter", zone="frigo", unit="pack"
    )
    assert product["status"] == "confirmation_required"
    created = await _tool(
        hass, "create_product", name="x", confirmation_id=product["confirmation_id"]
    )
    assert created == {"status": "done", "product": "Butter"}
    assert (await kitchen.async_find_product("butter")).default_zone_id == "fridge"


async def test_confirmation_expires(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, kitchen: InventoryService
) -> None:
    """A confirmation id is valid for five minutes."""
    product = await _tool(hass, "create_product", name="Butter")
    freezer.tick(timedelta(minutes=6))
    expired = await _tool(
        hass, "create_product", name="x", confirmation_id=product["confirmation_id"]
    )
    assert expired["error"] == "confirmation_expired"


async def test_undo_tool_is_per_channel_and_user(
    hass: HomeAssistant, kitchen: InventoryService
) -> None:
    """Undo only reverts the caller's own last change on Assist."""
    alice, bob = Context(user_id="alice"), Context(user_id="bob")
    await _tool(
        hass, "adjust_stock", context=alice, product="passata", action="consumed"
    )
    await _tool(
        hass, "adjust_stock", context=bob, product="mozzarella", action="consumed"
    )
    undone = await _tool(hass, "undo_last", context=alice)
    assert undone["undone"] == [
        {"product": "Passata", "zone": "Garage", "change": -1.0}
    ]
    assert await _quantity(kitchen, "mozzarella", "fridge") == 1
    assert (await _tool(hass, "undo_last", context=alice))["error"] == "nothing_to_undo"


async def test_tools_without_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """With the entry unloaded the tools are not offered and calls fail softly."""
    from custom_components.quartermaster import llm as qm_llm

    await hass.config_entries.async_unload(init_integration.entry_id)
    ctx = llm.LLMContext("test", Context(), "en", conversation.DOMAIN, None)
    assert qm_llm.async_get_tools(hass, ctx, llm.LLM_API_ASSIST) is None
    assert qm_llm.async_get_tools(hass, ctx, "other") is None
    result = await qm_llm.ListZonesTool().async_call(
        hass, llm.ToolInput(tool_name="x", tool_args={}), ctx
    )
    assert result.error is True


# quartermaster.ask with a simulated agent


Handler = Callable[[str], tuple[str, dict[str, Any]] | None]


class ScriptedAgent(conversation.AbstractConversationAgent):
    """Stands in for an LLM: maps phrases to tool calls and phrases replies."""

    def __init__(
        self, hass: HomeAssistant, script: dict[str, tuple[str, dict[str, Any]]]
    ) -> None:
        self.hass = hass
        self.script = script
        self.delay = 0.0
        self.fail = False
        self.suffix = ""
        self.busy = False
        self.conversation_ids: list[str | None] = []

    @property
    def supported_languages(self) -> Literal["*"]:
        return "*"

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        self.conversation_ids.append(user_input.conversation_id)
        if self.fail:
            raise HomeAssistantError("provider down")
        if self.busy:
            # Home Assistant processes one message per conversation at a time.
            raise HomeAssistantError("This conversation is busy with another message")
        self.busy = True
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.busy = False
        api = await llm.async_get_api(
            self.hass, llm.LLM_API_ASSIST, user_input.as_llm_context("test")
        )
        tool, args = self.script[user_input.text]
        result = await api.async_call_tool(
            llm.ToolInput(tool_name=f"{DOMAIN}__{tool}", tool_args=args)
        )
        assert isinstance(result, llm.ToolResult)
        data = result.data
        status = data.get("status")
        if status == "ambiguous":
            names = " o ".join(c["product"] for c in data["candidates"])  # type: ignore[index, union-attr]
            speech, follow_up = f"Ho {names}. Quale?", True
        elif status == "done":
            speech, follow_up = f"Fatto: {data['product']}.", False
        else:
            speech, follow_up = str(data), False
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(speech + self.suffix)
        return conversation.ConversationResult(
            response=response,
            conversation_id=user_input.conversation_id or "conv-1",
            continue_conversation=follow_up,
        )


SCRIPT: dict[str, tuple[str, dict[str, Any]]] = {
    # Italian
    "ho finito il latte": ("adjust_stock", {"product": "latte", "action": "finished"}),
    "ho finito il formaggio in frigo": (
        "adjust_stock",
        {"product": "formaggio", "action": "finished", "zone": "frigo"},
    ),
    "la mozzarella": ("adjust_stock", {"product": "mozzarella"}),
    "annulla": ("undo_last", {}),
    "ho preso due passate dal garage": (
        "adjust_stock",
        {"product": "passate", "action": "consumed", "quantity": 2, "zone": "garage"},
    ),
    "ho comprato tre latte": (
        "adjust_stock",
        {"product": "latte", "action": "bought", "quantity": 3},
    ),
    "ho aperto il parmigiano": (
        "adjust_stock",
        {"product": "parmigiano", "action": "opened"},
    ),
    # English
    "I finished the parmesan": (
        "adjust_stock",
        {"product": "parmesan", "action": "finished"},
    ),
    "I used one passata": (
        "adjust_stock",
        {"product": "passata", "action": "consumed"},
    ),
    "I bought milk": ("adjust_stock", {"product": "milk", "action": "bought"}),
    "undo": ("undo_last", {}),
}


@pytest.fixture
async def agent(
    hass: HomeAssistant, init_integration: MockConfigEntry, kitchen: InventoryService
) -> ScriptedAgent:
    """Register the scripted agent and select it in the options."""
    agent_entry = MockConfigEntry(domain="test")
    agent_entry.add_to_hass(hass)
    scripted = ScriptedAgent(hass, SCRIPT)
    conversation.async_set_agent(hass, agent_entry, scripted)
    hass.config_entries.async_update_entry(
        init_integration, options={"agent_id": agent_entry.entry_id}
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    return scripted


async def _ask(hass: HomeAssistant, text: str, **data: Any) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN,
        "ask",
        {"text": text, "channel": "alexa", "session_key": "echo", **data},
        blocking=True,
        return_response=True,
        context=Context(user_id="alexa-user"),
    )
    assert response is not None
    return dict(response)


async def test_ask_clarification_dialog(
    hass: HomeAssistant, init_integration: MockConfigEntry, agent: ScriptedAgent
) -> None:
    """Spec §6.2: two cheeses ask a question; the answer completes the write."""
    inventory: InventoryService = init_integration.runtime_data.inventory
    first = await _ask(hass, "ho finito il formaggio in frigo")
    assert first["continue_conversation"] is True
    assert first["reprompt"] == "Quale?"
    assert await _quantity(inventory, "mozzarella", "fridge") == 2

    second = await _ask(hass, "la mozzarella")
    assert second == {
        "speech": "Fatto: Mozzarella.",
        "continue_conversation": False,
        "reprompt": "",
        "conversation_id": "conv-1",
        "in_progress": False,
    }
    assert await _quantity(inventory, "mozzarella", "fridge") == 0
    assert agent.conversation_ids == [None, "conv-1"]
    [entry] = await inventory.async_list_journal(limit=1)
    assert (entry.channel, entry.user_id) == (Channel.ALEXA, "alexa-user")

    await _ask(hass, "annulla")
    assert await _quantity(inventory, "mozzarella", "fridge") == 2


@pytest.mark.parametrize(
    ("phrase", "product", "zone", "kind", "delta"),
    [
        ("ho finito il latte", "milk", "fridge", MovementKind.CONSUME, -1),
        (
            "ho preso due passate dal garage",
            "passata",
            "garage",
            MovementKind.CONSUME,
            -2,
        ),
        ("ho comprato tre latte", "milk", "fridge", MovementKind.PURCHASE, 3),
        ("I finished the parmesan", "parmesan", "fridge", MovementKind.CONSUME, -1),
        ("I used one passata", "passata", "garage", MovementKind.CONSUME, -1),
        ("I bought milk", "milk", "fridge", MovementKind.PURCHASE, 1),
    ],
)
async def test_ask_phrases_write_expected_movements(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    agent: ScriptedAgent,
    phrase: str,
    product: str,
    zone: str,
    kind: MovementKind,
    delta: float,
) -> None:
    """Each phrase leads to one movement on the alexa channel."""
    inventory: InventoryService = init_integration.runtime_data.inventory
    await _ask(hass, phrase)
    [entry] = await inventory.async_list_journal(limit=1)
    found = await inventory.async_find_product(product)
    assert (entry.product_id, entry.zone_id, entry.kind, entry.delta) == (
        found.id,
        zone,
        kind,
        delta,
    )
    assert entry.channel is Channel.ALEXA


async def test_ask_opened_and_english_undo(
    hass: HomeAssistant, init_integration: MockConfigEntry, agent: ScriptedAgent
) -> None:
    """Opening is recorded; undo in English reverts the last change."""
    inventory: InventoryService = init_integration.runtime_data.inventory
    assert (await _ask(hass, "ho aperto il parmigiano"))["speech"] == "Fatto: Parmesan."
    await _ask(hass, "I bought milk")
    await _ask(hass, "undo")
    assert await _quantity(inventory, "milk", "fridge") == 1


async def test_ask_read_only_alexa(
    hass: HomeAssistant, init_integration: MockConfigEntry, agent: ScriptedAgent
) -> None:
    """With the option on, Alexa cannot write."""
    hass.config_entries.async_update_entry(
        init_integration, options={**init_integration.options, "alexa_read_only": True}
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    reply = await _ask(hass, "ho finito il latte")
    assert "read_only" in reply["speech"]
    inventory: InventoryService = init_integration.runtime_data.inventory
    assert await _quantity(inventory, "milk", "fridge") == 1
    # Other channels still write.
    await _ask(hass, "ho finito il latte", channel="telegram")
    assert await _quantity(inventory, "milk", "fridge") == 0


async def test_ask_slow_reply(
    hass: HomeAssistant, init_integration: MockConfigEntry, agent: ScriptedAgent
) -> None:
    """A late reply is acknowledged, then published as an event."""
    events = async_capture_events(hass, EVENT_ASK_RESULT)
    agent.delay = 0.2
    with patch("custom_components.quartermaster.core.voice.REPLY_TIMEOUT", 0.05):
        reply = await _ask(hass, "ho finito il latte", language="it")
    assert reply["in_progress"] is True
    # No late-reply destination: no promise of a message.
    assert reply["speech"] == "Ci sto lavorando. Tra poco chiedimi se è fatto."
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()
    [event] = events
    assert event.data["speech"] == "Fatto: Milk."
    assert (event.data["channel"], event.data["session_key"]) == ("alexa", "echo")

    # With one, the acknowledgement says the answer will follow.
    hass.config_entries.async_update_entry(
        init_integration,
        options={**init_integration.options, "notify_voice_reply": ["notify.phone"]},
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    with patch("custom_components.quartermaster.core.voice.REPLY_TIMEOUT", 0.05):
        reply = await _ask(hass, "ho finito il latte", language="it")
    assert reply["speech"] == "Ci sto lavorando, ti avviso quando ho finito."
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()


async def test_written_channels_wait_for_the_reply(
    hass: HomeAssistant, init_integration: MockConfigEntry, agent: ScriptedAgent
) -> None:
    """Telegram gets one complete reply, without "I'm on it" first."""
    agent.delay = 0.2
    with patch("custom_components.quartermaster.core.voice.REPLY_TIMEOUT", 0.05):
        reply = await _ask(
            hass, "ho finito il latte", language="it", channel="telegram"
        )
    assert reply["in_progress"] is False
    assert reply["speech"] == "Fatto: Milk."


async def test_ask_agent_failure(
    hass: HomeAssistant, init_integration: MockConfigEntry, agent: ScriptedAgent
) -> None:
    """A failing agent gives a spoken apology, not an error."""
    agent.fail = True
    reply = await _ask(hass, "ho finito il latte", language="en")
    assert reply["speech"] == "Sorry, I couldn't do that. Please try again."
    assert reply["continue_conversation"] is False


async def test_ask_session_expires(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    agent: ScriptedAgent,
) -> None:
    """After the session lifetime a new conversation starts."""
    await _ask(hass, "ho finito il latte")
    freezer.tick(timedelta(seconds=121))
    await _ask(hass, "I bought milk")
    assert agent.conversation_ids == [None, None]


async def test_ask_without_response(
    hass: HomeAssistant, init_integration: MockConfigEntry, agent: ScriptedAgent
) -> None:
    """The action also works without asking for a response."""
    assert (
        await hass.services.async_call(
            DOMAIN, "ask", {"text": "I bought milk"}, blocking=True
        )
        is None
    )


def test_reprompt_for() -> None:
    """The reprompt repeats the last question, or the whole reply."""
    assert (
        reprompt_for("Ho due formaggi. Quale hai finito? Dimmi pure")
        == "Quale hai finito?"
    )
    assert reprompt_for("Fatto.") == "Fatto."


async def test_ask_tool_call_written_as_text(
    hass: HomeAssistant, init_integration: MockConfigEntry, agent: ScriptedAgent
) -> None:
    """A tool call written into the reply is not spoken, and the user is told."""
    agent.suffix = ' [quartermaster__add_to_shopping_list(item="milk")]'
    reply = await _ask(hass, "ho finito il latte", language="it")
    assert reply["speech"] == (
        "Fatto: Milk. Una parte della richiesta non è stata eseguita:"
        " controlla e riprova."
    )


async def test_ask_waits_for_the_running_turn(
    hass: HomeAssistant, init_integration: MockConfigEntry, agent: ScriptedAgent
) -> None:
    """A second request in the same session waits instead of failing as busy."""
    agent.delay = 0.1
    first, second = await asyncio.gather(
        _ask(hass, "ho finito il latte"), _ask(hass, "I bought milk")
    )
    assert first["speech"] == "Fatto: Milk."
    assert second["speech"] == "Fatto: Milk."
    assert agent.conversation_ids == [None, "conv-1"]


def test_strip_tool_calls() -> None:
    """Tool calls written as text in the usual model formats are removed."""
    assert strip_tool_calls("Fatto.") == ("Fatto.", False)
    assert strip_tool_calls("Ok. [add_item(name='latte'), undo_last()] Altro?") == (
        "Ok. Altro?",
        True,
    )
    assert strip_tool_calls('Ok quartermaster__get_stock(product="latte")') == (
        "Ok",
        True,
    )
    assert strip_tool_calls(
        'Sì. {"name": "quartermaster__undo_last", "parameters": {}}'
    ) == ("Sì.", True)
    assert strip_tool_calls("Ok <tool_call>{}</tool_call>") == ("Ok", True)
    assert strip_tool_calls("<|python_tag|>undo_last()") == ("", True)
    # Ordinary parentheses stay.
    assert strip_tool_calls("Latte (frigo): 2") == ("Latte (frigo): 2", False)


async def test_write_tools_follow_write_users(
    hass: HomeAssistant,
    hass_read_only_user: User,
    init_integration: MockConfigEntry,
    kitchen: InventoryService,
) -> None:
    """A user who may not write cannot write through the tools either."""
    context = Context(user_id=hass_read_only_user.id)
    refused = await _tool(
        hass, "adjust_stock", context=context, product="passata", action="consumed"
    )
    assert refused["error"] == "not_allowed"
    assert await _quantity(kitchen, "passata", "garage") == 4
    # Reading stays open.
    stock = await _tool(hass, "get_stock", context=context, product="passata")
    assert "error" not in stock

    hass.config_entries.async_update_entry(
        init_integration, options={CONF_WRITE_USERS: [hass_read_only_user.id]}
    )
    await hass.async_block_till_done()
    allowed = await _tool(
        hass, "adjust_stock", context=context, product="passata", action="consumed"
    )
    assert "error" not in allowed
    assert await _quantity(kitchen, "passata", "garage") == 3
