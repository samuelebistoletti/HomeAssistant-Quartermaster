"""Select: update mode for changes recognized from photos and cameras."""

from __future__ import annotations

from enum import StrEnum

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.const import SETTING_UPDATE_MODE
from custom_components.quartermaster.coordinator import QuartermasterCoordinator
from custom_components.quartermaster.entity import QuartermasterEntity

PARALLEL_UPDATES = 1


class UpdateMode(StrEnum):
    """Confirm every recognized change, or apply those above the threshold."""

    CONFIRM = "confirm"
    AUTOMATIC = "automatic"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QuartermasterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the select."""
    coordinator = entry.runtime_data.coordinator
    stored = await coordinator.inventory.backend.async_get_setting(SETTING_UPDATE_MODE)
    mode = UpdateMode(stored) if stored in set(UpdateMode) else UpdateMode.CONFIRM
    async_add_entities([UpdateModeSelect(coordinator, mode)])


class UpdateModeSelect(QuartermasterEntity, SelectEntity):
    """Update mode, stored in the database."""

    _attr_options = [mode.value for mode in UpdateMode]

    def __init__(self, coordinator: QuartermasterCoordinator, mode: UpdateMode) -> None:
        """Set up the select."""
        super().__init__(coordinator, "update_mode")
        self._attr_current_option = mode.value

    async def async_select_option(self, option: str) -> None:
        """Store the new mode."""
        await self.coordinator.inventory.backend.async_set_setting(
            SETTING_UPDATE_MODE, UpdateMode(option).value
        )
        self._attr_current_option = option
        self.async_write_ha_state()
