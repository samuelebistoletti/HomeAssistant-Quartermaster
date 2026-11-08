"""Quartermaster: household inventory and shopping list agent for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_EXTERNAL_TODO, DATABASE_FILE, DOMAIN
from .coordinator import QuartermasterCoordinator
from .core.inventory import InventoryService
from .core.shopping import ShoppingService
from .services import async_setup_services
from .storage.sqlite import SchemaTooNewError, SQLiteBackend

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.TODO,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class QuartermasterRuntimeData:
    """Runtime objects shared by every platform of the config entry."""

    inventory: InventoryService
    shopping: ShoppingService
    coordinator: QuartermasterCoordinator


type QuartermasterConfigEntry = ConfigEntry[QuartermasterRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: QuartermasterConfigEntry
) -> None:
    """Set up Quartermaster from a config entry."""
    backend = SQLiteBackend(hass, Path(hass.config.path(DOMAIN, DATABASE_FILE)))
    try:
        await backend.async_open()
    except SchemaTooNewError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="database_too_new",
            translation_placeholders={"found": str(err.found)},
        ) from err
    except (OSError, sqlite3.Error) as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="database_error",
            translation_placeholders={"error": str(err)},
        ) from err
    entry.async_on_unload(backend.async_close)
    inventory = InventoryService(backend)
    shopping = ShoppingService(hass, backend, inventory)
    shopping.external_entity_id = entry.options.get(CONF_EXTERNAL_TODO)
    coordinator = QuartermasterCoordinator(hass, entry, inventory, shopping)
    await coordinator.async_config_entry_first_refresh()
    entry.async_on_unload(
        inventory.async_add_listener(coordinator.async_inventory_changed)
    )
    entry.runtime_data = QuartermasterRuntimeData(
        inventory=inventory, shopping=shopping, coordinator=coordinator
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(
    hass: HomeAssistant, entry: QuartermasterConfigEntry
) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
