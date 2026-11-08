"""Sensors: essentials to buy, purchase suggestions, expiring products."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.coordinator import (
    QuartermasterCoordinator,
    QuartermasterData,
)
from custom_components.quartermaster.entity import QuartermasterEntity

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


SENSORS: tuple[QuartermasterSensorDescription, ...] = (
    QuartermasterSensorDescription(key="to_buy", entries=_to_buy),
    QuartermasterSensorDescription(key="suggestions", entries=_suggestions),
    QuartermasterSensorDescription(key="expiring", entries=_expiring),
    QuartermasterSensorDescription(key="pending_proposals", entries=_proposals),
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


class QuartermasterListSensor(QuartermasterEntity, SensorEntity):
    """Number of entries, with the entries as the `items` attribute."""

    entity_description: QuartermasterSensorDescription

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
