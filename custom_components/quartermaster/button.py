"""Buttons: go shopping, undo the last operation."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.coordinator import QuartermasterCoordinator
from custom_components.quartermaster.entity import (
    QuartermasterEntity,
    QuartermasterZoneEntity,
    async_add_zone_entities,
)
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
    async_add_zone_entities(
        coordinator,
        async_add_entities,
        lambda zone_id: [CheckZoneButton(coordinator, zone_id)],
    )


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


class CheckZoneButton(QuartermasterZoneEntity, ButtonEntity):
    """Check a zone now."""

    def __init__(self, coordinator: QuartermasterCoordinator, zone_id: str) -> None:
        """Set up the button."""
        super().__init__(coordinator, "check_zone", zone_id)

    async def async_press(self) -> None:
        """Run the zone check in the background."""
        zones = self.coordinator.config_entry.runtime_data.zones
        self.coordinator.config_entry.async_create_background_task(
            self.hass,
            zones.async_check_zone(self.zone_id, channel=Channel.DASHBOARD),
            f"check zone {self.zone_id}",
        )
