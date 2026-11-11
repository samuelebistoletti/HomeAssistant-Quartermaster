"""Zone manager: sources, the zone check algorithm, schedules and reminders.

Spec §5: every check (scheduled, by voice, from Telegram, the dashboard, a
button or an action) walks the zone's sources in priority order. A camera
source reads a frame, checks it, skips unchanged images and asks the model;
an insufficient result moves on to the next source. The manual source asks
the user for a photo, with one reminder after 12 hours, then the request
expires. Zones without a camera get a reminder every `reminder_days`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util
import probatio

from custom_components.quartermaster.const import (
    CONF_CONFIDENCE_THRESHOLD,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DOMAIN,
)
from custom_components.quartermaster.core.cron import Cron, CronError
from custom_components.quartermaster.core.inventory import InventoryError, normalize
from custom_components.quartermaster.storage.base import StorageBackend
from custom_components.quartermaster.storage.models import (
    Channel,
    PhotoRequest,
    PhotoRequestStatus,
    Zone,
)
from custom_components.quartermaster.vision.analyze import async_camera_frame

if TYPE_CHECKING:
    from custom_components.quartermaster import QuartermasterRuntimeData

_LOGGER = logging.getLogger(__name__)

EVENT_PHOTO_REQUEST = f"{DOMAIN}_photo_request"
EVENT_ZONE_CHECKED = f"{DOMAIN}_zone_checked"
# The list of a ledger zone to go through with the user.
EVENT_LEDGER_CHECK = f"{DOMAIN}_ledger_check"
LEDGER_LIST_MAX = 30
REQUEST_REMINDER_AFTER = timedelta(hours=12)
REQUEST_LIFETIME = timedelta(hours=24)
ZONE_ERROR_FAILURES = 3


class SourceType(StrEnum):
    """Kinds of zone sources."""

    CAMERA = "camera"
    CAMERA_SHOW = "camera_show"
    PASSAGE = "passage"
    MANUAL = "manual"
    # List check: for any place a photo shows badly (a closed cupboard, a
    # fridge, a freezer). A check sends the zone's list to go through with
    # the user instead of asking for a photo.
    LEDGER = "ledger"


class CheckStatus(StrEnum):
    """How a zone check ended."""

    UPDATED = "updated"
    PHOTO_REQUESTED = "photo_requested"
    LIST_SENT = "list_sent"
    FAILED = "failed"


def _roi(value: Any) -> list[float]:
    roi = [float(v) for v in cv.ensure_list(value)]
    if len(roi) != 4 or not all(0 <= v <= 1 for v in roi):
        raise probatio.Invalid("roi must be four fractions x1, y1, x2, y2")
    if roi[0] >= roi[2] or roi[1] >= roi[3]:
        raise probatio.Invalid("roi must have x1 < x2 and y1 < y2")
    return roi


def _hours(value: Any) -> str:
    text = str(value)
    parts = text.split("-")
    if len(parts) != 2 or any(dt_util.parse_time(p.strip()) is None for p in parts):
        raise probatio.Invalid("hours must be HH:MM-HH:MM")
    return text


_TRIGGER = probatio.All(cv.entity_id, probatio.Match(r"^(event|binary_sensor)\."))
_CAMERA = cv.entity_domain("camera")
_WATCH = {
    probatio.Required("entity_id"): _CAMERA,
    probatio.Required("trigger_entity"): _TRIGGER,
    probatio.Optional("roi"): _roi,
    probatio.Optional("capture", default="auto"): probatio.In(
        ["auto", "burst", "clip"]
    ),
    probatio.Optional("window_seconds", default=6): probatio.All(
        probatio.Coerce(float), probatio.Range(min=1, max=30)
    ),
    probatio.Optional("burst_interval_seconds", default=0.5): probatio.All(
        probatio.Coerce(float), probatio.Range(min=0.2, max=5)
    ),
    probatio.Optional("max_frames_to_model", default=4): probatio.All(
        int, probatio.Range(min=1, max=8)
    ),
    probatio.Optional("cooldown_seconds", default=30): probatio.All(
        int, probatio.Range(min=0, max=3600)
    ),
    probatio.Optional("max_per_day", default=40): probatio.All(
        int, probatio.Range(min=1, max=500)
    ),
    probatio.Optional("min_confidence", default=0.8): probatio.All(
        probatio.Coerce(float), probatio.Range(min=0, max=1)
    ),
    probatio.Optional("hours"): _hours,
    probatio.Optional("announce"): probatio.All(
        cv.ensure_list, [cv.entity_domain("notify")]
    ),
}

SOURCE_SCHEMAS: dict[SourceType, probatio.Schema] = {
    SourceType.CAMERA: probatio.Schema(
        {
            probatio.Required("type"): SourceType.CAMERA.value,
            probatio.Required("entity_id"): _CAMERA,
            probatio.Optional("roi"): _roi,
            probatio.Optional("max_age_minutes", default=30): probatio.All(
                int, probatio.Range(min=1, max=1440)
            ),
            probatio.Optional("max_checks_per_day", default=4): probatio.All(
                int, probatio.Range(min=1, max=96)
            ),
            # Interior camera with a door sensor (spec §15).
            probatio.Optional("trigger_entity"): probatio.All(
                cv.entity_id, probatio.Match(r"^binary_sensor\.")
            ),
            probatio.Optional("trigger_state", default="off"): probatio.In(
                ["on", "off"]
            ),
            probatio.Optional("trigger_delay_seconds", default=3): probatio.All(
                probatio.Coerce(float), probatio.Range(min=0, max=120)
            ),
            probatio.Optional("min_interval_minutes", default=30): probatio.All(
                int, probatio.Range(min=1, max=1440)
            ),
        }
    ),
    SourceType.CAMERA_SHOW: probatio.Schema(
        {
            probatio.Required("type"): SourceType.CAMERA_SHOW.value,
            **_WATCH,
            probatio.Optional("active", default="when_armed"): probatio.In(
                ["always", "when_armed"]
            ),
            probatio.Optional("arm_minutes", default=10): probatio.All(
                int, probatio.Range(min=1, max=240)
            ),
        }
    ),
    SourceType.PASSAGE: probatio.Schema(
        {
            probatio.Required("type"): SourceType.PASSAGE.value,
            **_WATCH,
            probatio.Optional("match_window_seconds", default=300): probatio.All(
                int, probatio.Range(min=10, max=3600)
            ),
        }
    ),
    SourceType.MANUAL: probatio.Schema({probatio.Required("type"): "manual"}),
    SourceType.LEDGER: probatio.Schema(
        {
            probatio.Required("type"): SourceType.LEDGER.value,
            # Days between two guided checks; 0 sends them only when asked.
            probatio.Optional("check_days", default=30): probatio.All(
                int, probatio.Range(min=0, max=365)
            ),
        }
    ),
}


def ledger_source(zone: Zone) -> dict[str, Any] | None:
    """Return the list-check source of a zone, if it has one."""
    for source in zone.sources:
        if source.get("type") == SourceType.LEDGER:
            return source
    return None


def validate_sources(sources: list[Any]) -> list[dict[str, Any]]:
    """Validate a list of sources; raise InventoryError on the first problem."""
    validated: list[dict[str, Any]] = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise InventoryError(
                "invalid_source", index=str(index + 1), error="not an object"
            )
        # Dropped option (cameras always ask now); stored sources may have it.
        source = {key: value for key, value in source.items() if key != "confirm"}
        try:
            kind = SourceType(str(source.get("type")))
            validated.append(dict(SOURCE_SCHEMAS[kind](source)))
        except (ValueError, probatio.Invalid) as err:
            raise InventoryError(
                "invalid_source", index=str(index + 1), error=str(err)
            ) from err
    return validated


@dataclass(slots=True)
class CheckOutcome:
    """Result of one zone check."""

    zone_id: str
    status: CheckStatus
    reason: str | None = None
    source: str | None = None
    overall_confidence: float | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the outcome for responses and storage."""
        return {
            "zone_id": self.zone_id,
            "status": self.status.value,
            "reason": self.reason,
            "source": self.source,
            "overall_confidence": self.overall_confidence,
            **self.result,
        }


class ZoneManager:
    """Runs zone checks, schedules and photo requests."""

    def __init__(self, hass: HomeAssistant, runtime: QuartermasterRuntimeData) -> None:
        """Keep references; nothing runs until `async_start`."""
        self._hass = hass
        self._runtime = runtime
        self._unsubs: list[CALLBACK_TYPE] = []
        self._running: set[str] = set()
        self._housekeeping_lock = asyncio.Lock()
        self._door_unsubs: list[CALLBACK_TYPE] = []
        self._door_checks: dict[tuple[str, int], float] = {}

    @property
    def _backend(self) -> StorageBackend:
        return self._runtime.inventory.backend

    # Status kept in the settings table

    async def async_last_check(self, zone_id: str) -> dict[str, Any] | None:
        """Return the last check outcome of a zone."""
        raw = await self._backend.async_get_setting(f"last_check:{zone_id}")
        return json.loads(raw) if raw else None

    async def async_failures(self, zone_id: str) -> int:
        """Return the consecutive failed checks of a zone."""
        return int(await self._backend.async_get_setting(f"failures:{zone_id}") or 0)

    async def _async_record(self, outcome: CheckOutcome) -> None:
        data = outcome.as_dict() | {"ts": dt_util.utcnow().isoformat()}
        await self._backend.async_set_setting(
            f"last_check:{outcome.zone_id}", json.dumps(data)
        )
        failures = 0
        if outcome.status is CheckStatus.FAILED or outcome.reason in {
            "camera_unavailable",
            "analysis_failed",
        }:
            failures = await self.async_failures(outcome.zone_id) + 1
        await self._backend.async_set_setting(
            f"failures:{outcome.zone_id}", str(failures)
        )
        self._hass.bus.async_fire(EVENT_ZONE_CHECKED, outcome.as_dict())

    async def _async_source_quota(self, zone_id: str, index: int, limit: int) -> bool:
        """Count a camera read against the source's daily limit."""
        today = dt_util.now().date().isoformat()
        key = f"source_reads:{zone_id}:{index}"
        raw = await self._backend.async_get_setting(key)
        day, count = raw.split("|") if raw else (today, "0")
        used = int(count) if day == today else 0
        if used >= limit:
            return False
        await self._backend.async_set_setting(key, f"{today}|{used + 1}")
        return True

    # The check

    async def async_check_zone(
        self,
        zone_ref: str,
        *,
        channel: Channel,
        user_id: str | None = None,
        chat_id: int | None = None,
    ) -> CheckOutcome:
        """Run the check algorithm for a zone.

        `chat_id` is the Telegram chat that asked for the check: a photo
        request goes back to it.
        """
        runtime = self._runtime
        zone = await runtime.inventory.async_find_zone(zone_ref)
        if zone.id in self._running:
            return CheckOutcome(zone.id, CheckStatus.FAILED, reason="already_running")
        self._running.add(zone.id)
        try:
            reason: str | None = None
            missing: list[str] = []
            sources = zone.sources or [{"type": SourceType.MANUAL.value}]
            for index, source in enumerate(sources):
                kind = source.get("type")
                if kind == SourceType.CAMERA:
                    outcome = await self._async_camera_source(
                        zone, index, source, channel, user_id
                    )
                    if isinstance(outcome, tuple):
                        reason, missing = outcome
                        continue
                    await self._async_record(outcome)
                    return outcome
                if kind == SourceType.LEDGER:
                    outcome = await self._async_ledger_check(zone, chat_id)
                    await self._async_record(outcome)
                    return outcome
                if kind == SourceType.MANUAL:
                    request = await self.async_request_photo(
                        zone,
                        reason or "check",
                        channel=channel,
                        products=missing,
                        chat_id=chat_id,
                    )
                    outcome = CheckOutcome(
                        zone.id,
                        CheckStatus.PHOTO_REQUESTED,
                        reason=reason,
                        source=kind,
                        result={"request_id": request.id},
                    )
                    await self._async_record(outcome)
                    return outcome
            outcome = CheckOutcome(zone.id, CheckStatus.FAILED, reason=reason)
            await self._async_record(outcome)
            return outcome
        finally:
            self._running.discard(zone.id)
            await runtime.coordinator.async_request_refresh()

    async def _async_ledger_check(
        self, zone: Zone, chat_id: int | None
    ) -> CheckOutcome:
        """Send the zone's list, oldest first, to be gone through by the user."""
        inventory = self._runtime.inventory
        products = {p.id: p for p in await inventory.async_list_products()}
        levels = [
            level
            for level in await inventory.async_get_stock(zone_id=zone.id)
            if level.quantity > 0 and level.product_id in products
        ]
        far = dt_util.utcnow() + timedelta(days=36500)
        levels.sort(
            key=lambda level: (
                level.best_before or far.date(),
                level.updated_at or far,
                products[level.product_id].name,
            )
        )
        items = [
            {
                "product_id": level.product_id,
                "product": products[level.product_id].name,
                "quantity": level.quantity,
                "unit": products[level.product_id].unit,
                "best_before": level.best_before.isoformat()
                if level.best_before
                else None,
                "since": level.updated_at.date().isoformat()
                if level.updated_at
                else None,
            }
            for level in levels[:LEDGER_LIST_MAX]
        ]
        await self._backend.async_set_setting(
            f"ledger_check:{zone.id}", dt_util.utcnow().isoformat()
        )
        self._hass.bus.async_fire(
            EVENT_LEDGER_CHECK,
            {
                "zone_id": zone.id,
                "zone": zone.name,
                "items": items,
                "more": len(levels) - len(items),
                "chat_id": chat_id,
            },
        )
        return CheckOutcome(
            zone.id,
            CheckStatus.LIST_SENT,
            source=SourceType.LEDGER.value,
            result={"items": items, "more": len(levels) - len(items)},
        )

    async def _async_camera_source(
        self,
        zone: Zone,
        index: int,
        source: dict[str, Any],
        channel: Channel,
        user_id: str | None,
    ) -> CheckOutcome | tuple[str, list[str]]:
        """Read and analyze one camera source within its daily limit."""
        if not await self._async_source_quota(
            zone.id, index, int(source.get("max_checks_per_day", 4))
        ):
            return "daily_limit", []
        try:
            frame = await async_camera_frame(self._hass, source["entity_id"])
            return await self._async_analyze(zone, frame, source, channel, user_id)
        except InventoryError as err:
            return _reason(err), []

    # Interior cameras with a door sensor (spec §15)

    @callback
    def async_watch_doors(self, zones: list[Zone]) -> None:
        """Check a zone's camera when its door sensor reaches the set state."""
        while self._door_unsubs:
            self._door_unsubs.pop()()
        for zone in zones:
            for index, source in enumerate(zone.sources):
                if source.get("type") != SourceType.CAMERA or not source.get(
                    "trigger_entity"
                ):
                    continue
                self._door_unsubs.append(
                    async_track_state_change_event(
                        self._hass,
                        [source["trigger_entity"]],
                        self._door_listener(zone.id, index, source),
                    )
                )

    def _door_listener(
        self, zone_id: str, index: int, source: dict[str, Any]
    ) -> Callable[[Event[EventStateChangedData]], None]:
        wanted = source.get("trigger_state", "off")

        @callback
        def changed(event: Event[EventStateChangedData]) -> None:
            new, old = event.data["new_state"], event.data["old_state"]
            if new is None or new.state != wanted:
                return
            if old is None or old.state in (wanted, STATE_UNAVAILABLE, STATE_UNKNOWN):
                return
            key = (zone_id, index)
            if (last := self._door_checks.get(key)) is not None and (
                time.monotonic() - last
                < 60 * float(source.get("min_interval_minutes", 30))
            ):
                return
            self._door_checks[key] = time.monotonic()

            async def run() -> None:
                await asyncio.sleep(float(source.get("trigger_delay_seconds", 3)))
                await self.async_check_door(zone_id, index)

            self._background(run(), f"door check {zone_id}")

        return changed

    async def async_check_door(self, zone_id: str, index: int) -> CheckOutcome | None:
        """Check a zone with one camera after its door closed.

        Only that camera is used: no photo request and no failure count, as
        a frame taken after a door event is often just unclear.
        """
        zones = {z.id: z for z in await self._runtime.inventory.async_list_zones()}
        zone = zones.get(zone_id)
        if zone is None or index >= len(zone.sources) or zone.id in self._running:
            return None
        self._running.add(zone.id)
        try:
            outcome = await self._async_camera_source(
                zone, index, zone.sources[index], Channel.CAMERA, None
            )
            if isinstance(outcome, tuple):
                _LOGGER.debug("Door check of %s not used: %s", zone.id, outcome[0])
                return None
            await self._async_record(outcome)
            return outcome
        finally:
            self._running.discard(zone.id)
            await self._runtime.coordinator.async_request_refresh()

    async def _async_analyze(
        self,
        zone: Zone,
        frame: bytes,
        source: dict[str, Any],
        channel: Channel,
        user_id: str | None,
    ) -> CheckOutcome | tuple[str, list[str]]:
        """Analyze a camera frame.

        When the result is insufficient, return the reason and the expected
        products that were not seen (what the photo should show).
        """
        runtime = self._runtime
        roi = tuple(source["roi"]) if source.get("roi") else None
        run = await runtime.vision.async_analyze(frame, zone, roi=roi)
        analysis = run.analysis
        threshold = self.threshold(zone)
        # An essential already at zero is not missing from the picture: it is
        # expected not to be there.
        in_stock = {
            level.product_id
            for level in await runtime.inventory.async_get_stock(zone_id=zone.id)
            if level.quantity > 0
        }
        essentials = {
            normalize(p.name)
            for p in await runtime.inventory.async_list_products()
            if p.essential and p.id in in_stock
        }
        missing_essentials = [
            name for name in analysis.expected_not_seen if normalize(name) in essentials
        ]
        if not analysis.image_usable or analysis.overall_confidence < threshold:
            return "low_confidence", missing_essentials
        if missing_essentials:
            return "not_recognized", missing_essentials
        if run.reused:
            # Same picture as last time: its proposal, if still waiting, is
            # the answer; a second identical one would only be noise.
            for older in await runtime.proposals.async_pending():
                if older.zone_id == zone.id and "not_seen" in older.payload:
                    await self.async_fulfil(zone.id)
                    return CheckOutcome(
                        zone.id,
                        CheckStatus.UPDATED,
                        source=SourceType.CAMERA.value,
                        overall_confidence=analysis.overall_confidence,
                        result={
                            "reused_previous": True,
                            "applied": 0,
                            "proposal_id": older.id,
                            "unchanged": True,
                        },
                    )
        # A camera never writes and never loads goods: its count is always a
        # proposal, of what went down only (the update mode is for photos a
        # person sends).
        outcome = await runtime.proposals.async_create(
            zone,
            analysis,
            channel=channel,
            user_id=user_id,
            automatic=False,
            threshold=threshold,
            allow_increase=False,
        )
        await runtime.vision.async_keep_proposal_crop(outcome.proposal, run.image)
        await self.async_fulfil(zone.id)
        return CheckOutcome(
            zone.id,
            CheckStatus.UPDATED,
            source=SourceType.CAMERA.value,
            overall_confidence=analysis.overall_confidence,
            result={
                "reused_previous": run.reused,
                "applied": len(outcome.applied),
                "proposal_id": outcome.proposal.id if outcome.proposal else None,
                "more_than_stock": outcome.more_than_stock,
            },
        )

    def threshold(self, zone: Zone) -> float:
        """Return the zone's confidence threshold."""
        options = self._runtime.coordinator.config_entry.options
        return zone.confidence_threshold or float(
            options.get(CONF_CONFIDENCE_THRESHOLD, DEFAULT_CONFIDENCE_THRESHOLD)
        )

    # Photo requests

    async def async_request_photo(
        self,
        zone: Zone,
        reason: str,
        *,
        channel: Channel,
        products: list[str] | None = None,
        chat_id: int | None = None,
    ) -> PhotoRequest:
        """Open a photo request (one per zone at a time) and announce it."""
        for request in await self._backend.async_list_photo_requests(
            status=PhotoRequestStatus.OPEN
        ):
            if request.zone_id == zone.id:
                return request
        now = dt_util.utcnow()
        request = await self._backend.async_add_photo_request(
            PhotoRequest(
                id=0,
                zone_id=zone.id,
                reason=reason,
                channel=channel,
                created=now,
                expires_at=now + REQUEST_LIFETIME,
            )
        )
        self._announce(
            request, zone, reminder=False, products=products, chat_id=chat_id
        )
        return request

    @callback
    def _announce(
        self,
        request: PhotoRequest,
        zone: Zone,
        *,
        reminder: bool,
        products: list[str] | None = None,
        chat_id: int | None = None,
    ) -> None:
        self._hass.bus.async_fire(
            EVENT_PHOTO_REQUEST,
            {
                "request_id": request.id,
                "zone_id": zone.id,
                "zone": zone.name,
                "reason": request.reason,
                "channel": request.channel.value,
                "reminder": reminder,
                "products": products or [],
                "chat_id": chat_id,
            },
        )

    async def async_fulfil(self, zone_id: str) -> None:
        """Close the open requests of a zone after a successful analysis."""
        for request in await self._backend.async_list_photo_requests(
            status=PhotoRequestStatus.OPEN
        ):
            if request.zone_id == zone_id:
                request.status = PhotoRequestStatus.FULFILLED
                await self._backend.async_update_photo_request(request)

    async def async_open_requests(self) -> list[PhotoRequest]:
        """Return open requests."""
        return await self._backend.async_list_photo_requests(
            status=PhotoRequestStatus.OPEN
        )

    # Scheduler

    @callback
    def async_start(self) -> None:
        """Run schedules every minute and housekeeping every hour."""
        self._unsubs.append(
            async_track_time_change(self._hass, self._async_minute, second=0)
        )
        self._unsubs.append(
            async_track_time_change(self._hass, self._async_hourly, minute=7, second=0)
        )

    @callback
    def async_stop(self) -> None:
        """Stop the timers and the door sensors."""
        while self._unsubs:
            self._unsubs.pop()()
        while self._door_unsubs:
            self._door_unsubs.pop()()

    @callback
    def _async_minute(self, now: datetime) -> None:
        self._background(self._async_run_schedules(now), "schedules")

    @callback
    def _async_hourly(self, now: datetime) -> None:
        self._background(self.async_housekeeping(), "housekeeping")

    @callback
    def _background(self, job: Coroutine[Any, Any, Any], name: str) -> None:
        """Run a job as a task of the entry, cancelled when it unloads."""
        entry = self._runtime.coordinator.config_entry
        entry.async_create_background_task(self._hass, job, f"{DOMAIN} {name}")

    async def _async_run_schedules(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        for zone in await self._runtime.inventory.async_list_zones():
            if not zone.check_schedule:
                continue
            try:
                due = Cron.parse(zone.check_schedule).matches(local)
            except CronError:
                continue
            if due:
                self._background(
                    self.async_check_zone(zone.id, channel=Channel.SCHEDULER),
                    f"scheduled check {zone.id}",
                )

    async def async_housekeeping(self) -> None:
        """Remind, expire requests and ask for photos of stale manual zones."""
        async with self._housekeeping_lock:
            await self._async_housekeeping()

    async def _async_housekeeping(self) -> None:
        now = dt_util.utcnow()
        zones = {z.id: z for z in await self._runtime.inventory.async_list_zones()}
        open_requests = await self.async_open_requests()
        for request in open_requests:
            if request.expires_at <= now:
                request.status = PhotoRequestStatus.EXPIRED
                await self._backend.async_update_photo_request(request)
            elif (
                request.reminded_at is None
                and now - request.created >= REQUEST_REMINDER_AFTER
            ):
                request.reminded_at = now
                await self._backend.async_update_photo_request(request)
                if request.zone_id in zones:
                    self._announce(request, zones[request.zone_id], reminder=True)
        waiting = {r.zone_id for r in open_requests if r.expires_at > now}
        updates = self._runtime.coordinator.data.zone_updates
        reminded: dict[str, datetime] = {}
        for request in await self._backend.async_list_photo_requests():
            if request.reason == "reminder" and (
                request.zone_id not in reminded
                or request.created > reminded[request.zone_id]
            ):
                reminded[request.zone_id] = request.created
        for zone in zones.values():
            has_camera = any(s.get("type") == SourceType.CAMERA for s in zone.sources)
            if (ledger := ledger_source(zone)) is not None:
                # With a camera the list is only the fallback of its checks.
                if not has_camera:
                    await self._async_ledger_due(
                        zone, int(ledger.get("check_days", 30))
                    )
                continue
            if has_camera or not zone.reminder_days or zone.id in waiting:
                continue
            # The zone's last update or, if later, the last reminder: an
            # unanswered request is repeated after reminder_days, not daily.
            last = max(
                (
                    when
                    for when in (updates.get(zone.id), reminded.get(zone.id))
                    if when is not None
                ),
                default=None,
            )
            if last is None or now - last >= timedelta(days=zone.reminder_days):
                await self.async_request_photo(
                    zone, "reminder", channel=Channel.SCHEDULER
                )
        if (crops := self._runtime.vision.crops) is not None:
            pending = await self._runtime.proposals.async_pending()
            await crops.async_prune("proposal", {str(p.id) for p in pending})
            await crops.async_prune("zone", set(zones))
        await self._runtime.coordinator.async_request_refresh()

    async def _async_ledger_due(self, zone: Zone, check_days: int) -> None:
        """Send the list check of a zone when its interval is over."""
        if check_days <= 0 or zone.id in self._running:
            return
        key = f"ledger_check:{zone.id}"
        now = dt_util.utcnow()
        last = dt_util.parse_datetime(await self._backend.async_get_setting(key) or "")
        if last is None:
            # First time: the interval starts now.
            await self._backend.async_set_setting(key, now.isoformat())
            return
        if now - last >= timedelta(days=check_days):
            await self._async_record(await self._async_ledger_check(zone, None))


def _reason(err: InventoryError) -> str:
    if err.translation_key == "image_rejected":
        return (err.translation_placeholders or {}).get("issue", "image_rejected")
    return err.translation_key or "error"
