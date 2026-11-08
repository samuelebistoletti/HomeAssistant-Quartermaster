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
from custom_components.quartermaster.storage.models import Channel
from custom_components.quartermaster.vision.quality import Frame
from custom_components.quartermaster.vision.schema import StructureMode
from custom_components.quartermaster.vision.show import (
    EVENT_SHOW_RECOGNIZED,
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


async def test_show_taken_and_returned(hass: HomeAssistant, show: Any) -> None:
    """Armed: a product taken is recorded; shown back it is undone."""
    entry, _scene, model, passata = show
    manager = entry.runtime_data.show
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "not_armed"

    events = async_capture_events(hass, EVENT_SHOW_RECOGNIZED)
    await hass.services.async_call(
        DOMAIN, "arm_show_mode", {"zone": "garage"}, blocking=True
    )
    assert hass.states.get("switch.quartermaster_show_mode_garage").state == "on"  # type: ignore[union-attr]
    model.answers["show"] = _taken(passata, 0.9)
    result = await manager.async_handle_trigger("garage:0")
    assert result["status"] == "applied"
    assert await _quantity(entry) == 3
    assert len(model.calls[0]["attachments"]) >= 1
    [event] = events
    assert (event.data["product"], event.data["action"]) == ("Passata", "taken")
    journal = await entry.runtime_data.inventory.async_list_journal(limit=1)
    assert journal[0].channel is Channel.CAMERA
    await entry.runtime_data.coordinator.async_refresh()
    assert hass.states.get("sensor.quartermaster_last_shown_garage").state == "Passata"  # type: ignore[union-attr]

    model.answers["show"] = _taken(passata, 0.9, "returned")
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "applied"
    assert await _quantity(entry) == 4
    # Returned with nothing to undo adds the quantity.
    assert (await manager.async_handle_trigger("garage:0"))["status"] == "applied"
    assert await _quantity(entry) == 5
    assert not any((Path(hass.config.media_dirs["local"]) / DOMAIN / "tmp").iterdir())


async def test_low_confidence_corroborated_by_passage(
    hass: HomeAssistant, show: Any
) -> None:
    """A held take becomes a consumption when the yard camera sees it go out."""
    entry, _, model, passata = show
    manager = entry.runtime_data.show
    manager.async_arm("garage")
    model.answers["show"] = _taken(passata, 0.6)
    result = await manager.async_handle_trigger("garage:0")
    assert result["status"] == "proposed"
    assert await _quantity(entry) == 4

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
    assert result["items"] == [{"product_id": passata, "status": "confirmed"}]
    assert await _quantity(entry) == 3
    assert await entry.runtime_data.proposals.async_pending() == []


async def test_passage_confirms_applied_take_without_double_count(
    hass: HomeAssistant, show: Any
) -> None:
    """A passage sighting of a recorded take only corroborates it."""
    entry, _, model, passata = show
    manager = entry.runtime_data.show
    manager.async_arm("garage")
    model.answers["show"] = _taken(passata, 0.95)
    await manager.async_handle_trigger("garage:0")
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


async def test_passage_alone_only_proposes(hass: HomeAssistant, show: Any) -> None:
    """Carrying out or in without a zone sighting gives proposals, no writes."""
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
    assert result["items"] == [{"product_id": passata, "status": "proposed"}]
    assert await _quantity(entry) == 4
    [proposal] = await entry.runtime_data.proposals.async_pending()
    assert proposal.payload["items"][0]["proposed"] == 6

    # A later zone sighting of the same product merges with it.
    manager.async_arm("garage")
    model.answers["show"] = _taken(passata, 0.5, "returned")
    result = await manager.async_handle_trigger("garage:0")
    assert result["confidence"] == combine([0.5, 0.99])
    assert result["status"] == "applied"
    assert await _quantity(entry) == 5
    assert await entry.runtime_data.proposals.async_pending() == []

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
    assert await _quantity(entry) == 3

    # Event entities trigger on a new state; unavailable is ignored.
    model.answers["passage"] = lambda: {"items": []}
    hass.states.async_set("event.yard_motion", "unavailable")
    hass.states.async_set("event.yard_motion", "2026-11-08T10:00:00")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert model.calls[-1]["task_name"].startswith(f"{DOMAIN} passage")


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
    assert (await runtime.show.async_handle_trigger("garage:0"))["status"] == "applied"
    assert "result_json" in model.calls[-1]["structure"]
    [event] = events
    assert event.data["to_list"] is True
    assert event.data["announce"] == ["notify.garage_echo"]
    model.answers["show"] = lambda: {"result_json": "not json"}
    with pytest.raises(InventoryError):
        await runtime.show.async_handle_trigger("garage:0")
