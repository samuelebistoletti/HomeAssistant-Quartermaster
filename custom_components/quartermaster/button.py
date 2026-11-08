"""Buttons: go shopping, undo the last operation."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.coordinator import QuartermasterCoordinator
from custom_components.quartermaster.entity import QuartermasterEntity
from custom_components.quartermaster.storage.models import Channel

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QuartermasterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the buttons."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities([StartShoppingButton(coordinator), UndoLastButton(coordinator)])


class StartShoppingButton(QuartermasterEntity, ButtonEntity):
    """Recompute the list and turn shopping mode on."""

    def __init__(self, coordinator: QuartermasterCoordinator) -> None:
        """Set up the button."""
        super().__init__(coordinator, "start_shopping")

    async def async_press(self) -> None:
        """Start shopping."""
        await self.coordinator.async_start_shopping()


class UndoLastButton(QuartermasterEntity, ButtonEntity):
    """Undo the most recent operation."""

    def __init__(self, coordinator: QuartermasterCoordinator) -> None:
        """Set up the button."""
        super().__init__(coordinator, "undo_last")

    async def async_press(self) -> None:
        """Undo."""
        await self.coordinator.inventory.async_undo_last(channel=Channel.DASHBOARD)
