"""Tests for camera_show and passage cameras."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.inventory import InventoryError
from custom_components.quartermaster.storage.models import Channel, MovementKind
from custom_components.quartermaster.vision.quality import Frame
from custom_components.quartermaster.vision.schema import StructureMode
from custom_components.quartermaster.vision.show import (
    EVENT_SHOW_PUT_AWAY,
    EVENT_SHOW_RECOGNIZED,
    EVENT_SHOW_REQUEST,
    _in_hours,
    choose_frames,
    combine,
)

from .test_vision import _photo


def test_combine() -> None:
    """Independent sightings combine; one sighting stays as it is."""
    assert combine([0.7]) == 0.7
    assert combine([0.7, 0.5]) == 0.85
    assert combine([]) == 0.0
    assert combine([1.5, -1]) == 1.0


def _frame(index: int, sharp: float, change: float, bits: int) -> Frame:
    return Frame(index, b"", sharp, change, f"{(1 << bits) - 1:016x}")


def test_choose_frames() -> None:
    """Unchanged and duplicate frames are dropped; the rest spread in time."""
    frames = [
        _frame(0, 50, 2, 0),  # no change
        _frame(1, 40, 30, 10),
        _frame(2, 45, 30, 10),  # duplicate of 1, sharper
        _frame(3, 30, 30, 30),
        _frame(4, 20, 30, 50),
        _frame(5, 60, 30, 64),
    ]
    assert [f.index for f in choose_frames(frames, 4)] == [2, 3, 4, 5]
    assert [f.index for f in choose_frames(frames, 2)] == [2, 5]
    assert choose_frames(frames[:1], 4) == []


def test_in_hours() -> None:
    """Hour windows, including ones across midnight."""
    noon, night = datetime(2026, 1, 1, 12, 0), datetime(2026, 1, 1, 23, 30)
    assert _in_hours(None, noon)
    assert _in_hours("07:00-23:00", noon)
    assert not _in_hours("07:00-23:00", night)
    assert _in_hours("22:00-06:00", night)
    assert not _in_hours("22:00-06:00", noon)


class Scene:
    """camera.snapshot returning the empty scene first, then a change."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.calls = 0
        self.changed = True
        hass.services.async_register("camera", "snapshot", self._snapshot)

    async def _snapshot(self, call: ServiceCall) -> None:
        self.calls += 1
        seed = 7 if self.calls == 1 or not self.changed else 100 + self.calls
        Path(call.data["filename"]).write_bytes(_photo((800, 600), seed=seed))


class Model:
    """ai_task.generate_data answering per task (show or passage)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.answers: dict[str, Callable[[], dict[str, Any]]] = {}
        self.calls: list[dict[str, Any]] = []
        self.refuse_many = False
        hass.services.async_register(
            "ai_task",
            "generate_data",
            self._handle,
            supports_response=SupportsResponse.ONLY,
        )

    async def _handle(self, call: ServiceCall) -> dict[str, Any]:
        self.calls.append(dict(call.data))
        if self.refuse_many and len(call.data["attachments"]) > 1:
            raise ServiceValidationError("one attachment only")
        kind = call.data["task_name"].split()[1]
        return {"data": self.answers[kind]()}


@pytest.fixture
async def show(
    hass: HomeAssistant, init_integration: MockConfigEntry, tmp_path: Path
) -> tuple[MockConfigEntry, Scene, Model, int]:
    """A garage shelf with camera_show and a yard passage camera."""
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    scene = Scene(hass)
    model = Model(hass)
    hass.config_entries.async_update_entry(
        init_integration, options={"ai_task_image": "ai_task.vision"}
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    inventory = init_integration.runtime_data.inventory
    await inventory.async_add_zone("Garage")
    await inventory.async_add_zone("Yard")
    passata = await inventory.async_add_product("Passata", default_zone="garage")
    await inventory.async_set_stock("passata", 4, channel=Channel.SERVICE)
    watch = {"window_seconds": 1, "burst_interval_seconds": 0.2, "cooldown_seconds": 0}
    await hass.services.async_call(
        DOMAIN,
        "set_zone",
        {
            "zone": "garage",
            "sources": [
                {
                    "type": "camera_show",
                    "entity_id": "camera.garage",
                    "trigger_entity": "binary_sensor.garage_motion",
                    **watch,
                },
                {"type": "manual"},
            ],
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        "set_zone",
        {
            "zone": "yard",
            "sources": [
                {
                    "type": "passage",
                    "entity_id": "camera.yard",
                    "trigger_entity": "event.yard_motion",
                    **watch,
                },
            ],
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    return init_integration, scene, model, passata.id


def _taken(
    product_id: int, confidence: float, action: str = "taken"
) -> Callable[[], dict[str, Any]]:
    return lambda: {
        "product_shown": True,
        "action": action,
        "product": "Passata",
        "matched_product_id": product_id,
        "quantity": 1,
        "confidence": confidence,
    }


async def _quantity(entry: MockConfigEntry) -> float:
    inventory = entry.runtime_data.inventory
    product = await inventory.async_find_product("passata")
    [level] = await inventory.async_get_stock(product_id=product.id, zone_id="garage")
    return level.quantity


async def _confirm(entry: MockConfigEntry, **correction: float) -> None:
    """Answer yes to the one waiting request, as a Telegram button does."""
    [proposal] = await entry.runtime_data.proposals.async_pending()
    await entry.runtime_data.proposals.async_confirm(
        proposal.id,
        corrections={0: correction} if correction else None,
        channel=Channel.TELEGRAM,
        user_id=None,
    )


async def test_show_taken_and_returned(hass: HomeAssistant, show: Any) -> None:
    """Armed: a product taken is asked about, and written only on a yes."""
    entry, _scene, model, passata = show
    manager = entry.runtime_data.show
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "not_armed"

    recognized = async_capture_events(hass, EVENT_SHOW_RECOGNIZED)
    requests = async_capture_events(hass, EVENT_SHOW_REQUEST)
    await hass.services.async_call(
        DOMAIN, "arm_show_mode", {"zone": "garage"}, blocking=True
    )
    assert hass.states.get("switch.quartermaster_show_mode_garage").state == "on"  # type: ignore[union-attr]
    model.answers["show"] = _taken(passata, 0.9)
    result = await manager.async_handle_trigger("garage:0")
    assert result["status"] == "proposed"
    assert await _quantity(entry) == 4  # a camera never writes
    assert recognized == []
    [request] = requests
    assert (request.data["action"], request.data["quantity"]) == ("taken", 1)
    assert request.data["current"] == 4
    assert len(model.calls[0]["attachments"]) >= 1

    await _confirm(entry)
    assert await _quantity(entry) == 3
    [event] = recognized
    assert (event.data["product"], event.data["action"]) == ("Passata", "taken")
    assert event.data["operation_id"]
    journal = await entry.runtime_data.inventory.async_list_journal(limit=1)
    assert (journal[0].kind, journal[0].channel) == (
        MovementKind.CONSUME,
        Channel.TELEGRAM,
    )
    await entry.runtime_data.coordinator.async_refresh()
    assert hass.states.get("sensor.quartermaster_last_shown_garage").state == "Passata"  # type: ignore[union-attr]

    # Put back after the yes: a request to undo that consumption.
    model.answers["show"] = _taken(passata, 0.9, "returned")
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "proposed"
    assert await _quantity(entry) == 3
    await _confirm(entry)
    assert await _quantity(entry) == 4
    assert recognized[-1].data["action"] == "returned"

    # Put back with nothing to undo: nothing to record, the user is told.
    assert (await manager.async_handle_trigger("garage:0"))["status"] == (
        "needs_receipt"
    )
    assert requests[-1].data["proposal_id"] is None
    assert await _quantity(entry) == 4
    assert await entry.runtime_data.proposals.async_pending() == []
    assert not any((Path(hass.config.media_dirs["local"]) / DOMAIN / "tmp").iterdir())


async def test_placed_goods_match_recent_purchases(
    hass: HomeAssistant, show: Any
) -> None:
    """Gap E3: goods put away only confirm the last 24 hours of purchases."""
    entry, _scene, model, passata = show
    manager = entry.runtime_data.show
    inventory = entry.runtime_data.inventory
    manager.async_arm("garage")
    put_away = async_capture_events(hass, EVENT_SHOW_PUT_AWAY)
    requests = async_capture_events(hass, EVENT_SHOW_REQUEST)
    await inventory.async_record_purchases([(passata, 2)], channel=Channel.TELEGRAM)
    model.answers["show"] = _taken(passata, 0.9, "returned")
    for seen in (1, 2, 3):
        result = await manager.async_handle_trigger("garage:0")
        assert result["status"] == "put_away"
        assert (put_away[-1].data["seen"], put_away[-1].data["bought"]) == (seen, 2)
    assert put_away[-1].data["product"] == "Passata"
    assert await _quantity(entry) == 6  # 4 + the purchase; the camera adds nothing
    assert requests == []
    assert await entry.runtime_data.proposals.async_pending() == []

    # Below min_confidence: still matched, but nobody is told.
    model.answers["show"] = _taken(passata, 0.5, "returned")
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "put_away"
    assert len(put_away) == 3

    # An undone purchase no longer counts: the receipt is asked for.
    await inventory.async_undo_last(channel=Channel.TELEGRAM)
    model.answers["show"] = _taken(passata, 0.9, "returned")
    result = await manager.async_handle_trigger("garage:0")
    assert result["status"] == "needs_receipt"
    assert requests[-1].data["proposal_id"] is None


async def test_taken_then_returned_before_answering(
    hass: HomeAssistant, show: Any
) -> None:
    """A product taken and put back before the answer leaves nothing to ask."""
    entry, _scene, model, passata = show
    manager = entry.runtime_data.show
    manager.async_arm("garage")
    model.answers["show"] = _taken(passata, 0.9)
    await manager.async_handle_trigger("garage:0")
    model.answers["show"] = _taken(passata, 0.9, "returned")
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "cancelled"
    assert await entry.runtime_data.proposals.async_pending() == []
    assert await _quantity(entry) == 4


async def test_another_quantity_and_out_of_stock(
    hass: HomeAssistant, show: Any
) -> None:
    """The answer may give another quantity; nothing in stock, nothing asked."""
    entry, _scene, model, passata = show
    manager = entry.runtime_data.show
    manager.async_arm("garage")
    model.answers["show"] = _taken(passata, 0.9)
    await manager.async_handle_trigger("garage:0")
    # The dashboard sends the quantity taken through the action.
    [proposal] = await entry.runtime_data.proposals.async_pending()
    await hass.services.async_call(
        DOMAIN,
        "confirm_pending",
        {"proposal_id": proposal.id, "corrections": [{"key": 0, "moved": 3}]},
        blocking=True,
    )
    assert await _quantity(entry) == 1
    [consumed] = await entry.runtime_data.inventory.async_list_journal(limit=1)
    assert (consumed.kind, consumed.delta) == (MovementKind.CONSUME, -3)
    # More taken than there is: the request is for what is left.
    model.answers["show"] = lambda: {**_taken(passata, 0.9)(), "quantity": 5}
    await manager.async_handle_trigger("garage:0")
    [proposal] = await entry.runtime_data.proposals.async_pending()
    assert proposal.payload["items"][0]["moved"] == 1
    # The dashboard's corrected count is what is left.
    await _confirm(entry, quantity=0)
    assert await _quantity(entry) == 0
    assert (await manager.async_handle_trigger("garage:0"))["status"] == (
        "not_in_stock"
    )


async def test_low_confidence_corroborated_by_passage(
    hass: HomeAssistant, show: Any
) -> None:
    """A doubtful take waits silently; seen again at the door, the user is asked."""
    entry, _, model, passata = show
    manager = entry.runtime_data.show
    manager.async_arm("garage")
    requests = async_capture_events(hass, EVENT_SHOW_REQUEST)
    model.answers["show"] = _taken(passata, 0.6)
    result = await manager.async_handle_trigger("garage:0")
    assert result["status"] == "proposed"
    assert requests == []

    model.answers["passage"] = lambda: {
        "items": [
            {
                "product": "Passata",
                "matched_product_id": passata,
                "quantity": 1,
                "direction": "out",
                "confidence": 0.6,
            },
        ]
    }
    result = await manager.async_handle_trigger("yard:0")
    assert result["items"] == [{"product_id": passata, "status": "corroborated"}]
    [request] = requests
    assert request.data["confidence"] == combine([0.6, 0.6])
    # Still nothing written, and one request only.
    assert await _quantity(entry) == 4
    assert len(await entry.runtime_data.proposals.async_pending()) == 1


async def test_passage_supports_a_take_without_double_count(
    hass: HomeAssistant, show: Any
) -> None:
    """A passage sighting of a take already asked about adds no second request."""
    entry, _, model, passata = show
    manager = entry.runtime_data.show
    manager.async_arm("garage")
    model.answers["show"] = _taken(passata, 0.95)
    await manager.async_handle_trigger("garage:0")
    await _confirm(entry)
    model.answers["passage"] = lambda: {
        "items": [
            {
                "product": "Passata",
                "matched_product_id": passata,
                "quantity": 1,
                "direction": "out",
                "confidence": 0.7,
            },
        ]
    }
    result = await manager.async_handle_trigger("yard:0")
    assert result["items"] == [{"product_id": passata, "status": "corroborated"}]
    assert await _quantity(entry) == 3
    assert await entry.runtime_data.proposals.async_pending() == []


async def test_passage_alone(hass: HomeAssistant, show: Any) -> None:
    """Goods carried in are only seen; goods carried out are asked about."""
    entry, _, model, passata = show
    manager = entry.runtime_data.show
    model.answers["passage"] = lambda: {
        "items": [
            {
                "product": "Passata",
                "matched_product_id": passata,
                "quantity": 2,
                "direction": "in",
                "confidence": 0.99,
            },
            {
                "product": "Mystery",
                "quantity": 1,
                "direction": "out",
                "confidence": 0.9,
            },
            "bad",
        ]
    }
    result = await manager.async_handle_trigger("yard:0")
    assert result["items"] == [{"product_id": passata, "status": "seen"}]
    assert await _quantity(entry) == 4
    assert await entry.runtime_data.proposals.async_pending() == []

    # Placed on the shelf with nothing taken before: no stock change either.
    manager.async_arm("garage")
    model.answers["show"] = _taken(passata, 0.5, "returned")
    result = await manager.async_handle_trigger("garage:0")
    assert result["confidence"] == combine([0.5, 0.99])
    assert result["status"] == "needs_receipt"
    assert await _quantity(entry) == 4

    model.answers["passage"] = lambda: {
        "items": [
            {
                "product": "Passata",
                "matched_product_id": passata,
                "quantity": 2,
                "direction": "out",
                "confidence": 0.9,
            }
        ]
    }
    result = await manager.async_handle_trigger("yard:0")
    assert result["items"] == [{"product_id": passata, "status": "proposed"}]
    [proposal] = await entry.runtime_data.proposals.async_pending()
    assert (proposal.zone_id, proposal.payload["items"][0]["moved"]) == ("garage", 2)
    await _confirm(entry)
    assert await _quantity(entry) == 2

    model.answers["passage"] = lambda: {"items": []}
    assert (await manager.async_handle_trigger("yard:0"))["status"] == "nothing_carried"


async def test_nothing_shown_and_unclear(hass: HomeAssistant, show: Any) -> None:
    """No product: no action. Unclear or unknown products become proposals."""
    entry, _, model, passata = show
    manager = entry.runtime_data.show
    manager.async_arm("garage")
    model.answers["show"] = lambda: {
        "product_shown": False,
        "action": "unclear",
        "confidence": 0,
    }
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "nothing_shown"
    model.answers["show"] = lambda: {
        "product_shown": True,
        "action": "taken",
        "product": "Caviar",
        "confidence": 0.95,
    }
    result = await manager.async_handle_trigger("garage:0")
    assert (result["status"], result["action"]) == ("proposed", "unclear")
    # A camera never loads goods: an unknown sighting cannot become a product.
    proposals = entry.runtime_data.proposals
    [caviar] = await proposals.async_pending()
    with pytest.raises(InventoryError) as err:
        await proposals.async_confirm(
            caviar.id,
            corrections={0: {"new_product": True}},
            channel=Channel.SERVICE,
            user_id=None,
        )
    assert err.value.translation_key == "camera_no_new_product"
    outcome = await proposals.async_confirm(
        caviar.id, channel=Channel.SERVICE, user_id=None
    )
    assert (outcome.applied, len(outcome.waiting)) == ([], 1)
    model.answers["show"] = _taken(passata, 0.95, "unclear")
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "proposed"
    assert await _quantity(entry) == 4


async def test_unchanged_frames_suspend(hass: HomeAssistant, show: Any) -> None:
    """Three triggers without change suspend the source; arming resumes it."""
    entry, scene, model, _ = show
    manager = entry.runtime_data.show
    manager.async_arm("garage")
    scene.changed = False
    for _ in range(3):
        assert (await manager.async_handle_trigger("garage:0"))["status"] == "no_change"
    assert model.calls == []
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "suspended"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, "show_suspended_garage_0") is not None
    manager.async_arm("garage")
    assert registry.async_get_issue(DOMAIN, "show_suspended_garage_0") is None


async def test_limits(hass: HomeAssistant, show: Any) -> None:
    """Hours, cooldown and daily limits are respected; disarm works."""
    entry, _, model, passata = show
    manager = entry.runtime_data.show
    watcher = manager._watchers["garage:0"]
    manager.async_arm("garage", 5)
    watcher.source = {**watcher.source, "hours": "00:00-00:01"}
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "outside_hours"
    watcher.source = {**watcher.source, "hours": None, "cooldown_seconds": 3600}
    model.answers["show"] = _taken(passata, 0.95)
    await manager.async_handle_trigger("garage:0")
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "cooldown"
    watcher.source = {**watcher.source, "cooldown_seconds": 0, "max_per_day": 1}
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "daily_limit"
    await hass.services.async_call(
        "switch",
        "turn_off",
        target={"entity_id": "switch.quartermaster_show_mode_garage"},
        blocking=True,
    )
    assert not manager.is_armed("garage")
    with pytest.raises(InventoryError):
        manager.async_arm("yard")


async def test_single_attachment_fallback_and_trigger(
    hass: HomeAssistant, show: Any
) -> None:
    """A provider refusing several images gets the sharpest; motion triggers."""
    entry, _, model, passata = show
    model.refuse_many = True
    model.answers["show"] = _taken(passata, 0.95)
    await hass.services.async_call(
        "switch",
        "turn_on",
        target={"entity_id": "switch.quartermaster_show_mode_garage"},
        blocking=True,
    )
    hass.states.async_set("binary_sensor.garage_motion", "off")
    hass.states.async_set("binary_sensor.garage_motion", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(model.calls[-1]["attachments"]) == 1
    assert len(await entry.runtime_data.proposals.async_pending()) == 1
    assert await _quantity(entry) == 4

    # Event entities trigger on a new state. A state restored after a restart
    # or a reload (the entity appears, or comes back from unavailable, with
    # the time of an old event) is not one.
    model.answers["passage"] = lambda: {"items": []}
    calls = len(model.calls)
    hass.states.async_set("event.yard_motion", "2026-11-08T10:00:00+00:00")
    hass.states.async_set("event.yard_motion", "unavailable")
    hass.states.async_set("event.yard_motion", "2026-11-08T10:00:00+00:00")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(model.calls) == calls
    hass.states.async_set("event.yard_motion", "2026-11-08T10:05:00+00:00")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert model.calls[-1]["task_name"].startswith(f"{DOMAIN} passage")
    # A motion sensor that comes back already on is not a trigger either.
    calls = len(model.calls)
    hass.states.async_set("binary_sensor.garage_motion", "unavailable")
    hass.states.async_set("binary_sensor.garage_motion", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(model.calls) == calls


async def test_single_attachment_retry_counts(hass: HomeAssistant, show: Any) -> None:
    """The single-image retry counts as a call and stops at the limits."""
    entry, _, model, passata = show
    vision = entry.runtime_data.vision
    manager = entry.runtime_data.show
    model.refuse_many = True
    model.answers["show"] = _taken(passata, 0.95)
    await hass.services.async_call(
        "switch",
        "turn_on",
        target={"entity_id": "switch.quartermaster_show_mode_garage"},
        blocking=True,
    )
    before = await vision.async_analyses_today()
    await manager.async_handle_trigger("garage:0")
    assert await vision.async_analyses_today() == before + 2

    # With one call left this hour, the refused call is not retried.
    vision.hourly_limit = len(vision._recent) + 1
    calls = len(model.calls)
    manager._watchers["garage:0"].last_trigger = None
    manager._watchers["garage:0"].reference = None
    with pytest.raises(InventoryError) as err:
        await manager.async_handle_trigger("garage:0")
    assert err.value.translation_key == "hourly_limit"
    assert len(model.calls) == calls + 1
    assert await vision.async_analyses_today() == before + 3


async def test_tool_arms(hass: HomeAssistant, show: Any) -> None:
    """The LLM tool turns show mode on."""
    from homeassistant.components import conversation
    from homeassistant.core import Context
    from homeassistant.helpers import llm

    api = await llm.async_get_api(
        hass,
        llm.LLM_API_ASSIST,
        llm.LLMContext("t", Context(), "en", conversation.DOMAIN, None),
    )
    result = await api.async_call_tool(
        llm.ToolInput(
            tool_name=f"{DOMAIN}__arm_show_mode",
            tool_args={"zone": "garage", "minutes": 2},
        )
    )
    assert result.data["status"] == "armed"  # type: ignore[union-attr]


async def test_text_mode_list_and_announce(hass: HomeAssistant, show: Any) -> None:
    """Text mode parses JSON; an essential below minimum is flagged for the list."""
    entry, _scene, model, passata = show
    runtime = entry.runtime_data
    runtime.vision.structure_mode = StructureMode.TEXT
    await runtime.inventory.async_set_essential("passata", essential=True, min_stock=4)
    zone = await runtime.inventory.async_find_zone("garage")
    sources = [dict(zone.sources[0]) | {"announce": ["notify.garage_echo"]}]
    await hass.services.async_call(
        DOMAIN,
        "set_zone",
        {"zone": "garage", "sources": [*sources, {"type": "manual"}]},
        blocking=True,
    )
    await hass.async_block_till_done()
    events = async_capture_events(hass, EVENT_SHOW_RECOGNIZED)
    await hass.services.async_call(
        DOMAIN, "arm_show_mode", {"zone": "garage"}, blocking=True
    )
    model.answers["show"] = lambda: {"result_json": json.dumps(_taken(passata, 0.9)())}
    assert (await runtime.show.async_handle_trigger("garage:0"))["status"] == "proposed"
    assert "result_json" in model.calls[-1]["structure"]
    await _confirm(entry)
    [event] = events
    assert event.data["to_list"] is True
    assert event.data["announce"] == ["notify.garage_echo"]
    model.answers["show"] = lambda: {"result_json": "not json"}
    with pytest.raises(InventoryError):
        await runtime.show.async_handle_trigger("garage:0")
