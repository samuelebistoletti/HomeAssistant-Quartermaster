"""Tests for notification routing."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.inventory import StockOperation
from custom_components.quartermaster.notify import in_quiet_hours
from custom_components.quartermaster.storage.models import Channel


def test_quiet_hours() -> None:
    """Windows across midnight and malformed values."""
    late, noon = datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 1, 12, 0)
    assert in_quiet_hours("22:00-07:00", late)
    assert not in_quiet_hours("22:00-07:00", noon)
    assert in_quiet_hours("11:00-13:00", noon)
    for spec in (None, "", "nonsense", "25:00-07:00"):
        assert not in_quiet_hours(spec, late)


class Notify:
    """notify.send_message recording messages per entity."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.messages: list[tuple[str, str]] = []
        self.fail: set[str] = set()
        hass.services.async_register("notify", "send_message", self._send)
        hass.states.async_set("notify.phone", "unknown")
        hass.states.async_set("notify.echo", "unavailable")
        hass.states.async_set("notify.tablet", "unknown")

    async def _send(self, call: ServiceCall) -> None:
        targets = call.data["entity_id"]
        for entity_id in [targets] if isinstance(targets, str) else targets:
            if entity_id in self.fail:
                raise ServiceValidationError("down")
            self.messages.append((entity_id, call.data["message"]))


@pytest.fixture
async def notified(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> tuple[MockConfigEntry, Notify]:
    """Every message type to echo (unavailable), then phone, then tablet."""
    hass.config.language = "it"
    notify = Notify(hass)
    chain = ["notify.echo", "notify.phone", "notify.tablet"]
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            f"notify_{kind}": chain
            for kind in (
                "photo_request",
                "check_result",
                "expiring",
                "camera",
                "essential_finished",
                "shopping",
                "weekly",
                "voice_reply",
            )
        },
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    inventory = init_integration.runtime_data.inventory
    await inventory.async_add_zone("Fridge", aliases=["frigo"])
    await inventory.async_add_product("Milk", default_zone="fridge", unit="bottle")
    await inventory.async_set_stock("milk", 2, channel=Channel.SERVICE)
    await init_integration.runtime_data.coordinator.async_refresh()
    return init_integration, notify


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_fallback_and_events(hass: HomeAssistant, notified: Any) -> None:
    """The first available entity gets the message; events become texts."""
    _entry, notify = notified
    hass.bus.async_fire(
        f"{DOMAIN}_photo_request",
        {"zone": "Frigo", "reason": "low_confidence", "reminder": False},
    )
    await _settle(hass)
    assert notify.messages[-1] == (
        "notify.phone",
        "Mandami una foto di Frigo: l'immagine della telecamera non era abbastanza chiara.",
    )
    notify.fail.add("notify.phone")
    hass.bus.async_fire(
        f"{DOMAIN}_show_recognized",
        {"zone": "Garage", "product": "Passata", "action": "taken", "quantity": 1},
    )
    await _settle(hass)
    assert notify.messages[-1] == (
        "notify.tablet",
        "Ho segnato 1 Passata in meno in Garage.",
    )
    notify.fail.clear()
    hass.bus.async_fire(
        f"{DOMAIN}_ask_result", {"channel": "alexa", "speech": "Fatto, latte finito."}
    )
    hass.bus.async_fire(f"{DOMAIN}_ask_result", {"channel": "telegram", "speech": "x"})
    hass.bus.async_fire(
        f"{DOMAIN}_zone_checked",
        {"zone_id": "fridge", "status": "updated", "applied": 2, "proposal_id": 5},
    )
    hass.bus.async_fire(
        f"{DOMAIN}_zone_checked", {"zone_id": "fridge", "status": "failed"}
    )
    await _settle(hass)
    texts = [text for _, text in notify.messages[-3:]]
    assert texts[0] == "Fatto, latte finito."
    assert sorted(texts[1:]) == sorted(
        [
            "Controllo di Fridge fatto: 2 variazioni applicate, proposta in attesa: 1.",
            "Non sono riuscito a controllare Fridge: " + "l'analisi non è riuscita.",
        ]
    )


async def test_essential_running_out(hass: HomeAssistant, notified: Any) -> None:
    """An essential going below its minimum is announced once."""
    entry, notify = notified
    inventory = entry.runtime_data.inventory
    await inventory.async_set_essential("milk", essential=True, min_stock=3)
    await entry.runtime_data.coordinator.async_refresh()
    await _settle(hass)
    await entry.runtime_data.coordinator.async_refresh()
    await _settle(hass)
    assert [t for _, t in notify.messages].count(
        "Milk sta finendo: l'ho messo in lista."
    ) == 1


async def test_shopping_message_with_reasons(
    hass: HomeAssistant, notified: Any
) -> None:
    """Go shopping sends the list, then suggestions with readable reasons."""
    entry, notify = notified
    inventory = entry.runtime_data.inventory
    await inventory.async_set_essential(
        "milk", essential=True, min_stock=1, target_stock=4
    )
    await inventory.async_add_product("Jam", default_zone="fridge")
    await inventory.async_set_stock("jam", 1, channel=Channel.SERVICE)
    await inventory.async_set_stock("jam", 0, channel=Channel.SERVICE)
    await inventory.async_adjust_stock(
        "jam", StockOperation.PURCHASE, channel=Channel.SERVICE
    )
    await inventory.async_adjust_stock(
        "jam", StockOperation.FINISH, channel=Channel.SERVICE
    )
    await hass.services.async_call(DOMAIN, "start_shopping", {}, blocking=True)
    await _settle(hass)
    texts = [t for _, t in notify.messages]
    assert "La lista della spesa è vuota." in texts
    suggestions = next(t for t in texts if t.startswith("Potrebbe servirti anche:"))
    today = dt_util.now().date()
    assert "• Milk: ne hai 2, di solito ne tieni 4" in suggestions
    assert f"• Jam: l'hai finito il {today.day}/{today.month}" in suggestions


async def test_quiet_hours_hold_messages(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notified: Any
) -> None:
    """During quiet hours messages wait; they go out when quiet hours end."""
    entry, notify = notified
    local = dt_util.get_default_time_zone()
    # Timers are set up at the real time and only fire forward: stay ahead.
    day = dt_util.now().date() + timedelta(days=1)
    freezer.move_to(datetime.combine(day, datetime.min.time(), local).replace(hour=23))
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "quiet_hours": "22:00-07:00"}
    )
    hass.bus.async_fire(
        f"{DOMAIN}_photo_request",
        {"zone": "Frigo", "reason": "reminder", "reminder": True},
    )
    await _settle(hass)
    assert notify.messages == []
    assert len(entry.runtime_data.notifier.queue) == 1
    freezer.move_to(
        datetime.combine(day + timedelta(days=1), datetime.min.time(), local).replace(
            hour=7, minute=1, second=30
        )
    )
    async_fire_time_changed(hass)
    await _settle(hass)
    assert notify.messages[-1][1].startswith(
        "Promemoria: serve ancora una foto di Frigo"
    )


async def test_expiring_and_weekly(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notified: Any
) -> None:
    """At 9:00 expiring products go out; on Monday also the weekly summary."""
    entry, notify = notified
    inventory = entry.runtime_data.inventory
    local = dt_util.get_default_time_zone()
    # The next Monday, always ahead of the real time the timers were set up at.
    today = dt_util.now().date()
    monday = today + timedelta(days=7 - today.weekday())
    midnight = datetime.combine(monday, datetime.min.time(), local)
    freezer.move_to(midnight.replace(hour=8))
    best_before = monday + timedelta(days=1)
    await inventory.async_set_stock(
        "milk", 1, channel=Channel.SERVICE, best_before=best_before
    )
    await entry.runtime_data.coordinator.async_refresh()
    freezer.move_to(midnight.replace(hour=9, second=30))
    async_fire_time_changed(hass)
    await _settle(hass)
    texts = [t for _, t in notify.messages]
    expected = f"In scadenza:\n• Milk (Fridge): {best_before.day}/{best_before.month}"
    assert expected in texts
    assert any(t.startswith("Questa settimana:") for t in texts)

    # Nothing expiring: no message.
    await inventory.async_set_stock("milk", 0, channel=Channel.SERVICE)
    await entry.runtime_data.coordinator.async_refresh()
    count = len(notify.messages)
    await entry.runtime_data.notifier.async_send_expiring()
    assert len(notify.messages) == count


async def test_telegram_copies_with_buttons(hass: HomeAssistant, notified: Any) -> None:
    """With telegram_notify on, private allowed chats also get the message."""
    entry, _ = notified
    sent: list[dict[str, Any]] = []

    async def send(call: ServiceCall) -> None:
        sent.append(dict(call.data))

    hass.services.async_register("telegram_bot", "send_message", send)
    hass.states.async_set("event.bot", "unknown")
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "telegram_event_entity": "event.bot",
            "telegram_chats": ["11", "-22"],
            "telegram_notify": True,
        },
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire(
        f"{DOMAIN}_show_recognized",
        {"zone": "Garage", "product": "Passata", "action": "returned", "quantity": 2},
    )
    await _settle(hass)
    [message] = sent
    assert message["chat_id"] == [11]
    assert message["message"] == "Ho segnato 2 Passata rimesso in Garage."
    assert message["inline_keyboard"] == ["Annulla:/qm_undo_camera"]


async def test_camera_request_buttons(hass: HomeAssistant, notified: Any) -> None:
    """A sighting is asked about with the usual quantities; a tap confirms it."""
    entry, notify = notified
    sent: list[dict[str, Any]] = []

    async def send(call: ServiceCall) -> None:
        sent.append(dict(call.data))

    hass.services.async_register("telegram_bot", "send_message", send)
    hass.services.async_register("telegram_bot", "answer_callback_query", send)
    hass.states.async_set("event.bot", "unknown")
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "telegram_event_entity": "event.bot",
            "telegram_chats": ["11"],
            "telegram_notify": True,
        },
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    await runtime.inventory.async_add_zone("Garage")
    passata = await runtime.inventory.async_add_product(
        "Passata", default_zone="garage"
    )
    await runtime.inventory.async_set_stock("passata", 4, channel=Channel.SERVICE)
    proposal = await runtime.proposals.async_propose_movement(
        "garage",
        passata,
        action="taken",
        quantity=1,
        current=4,
        confidence=0.9,
        seen_as="Passata",
        source="garage:0",
    )
    hass.bus.async_fire(
        f"{DOMAIN}_show_request",
        {
            "proposal_id": proposal.id,
            "zone": "Garage",
            "product": "Passata",
            "action": "taken",
            "quantity": 1,
            "current": 4,
        },
    )
    await _settle(hass)
    text = "Garage: hai preso 1 Passata? Ne avevi 4, ne resterebbero 3."
    assert ("notify.phone", text) in notify.messages
    message = sent[-1]
    assert message["message"] == text
    assert message["inline_keyboard"] == [
        f"✔ 1:/qm_ok {proposal.id}, 2:/qm_qty {proposal.id} 2,"
        f" Tutti (4):/qm_qty {proposal.id} 4, No:/qm_no {proposal.id}"
    ]

    # "2" on Telegram: two are consumed, and the outcome carries Undo.
    hass.states.async_set(
        "event.bot",
        dt_util.utcnow().isoformat(),
        {
            "event_type": "telegram_callback",
            "chat_id": 11,
            "id": "q",
            "data": f"/qm_qty {proposal.id} 2",
        },
    )
    await _settle(hass)
    [level] = await runtime.inventory.async_get_stock(product_id=passata.id)
    assert level.quantity == 2
    outcome = next(m for m in sent if m.get("message", "").startswith("Ho segnato"))
    assert outcome["message"] == "Ho segnato 2 Passata in meno in Garage."
    assert outcome["inline_keyboard"][0].startswith("Annulla:/qm_undo_camera ")

    # Goods placed with nothing to undo: plain information, no buttons.
    hass.bus.async_fire(
        f"{DOMAIN}_show_request",
        {
            "proposal_id": None,
            "zone": "Garage",
            "product": "Passata",
            "action": "returned",
            "quantity": 2,
        },
    )
    await _settle(hass)
    assert sent[-1]["message"].startswith("Ho visto mettere 2 Passata in Garage.")
    assert "inline_keyboard" not in sent[-1]


async def test_put_away_summary(
    hass: HomeAssistant, notified: Any, freezer: FrozenDateTimeFactory
) -> None:
    """Goods put away within two minutes give one summary; differences only told."""
    _entry, notify = notified
    for product_id, product, seen, bought in (
        (1, "Latte", 1, 3),
        (2, "Pasta", 2, 2),
        (1, "Latte", 3, 3),
    ):
        hass.bus.async_fire(
            f"{DOMAIN}_show_put_away",
            {
                "zone_id": "garage",
                "zone": "Garage",
                "product_id": product_id,
                "product": product,
                "seen": seen,
                "bought": bought,
            },
        )
    await _settle(hass)
    assert notify.messages == []
    freezer.tick(timedelta(minutes=2, seconds=1))
    async_fire_time_changed(hass)
    await _settle(hass)
    assert notify.messages == [
        (
            "notify.phone",
            "Spesa messa via: Latte 3 visti su 3 comprati, Pasta 2 visti su 2 comprati.",
        )
    ]
    hass.bus.async_fire(
        f"{DOMAIN}_show_put_away",
        {"product_id": 1, "product": "Latte", "seen": 4, "bought": 3, "zone": "Garage"},
    )
    await _settle(hass)
    freezer.tick(timedelta(minutes=2, seconds=1))
    async_fire_time_changed(hass)
    await _settle(hass)
    assert notify.messages[-1][1] == (
        "Spesa messa via: Latte 4 visti su 3 comprati.\n"
        "Ho visto 4 Latte, gli acquisti delle ultime 24 ore dicono 3: "
        "non ho cambiato nulla."
    )


async def test_check_result_lists_products_not_seen(
    hass: HomeAssistant, notified: Any
) -> None:
    """A camera count names the products it did not see, marked as such."""
    entry, notify = notified
    proposal = await entry.runtime_data.proposals.async_propose(
        "fridge",
        [
            {
                "key": 0,
                "product_id": 1,
                "product": "Mozzarella",
                "seen_as": "mozzarella",
                "current": 1,
                "proposed": 0,
                "unit": None,
                "confidence": 0.9,
                "missing": True,
            }
        ],
        channel=Channel.CAMERA,
        user_id=None,
        extra={"not_seen": ["mozzarella"]},
    )
    hass.bus.async_fire(
        f"{DOMAIN}_zone_checked",
        {"zone_id": "fridge", "status": "updated", "proposal_id": proposal.id},
    )
    await _settle(hass)
    assert notify.messages[-1][1].endswith("\n• Mozzarella: 1 → 0 (non visto)")
