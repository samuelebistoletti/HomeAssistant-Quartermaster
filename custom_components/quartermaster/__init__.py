"""Quartermaster: household inventory and shopping list agent for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN

PLATFORMS: list[Platform] = []

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class QuartermasterRuntimeData:
    """Runtime objects shared by every platform of the config entry."""


type QuartermasterConfigEntry = ConfigEntry[QuartermasterRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: QuartermasterConfigEntry
) -> None:
    """Set up Quartermaster from a config entry."""
    entry.runtime_data = QuartermasterRuntimeData()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(
    hass: HomeAssistant, entry: QuartermasterConfigEntry
) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
