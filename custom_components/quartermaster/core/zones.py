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
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
import probatio

from custom_components.quartermaster.const import (
    CONF_CONFIDENCE_THRESHOLD,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DOMAIN,
    SETTING_UPDATE_MODE,
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
REQUEST_REMINDER_AFTER = timedelta(hours=12)
REQUEST_LIFETIME = timedelta(hours=24)
ZONE_ERROR_FAILURES = 3


class SourceType(StrEnum):
    """Kinds of zone sources."""

    CAMERA = "camera"
    CAMERA_SHOW = "camera_show"
    PASSAGE = "passage"
    MANUAL = "manual"


class CheckStatus(StrEnum):
    """How a zone check ended."""

    UPDATED = "updated"
    PHOTO_REQUESTED = "photo_requested"
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
        }
    ),
    SourceType.CAMERA_SHOW: probatio.Schema(
        {
            probatio.Required("type"): SourceType.CAMERA_SHOW.value,
            **_WATCH,
            probatio.Optional("active", default="when_armed"): probatio.In(
                ["always", "when_armed"]
            ),
            probatio.Optional("confirm", default="auto"): probatio.In(["auto", "ask"]),
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
}


def validate_sources(sources: list[Any]) -> list[dict[str, Any]]:
    """Validate a list of sources; raise InventoryError on the first problem."""
    validated: list[dict[str, Any]] = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise InventoryError(
                "invalid_source", index=str(index + 1), error="not an object"
            )
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
        self, zone_ref: str, *, channel: Channel, user_id: str | None = None
    ) -> CheckOutcome:
        """Run the check algorithm for a zone."""
        runtime = self._runtime
        zone = await runtime.inventory.async_find_zone(zone_ref)
        if zone.id in self._running:
            return CheckOutcome(zone.id, CheckStatus.FAILED, reason="already_running")
        self._running.add(zone.id)
        try:
            reason: str | None = None
            sources = zone.sources or [{"type": SourceType.MANUAL.value}]
            for index, source in enumerate(sources):
                kind = source.get("type")
                if kind == SourceType.CAMERA:
                    if not await self._async_source_quota(
                        zone.id, index, int(source.get("max_checks_per_day", 4))
                    ):
                        reason = "daily_limit"
                        continue
                    try:
                        frame = await async_camera_frame(
                            self._hass, source["entity_id"]
                        )
                        outcome = await self._async_analyze(
                            zone, frame, source, channel, user_id
                        )
                    except InventoryError as err:
                        reason = _reason(err)
                        continue
                    if outcome is None:
                        reason = "low_confidence"
                        continue
                    await self._async_record(outcome)
                    return outcome
                if kind == SourceType.MANUAL:
                    request = await self.async_request_photo(
                        zone, reason or "check", channel=channel
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

    async def _async_analyze(
        self,
        zone: Zone,
        frame: bytes,
        source: dict[str, Any],
        channel: Channel,
        user_id: str | None,
    ) -> CheckOutcome | None:
        """Analyze a camera frame; None when the result is insufficient."""
        runtime = self._runtime
        roi = tuple(source["roi"]) if source.get("roi") else None
        run = await runtime.vision.async_analyze(frame, zone, roi=roi)
        analysis = run.analysis
        threshold = self.threshold(zone)
        essentials = {
            normalize(p.name)
            for p in await runtime.inventory.async_list_products()
            if p.essential
        }
        missing_essential = any(
            normalize(name) in essentials for name in analysis.expected_not_seen
        )
        if (
            not analysis.image_usable
            or analysis.overall_confidence < threshold
            or missing_essential
        ):
            return None
        mode = await self._backend.async_get_setting(SETTING_UPDATE_MODE)
        outcome = await runtime.proposals.async_create(
            zone,
            analysis,
            channel=channel,
            user_id=user_id,
            automatic=mode == "automatic",
            threshold=threshold,
        )
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
        self, zone: Zone, reason: str, *, channel: Channel
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
        self._announce(request, zone, reminder=False)
        return request

    @callback
    def _announce(self, request: PhotoRequest, zone: Zone, *, reminder: bool) -> None:
        self._hass.bus.async_fire(
            EVENT_PHOTO_REQUEST,
            {
                "request_id": request.id,
                "zone_id": zone.id,
                "zone": zone.name,
                "reason": request.reason,
                "channel": request.channel.value,
                "reminder": reminder,
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
        """Stop the timers."""
        while self._unsubs:
            self._unsubs.pop()()

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
        for zone in zones.values():
            has_camera = any(s.get("type") == SourceType.CAMERA for s in zone.sources)
            if has_camera or not zone.reminder_days or zone.id in waiting:
                continue
            last = updates.get(zone.id)
            if last is None or now - last >= timedelta(days=zone.reminder_days):
                await self.async_request_photo(
                    zone, "reminder", channel=Channel.SCHEDULER
                )
        await self._runtime.coordinator.async_request_refresh()


def _reason(err: InventoryError) -> str:
    if err.translation_key == "image_rejected":
        return (err.translation_placeholders or {}).get("issue", "image_rejected")
    return err.translation_key or "error"
