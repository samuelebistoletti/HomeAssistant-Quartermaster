"""Base entity: one service device per config entry."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.coordinator import QuartermasterCoordinator


class QuartermasterEntity(CoordinatorEntity[QuartermasterCoordinator]):
    """Entity bound to the coordinator and the Quartermaster device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: QuartermasterCoordinator, key: str) -> None:
        """Set the unique id, translation key and device."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )


class QuartermasterZoneEntity(QuartermasterEntity):
    """Entity bound to one zone; unavailable once the zone is gone."""

    def __init__(
        self, coordinator: QuartermasterCoordinator, key: str, zone_id: str
    ) -> None:
        """Set the per-zone unique id and the zone name placeholder."""
        super().__init__(coordinator, key)
        self.zone_id = zone_id
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}_{zone_id}"
        self._attr_translation_placeholders = {
            "zone": coordinator.data.zones[zone_id].name
        }

    @property
    def available(self) -> bool:
        """Available while the zone exists."""
        return super().available and self.zone_id in self.coordinator.data.zones


@callback
def async_add_zone_entities(
    coordinator: QuartermasterCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[str], Iterable[Entity]],
) -> None:
    """Add entities for every zone now and for zones created later."""
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [zone_id for zone_id in coordinator.data.zones if zone_id not in known]
        known.intersection_update(coordinator.data.zones)
        if not new:
            return
        known.update(new)
        async_add_entities(entity for zone_id in new for entity in factory(zone_id))

    add_new()
    coordinator.config_entry.async_on_unload(coordinator.async_add_listener(add_new))
