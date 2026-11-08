"""Base entity: one service device per config entry."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
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
