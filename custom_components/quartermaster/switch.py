"""Switches: show mode (camera_show) per zone; it turns itself off."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.coordinator import QuartermasterCoordinator
from custom_components.quartermaster.entity import (
    QuartermasterZoneEntity,
    async_add_zone_entities,
    has_show,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QuartermasterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a show mode switch per zone with camera_show."""
    coordinator = entry.runtime_data.coordinator
    async_add_zone_entities(
        coordinator,
        async_add_entities,
        lambda zone_id: (
            [ShowModeSwitch(coordinator, zone_id)]
            if has_show(coordinator, zone_id)
            else []
        ),
    )


class ShowModeSwitch(QuartermasterZoneEntity, SwitchEntity):
    """Arms camera_show for the zone for a while."""

    def __init__(self, coordinator: QuartermasterCoordinator, zone_id: str) -> None:
        """Set up the switch."""
        super().__init__(coordinator, "show_mode", zone_id)

    @property
    def available(self) -> bool:
        """Available while the zone has a camera_show source."""
        return super().available and has_show(self.coordinator, self.zone_id)

    @property
    def is_on(self) -> bool:
        """Return whether show mode is on."""
        return self.coordinator.config_entry.runtime_data.show.is_armed(self.zone_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when show mode turns off."""
        show = self.coordinator.config_entry.runtime_data.show
        until: datetime | None = show.armed_until.get(self.zone_id)
        return {
            "until": until.isoformat() if until and self.is_on else None,
            "suspended": any(
                key.startswith(f"{self.zone_id}:") for key in show.suspended
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Arm."""
        self.coordinator.config_entry.runtime_data.show.async_arm(self.zone_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disarm."""
        self.coordinator.config_entry.runtime_data.show.async_disarm(self.zone_id)
