"""Binary sensors: a zone whose checks keep failing."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.coordinator import QuartermasterCoordinator
from custom_components.quartermaster.core.zones import ZONE_ERROR_FAILURES
from custom_components.quartermaster.entity import (
    QuartermasterZoneEntity,
    async_add_zone_entities,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QuartermasterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one problem sensor per zone."""
    coordinator = entry.runtime_data.coordinator
    async_add_zone_entities(
        coordinator,
        async_add_entities,
        lambda zone_id: [ZoneErrorBinarySensor(coordinator, zone_id)],
    )


class ZoneErrorBinarySensor(QuartermasterZoneEntity, BinarySensorEntity):
    """On after repeated failures (camera unavailable, analysis failed)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: QuartermasterCoordinator, zone_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "zone_error", zone_id)

    @property
    def is_on(self) -> bool:
        """Return whether the zone is in error."""
        failures = self.coordinator.data.zone_failures.get(self.zone_id, 0)
        return failures >= ZONE_ERROR_FAILURES
