"""Sensors: essentials to buy, purchase suggestions, expiring products."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.coordinator import (
    QuartermasterCoordinator,
    QuartermasterData,
)
from custom_components.quartermaster.entity import (
    QuartermasterEntity,
    QuartermasterZoneEntity,
    async_add_zone_entities,
    has_show,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class QuartermasterSensorDescription(SensorEntityDescription):
    """A list-valued sensor: its state is the number of entries."""

    entries: Callable[[QuartermasterData], list[dict[str, Any]]]


def _to_buy(data: QuartermasterData) -> list[dict[str, Any]]:
    return [
        {
            "product_id": entry.product.id,
            "product": entry.product.name,
            "have": entry.total,
            "min_stock": entry.product.min_stock,
            "quantity": entry.quantity,
            "unit": entry.product.unit,
        }
        for entry in data.analysis.to_buy
    ]


def _suggestions(data: QuartermasterData) -> list[dict[str, Any]]:
    return [
        {
            "product_id": entry.product.id,
            "product": entry.product.name,
            "have": entry.total,
            "unit": entry.product.unit,
            "reason": entry.reason.value,
            **entry.details,
        }
        for entry in data.analysis.suggestions
    ]


def _expiring(data: QuartermasterData) -> list[dict[str, Any]]:
    return [
        {
            "product_id": entry.product.id,
            "product": entry.product.name,
            "zone": entry.zone.name,
            "quantity": entry.quantity,
            "unit": entry.product.unit,
            "best_before": entry.best_before.isoformat(),
            "days_left": entry.days_left,
        }
        for entry in data.analysis.expiring
    ]


def _proposals(data: QuartermasterData) -> list[dict[str, Any]]:
    return [
        {
            "proposal_id": proposal.id,
            "zone": data.zones[proposal.zone_id].name
            if proposal.zone_id in data.zones
            else None,
            "channel": proposal.channel.value,
            "created": proposal.created.isoformat(),
            "items": proposal.payload["items"],
        }
        for proposal in data.proposals
    ]


def _photo_requests(data: QuartermasterData) -> list[dict[str, Any]]:
    return [
        {
            "request_id": request.id,
            "zone_id": request.zone_id,
            "zone": data.zones[request.zone_id].name
            if request.zone_id in data.zones
            else None,
            "reason": request.reason,
            "created": request.created.isoformat(),
        }
        for request in data.photo_requests
    ]


SENSORS: tuple[QuartermasterSensorDescription, ...] = (
    QuartermasterSensorDescription(key="to_buy", entries=_to_buy),
    QuartermasterSensorDescription(key="suggestions", entries=_suggestions),
    QuartermasterSensorDescription(key="expiring", entries=_expiring),
    QuartermasterSensorDescription(key="pending_proposals", entries=_proposals),
    QuartermasterSensorDescription(key="photo_requests", entries=_photo_requests),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QuartermasterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            *(QuartermasterListSensor(coordinator, d) for d in SENSORS),
            AnalysesTodaySensor(coordinator),
        ]
    )
    async_add_zone_entities(
        coordinator,
        async_add_entities,
        lambda zone_id: [
            LastCheckSensor(coordinator, zone_id),
            *(
                [LastShowSensor(coordinator, zone_id)]
                if has_show(coordinator, zone_id)
                else []
            ),
        ],
    )


class QuartermasterListSensor(QuartermasterEntity, SensorEntity):
    """Number of entries, with the entries as the `items` attribute."""

    entity_description: QuartermasterSensorDescription
    # The lists are large and change often: kept out of the recorder.
    _unrecorded_attributes = frozenset({"items"})

    def __init__(
        self,
        coordinator: QuartermasterCoordinator,
        description: QuartermasterSensorDescription,
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int:
        """Return the number of entries."""
        return len(self.entity_description.entries(self.coordinator.data))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the entries."""
        return {"items": self.entity_description.entries(self.coordinator.data)}


class AnalysesTodaySensor(QuartermasterEntity, SensorEntity):
    """Model calls made today, against the daily limit."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: QuartermasterCoordinator) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "analyses_today")

    @property
    def native_value(self) -> int:
        """Return today's count."""
        return self.coordinator.data.analyses_today

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the limit."""
        vision = self.coordinator.vision
        return {"limit": vision.daily_limit if vision else None}


class LastCheckSensor(QuartermasterZoneEntity, SensorEntity):
    """When a zone was last checked, with the outcome."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: QuartermasterCoordinator, zone_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "last_check", zone_id)

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the last check."""
        last = self.coordinator.data.zone_checks.get(self.zone_id)
        return dt_util.parse_datetime(last["ts"]) if last else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the outcome."""
        last = self.coordinator.data.zone_checks.get(self.zone_id) or {}
        updated = self.coordinator.data.zone_updates.get(self.zone_id)
        return {
            "status": last.get("status"),
            "reason": last.get("reason"),
            "overall_confidence": last.get("overall_confidence"),
            "zone_updated": updated.isoformat() if updated else None,
        }


class LastShowSensor(QuartermasterZoneEntity, SensorEntity):
    """The last product recognized by camera_show in the zone."""

    def __init__(self, coordinator: QuartermasterCoordinator, zone_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "last_show", zone_id)

    @property
    def native_value(self) -> str | None:
        """Return the product name."""
        show = self.coordinator.config_entry.runtime_data.show
        last = show.last_show.get(self.zone_id)
        return last["product"] if last else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return action, quantity, confidence, outcome and time."""
        show = self.coordinator.config_entry.runtime_data.show
        return dict(show.last_show.get(self.zone_id) or {})
