"""Tests for zones: cron, sources, the check algorithm, schedules and requests."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.cron import Cron, CronError, is_valid
from custom_components.quartermaster.core.inventory import InventoryError
from custom_components.quartermaster.core.zones import (
    EVENT_PHOTO_REQUEST,
    EVENT_ZONE_CHECKED,
    validate_sources,
)
from custom_components.quartermaster.storage.models import Channel

from .test_vision import FakeAITask, _photo

# Cron


@pytest.mark.parametrize(
    ("expression", "when", "expected"),
    [
        ("0 19 * * *", datetime(2026, 11, 8, 19, 0), True),
        ("0 19 * * *", datetime(2026, 11, 8, 19, 1), False),
        ("*/15 8-10 * * *", datetime(2026, 11, 8, 9, 45), True),
        ("0 9 * * 1-5", datetime(2026, 11, 8, 9, 0), False),  # Sunday
        ("0 9 * * 0", datetime(2026, 11, 8, 9, 0), True),
        ("0 9 * * 7", datetime(2026, 11, 8, 9, 0), True),
        ("0 9 1 * 1", datetime(2026, 11, 9, 9, 0), True),  # day OR weekday
        ("0 9 1,15 11 *", datetime(2026, 11, 15, 9, 0), True),
        ("30 6 * 12 *", datetime(2026, 11, 8, 6, 30), False),
    ],
)
def test_cron_matches(expression: str, when: datetime, expected: bool) -> None:
    """Five-field cron with ranges, lists, steps and either day field."""
    assert Cron.parse(expression).matches(when) is expected


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "0 19 * *",
        "61 * * * *",
        "* 24 * * *",
        "a * * * *",
        "*/0 * * * *",
        "5-1 * * * *",
        "1-x * * * *",
    ],
)
def test_cron_invalid(expression: str) -> None:
    """Malformed expressions are refused."""
    assert not is_valid(expression)
    with pytest.raises(CronError):
        Cron.parse(expression)


# Sources


def test_validate_sources() -> None:
    """Sources get their defaults; bad ones name the source and the problem."""
    sources = validate_sources(
        [
            {"type": "camera", "entity_id": "camera.garage", "roi": [0.6, 0, 1, 1]},
            {
                "type": "camera_show",
                "entity_id": "camera.garage",
                "trigger_entity": "event.garage_motion",
                "hours": "07:00-23:00",
            },
            {
                "type": "passage",
                "entity_id": "camera.yard",
                "trigger_entity": "binary_sensor.yard",
            },
            {"type": "manual"},
        ]
    )
    assert sources[0]["max_checks_per_day"] == 4
    assert (
        sources[1]["active"],
        sources[1]["confirm"],
        sources[1]["min_confidence"],
    ) == (
        "when_armed",
        "auto",
        0.8,
    )
    assert sources[2]["match_window_seconds"] == 300
    for bad in (
        [{"type": "drone"}],
        ["camera"],
        [{"type": "camera", "entity_id": "sensor.x"}],
        [{"type": "camera", "entity_id": "camera.x", "roi": [0.5, 0, 0.4, 1]}],
        [{"type": "camera", "entity_id": "camera.x", "roi": [0, 0, 2, 1]}],
        [{"type": "camera_show", "entity_id": "camera.x", "trigger_entity": "light.x"}],
        [
            {
                "type": "camera_show",
                "entity_id": "camera.x",
                "trigger_entity": "event.x",
                "hours": "7-23",
            }
        ],
    ):
        with pytest.raises(InventoryError) as err:
            validate_sources(bad)
        assert err.value.translation_key == "invalid_source"


# Checks


class FakeCamera:
    """camera.snapshot writing a photo, or failing."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.fail = False
        self.seed = 1
        hass.services.async_register("camera", "snapshot", self._snapshot)

    async def _snapshot(self, call: ServiceCall) -> None:
        if self.fail:
            raise ServiceValidationError("camera offline")
        Path(call.data["filename"]).write_bytes(_photo(seed=self.seed))


@pytest.fixture
async def zoned(
    hass: HomeAssistant, init_integration: MockConfigEntry, tmp_path: Path
) -> tuple[MockConfigEntry, FakeAITask, FakeCamera]:
    """A garage with a camera then manual, and a pantry with manual only."""
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    ai = FakeAITask(hass)
    camera = FakeCamera(hass)
    hass.config_entries.async_update_entry(
        init_integration, options={"ai_task_image": "ai_task.vision"}
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    inventory = init_integration.runtime_data.inventory
    await inventory.async_add_zone("Garage")
    await inventory.async_add_zone("Pantry")
    passata = await inventory.async_add_product("Passata", default_zone="garage")
    await inventory.async_set_stock("passata", 2, channel=Channel.SERVICE)
    await hass.services.async_call(
        DOMAIN,
        "set_zone",
        {
            "zone": "garage",
            "sources": [
                {
                    "type": "camera",
                    "entity_id": "camera.garage",
                    "max_checks_per_day": 2,
                },
                {"type": "manual"},
            ],
            "check_schedule": "0 19 * * *",
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "set_zone", {"zone": "pantry", "reminder_days": 3}, blocking=True
    )
    ai.answer = {
        "image_usable": True,
        "overall_confidence": 0.9,
        "items": [
            {
                "product": "Passata",
                "matched_product_id": passata.id,
                "quantity": 3,
                "confidence": 0.9,
            }
        ],
    }
    await hass.async_block_till_done()
    return init_integration, ai, camera


async def _check(hass: HomeAssistant, zone: str) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN, "check_zone", {"zone": zone}, blocking=True, return_response=True
    )
    assert response is not None
    return dict(response)


async def test_set_zone(hass: HomeAssistant, zoned: Any) -> None:
    """Zones change through the action; bad schedules and sources are refused."""
    response = await hass.services.async_call(
        DOMAIN,
        "set_zone",
        {
            "zone": "pantry",
            "name": "Big pantry",
            "aliases": ["dispensa"],
            "confidence_threshold": 0.6,
        },
        blocking=True,
        return_response=True,
    )
    assert (response["name"], response["confidence_threshold"]) == ("Big pantry", 0.6)  # type: ignore[index]
    await hass.services.async_call(
        DOMAIN,
        "set_zone",
        {"zone": "dispensa", "check_schedule": "", "reminder_days": 0},
        blocking=True,
    )
    for data, key in (
        ({"check_schedule": "every day"}, "invalid_schedule"),
        ({"sources": [{"type": "drone"}]}, "invalid_source"),
        ({"name": "Garage"}, "zone_exists"),
        ({"name": "  "}, "invalid_name"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN, "set_zone", {"zone": "pantry", **data}, blocking=True
            )
        assert err.value.translation_key == key


async def test_camera_check_updates_zone(hass: HomeAssistant, zoned: Any) -> None:
    """A good frame is analyzed and proposed; the zone counts as updated."""
    _entry, ai, _ = zoned
    outcome = await _check(hass, "garage")
    assert (outcome["status"], outcome["source"]) == ("updated", "camera")
    assert outcome["proposal_id"] is not None
    assert ai.calls[0]["entity_id"] == "ai_task.vision"
    state = hass.states.get("sensor.quartermaster_last_check_garage")
    assert state is not None
    assert state.attributes["status"] == "updated"
    assert (
        hass.states.get("binary_sensor.quartermaster_zone_error_garage").state == "off"
    )  # type: ignore[union-attr]


async def test_low_confidence_falls_back_to_photo_request(
    hass: HomeAssistant, zoned: Any
) -> None:
    """An insufficient result moves on to the manual source."""
    _, ai, _ = zoned
    events = async_capture_events(hass, EVENT_PHOTO_REQUEST)
    ai.answer["overall_confidence"] = 0.3
    outcome = await _check(hass, "garage")
    assert (outcome["status"], outcome["reason"]) == (
        "photo_requested",
        "low_confidence",
    )
    [event] = events
    assert (event.data["zone_id"], event.data["reason"], event.data["reminder"]) == (
        "garage",
        "low_confidence",
        False,
    )
    # One open request per zone.
    again = await _check(hass, "garage")
    assert again["request_id"] == outcome["request_id"]
    assert len(events) == 1
    assert hass.states.get("sensor.quartermaster_photo_requests").state == "1"  # type: ignore[union-attr]


async def test_camera_failures_set_zone_error(hass: HomeAssistant, zoned: Any) -> None:
    """Repeated camera failures turn the zone error on; a success clears it."""
    entry, _, camera = zoned
    # Failed reads count against the daily limit too (battery cameras).
    await entry.runtime_data.inventory.async_update_zone(
        "garage",
        sources=validate_sources(
            [{"type": "camera", "entity_id": "camera.garage", "max_checks_per_day": 5}]
        ),
    )
    camera.fail = True
    for _ in range(3):
        outcome = await _check(hass, "garage")
        assert outcome["reason"] == "camera_unavailable"
    await entry.runtime_data.coordinator.async_refresh()
    assert (
        hass.states.get("binary_sensor.quartermaster_zone_error_garage").state == "on"
    )  # type: ignore[union-attr]


async def test_daily_limit_per_source(hass: HomeAssistant, zoned: Any) -> None:
    """After max_checks_per_day the camera is skipped."""
    _, _ai, camera = zoned
    for seed in (1, 2):
        camera.seed = seed
        assert (await _check(hass, "garage"))["status"] == "updated"
    outcome = await _check(hass, "garage")
    assert (outcome["status"], outcome["reason"]) == ("photo_requested", "daily_limit")


async def test_photo_fulfils_request(
    hass: HomeAssistant, zoned: Any, tmp_path: Path
) -> None:
    """A successful photo of the zone closes its open request."""
    entry, _, _ = zoned
    await _check(hass, "pantry")
    assert len(await entry.runtime_data.zones.async_open_requests()) == 1
    photo = tmp_path / "photos" / "pantry.jpg"
    photo.parent.mkdir()
    photo.write_bytes(_photo(seed=4))
    hass.config.allowlist_external_dirs = {str(photo.parent)}
    await hass.services.async_call(
        DOMAIN, "analyze_image", {"zone": "pantry", "file": str(photo)}, blocking=True
    )
    assert await entry.runtime_data.zones.async_open_requests() == []


async def test_zone_without_sources_and_running_twice(
    hass: HomeAssistant, zoned: Any
) -> None:
    """A zone without sources asks for a photo; a running check is not doubled."""
    entry, _, _ = zoned
    zones = entry.runtime_data.zones
    zones._running.add("garage")
    outcome = await zones.async_check_zone("garage", channel=Channel.SERVICE)
    assert outcome.reason == "already_running"
    zones._running.clear()
    await entry.runtime_data.inventory.async_update_zone("garage", sources=[])
    outcome = await zones.async_check_zone("garage", channel=Channel.SERVICE)
    assert outcome.status.value == "photo_requested"


async def test_camera_only_zone_failure(hass: HomeAssistant, zoned: Any) -> None:
    """With no manual source left a failed check is reported as failed."""
    entry, _, camera = zoned
    await entry.runtime_data.inventory.async_update_zone(
        "garage",
        sources=validate_sources([{"type": "camera", "entity_id": "camera.garage"}]),
    )
    camera.fail = True
    outcome = await _check(hass, "garage")
    assert (outcome["status"], outcome["reason"]) == ("failed", "camera_unavailable")


async def test_schedule_runs_checks(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, zoned: Any
) -> None:
    """At the scheduled minute the zone is checked by the scheduler."""
    entry, ai, _ = zoned
    # Tomorrow at 19:00: timers set up at the real time only fire forward.
    tomorrow = dt_util.now().date() + timedelta(days=1)
    local = dt_util.get_default_time_zone()
    freezer.move_to(
        datetime.combine(tomorrow, datetime.min.time(), local).replace(hour=19)
    )
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(ai.calls) == 1
    journal_channel = (await entry.runtime_data.zones.async_last_check("garage"))[
        "status"
    ]  # type: ignore[index]
    assert journal_channel == "updated"


async def test_housekeeping(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, zoned: Any
) -> None:
    """Stale manual zones get a request; one reminder after 12 h; expiry at 24 h."""
    entry, _, _ = zoned
    zones = entry.runtime_data.zones
    inventory = entry.runtime_data.inventory
    await inventory.async_add_product("Rice", default_zone="pantry")
    await inventory.async_set_stock("rice", 1, channel=Channel.SERVICE)
    await entry.runtime_data.coordinator.async_refresh()
    events = async_capture_events(hass, EVENT_PHOTO_REQUEST)
    await zones.async_housekeeping()
    assert events == []  # updated just now; the garage has a camera

    freezer.tick(timedelta(days=4))
    await entry.runtime_data.coordinator.async_refresh()
    await zones.async_housekeeping()
    assert [(e.data["zone_id"], e.data["reason"]) for e in events] == [
        ("pantry", "reminder")
    ]

    freezer.tick(timedelta(hours=13))
    await zones.async_housekeeping()
    assert events[-1].data["reminder"] is True
    await zones.async_housekeeping()
    assert len(events) == 2

    freezer.tick(timedelta(hours=12))
    await zones.async_housekeeping()
    # The old request expired; the next one waits for reminder_days again.
    assert await zones.async_open_requests() == []
    assert len(events) == 2

    freezer.tick(timedelta(days=3))
    await zones.async_housekeeping()
    [request] = await zones.async_open_requests()
    assert request.created >= dt_util.utcnow() - timedelta(minutes=1)
    assert len(events) == 3


async def test_zone_entities_follow_zones(hass: HomeAssistant, zoned: Any) -> None:
    """Entities appear for new zones and leave the registry with deleted ones."""
    entry, _, _ = zoned
    registry = er.async_get(hass)
    await hass.services.async_call(
        DOMAIN, "add_zone", {"name": "Freezer"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("button.quartermaster_check_freezer") is not None
    await hass.services.async_call(
        DOMAIN, "remove_zone", {"zone": "freezer"}, blocking=True
    )
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert registry.async_get("button.quartermaster_check_freezer") is None


async def test_check_zone_button_and_tool(hass: HomeAssistant, zoned: Any) -> None:
    """The button and the LLM tool start a check in the background."""
    from homeassistant.components import conversation
    from homeassistant.core import Context
    from homeassistant.helpers import llm

    _, ai, _ = zoned
    await hass.services.async_call(
        "button",
        "press",
        target={"entity_id": "button.quartermaster_check_garage"},
        blocking=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(ai.calls) == 1
    api = await llm.async_get_api(
        hass,
        llm.LLM_API_ASSIST,
        llm.LLMContext("t", Context(), "en", conversation.DOMAIN, None),
    )
    result = await api.async_call_tool(
        llm.ToolInput(tool_name=f"{DOMAIN}__check_zone", tool_args={"zone": "garage"})
    )
    assert result.data == {"status": "started", "zone": "Garage"}  # type: ignore[union-attr]


async def test_same_picture_keeps_one_proposal(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, zoned: Any
) -> None:
    """An unchanged scene refreshes the zone's date and adds no proposal."""
    entry, ai, _ = zoned
    first = await _check(hass, "garage")
    freezer.tick(timedelta(hours=2))
    events = async_capture_events(hass, EVENT_ZONE_CHECKED)
    second = await _check(hass, "garage")
    assert len(ai.calls) == 1
    assert second["reused_previous"] is True
    assert second["proposal_id"] == first["proposal_id"]
    assert len(await entry.runtime_data.proposals.async_pending()) == 1
    assert events[-1].data["unchanged"] is True
    await entry.runtime_data.coordinator.async_refresh()
    updated = entry.runtime_data.coordinator.data.zone_updates["garage"]
    assert dt_util.utcnow() - updated < timedelta(minutes=1)


async def test_new_count_replaces_the_waiting_one(
    hass: HomeAssistant, zoned: Any
) -> None:
    """A different picture gives a new proposal and closes the old one."""
    entry, ai, camera = zoned
    first = await _check(hass, "garage")
    camera.seed = 2
    ai.answer["items"][0]["quantity"] = 5
    second = await _check(hass, "garage")
    assert second["proposal_id"] != first["proposal_id"]
    [pending] = await entry.runtime_data.proposals.async_pending()
    assert pending.id == second["proposal_id"]


async def test_essential_at_zero_is_not_missing(
    hass: HomeAssistant, zoned: Any
) -> None:
    """An essential already finished and not seen does not ask for a photo."""
    entry, ai, _ = zoned
    inventory = entry.runtime_data.inventory
    await inventory.async_add_product(
        "Oil", default_zone="garage", essential=True, min_stock=1
    )
    ai.answer["expected_not_seen"] = ["Oil"]
    outcome = await _check(hass, "garage")
    assert outcome["status"] == "updated"
    assert await entry.runtime_data.zones.async_open_requests() == []


async def test_product_seen_twice_is_summed(hass: HomeAssistant, zoned: Any) -> None:
    """Two rows for one product become one count, with the lower confidence."""
    entry, ai, _ = zoned
    ai.answer["items"].append(
        {**ai.answer["items"][0], "quantity": 2, "confidence": 0.7}
    )
    outcome = await _check(hass, "garage")
    [pending] = await entry.runtime_data.proposals.async_pending()
    assert pending.id == outcome["proposal_id"]
    [row] = pending.payload["items"]
    assert (row["current"], row["proposed"], row["confidence"]) == (2, 5, 0.7)
