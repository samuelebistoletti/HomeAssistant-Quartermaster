"""Apply what the guided setup asked for when the entry is first set up.

The config flow cannot write to the database, which opens only with the
entry. It keeps zones, essentials and the todo import in `entry.data`; the
first setup creates them and then clears them, so a reload does not repeat
them.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.quartermaster.const import (
    DATA_INITIAL_ESSENTIALS,
    DATA_INITIAL_IMPORT,
    DATA_INITIAL_ZONES,
)
from custom_components.quartermaster.core.inventory import (
    InventoryError,
    InventoryService,
)
from custom_components.quartermaster.core.shopping import ImportTarget, ShoppingService
from custom_components.quartermaster.core.zones import SourceType

_LOGGER = logging.getLogger(__name__)

INITIAL_KEYS = (DATA_INITIAL_ZONES, DATA_INITIAL_ESSENTIALS, DATA_INITIAL_IMPORT)


def zone_sources(
    camera: str | None, roi: list[float] | None, check: str = "photo"
) -> list[dict[str, Any]]:
    """Return the sources of a zone from the setup.

    The camera first, if any; then how the zone is checked without it: a
    photo request (`photo`) or the zone's list to go through (`list`), for
    places a photo shows badly (a closed cupboard, a fridge, a freezer).
    """
    fallback = (
        {"type": SourceType.LEDGER.value}
        if check == "list"
        else {"type": SourceType.MANUAL.value}
    )
    if not camera:
        return [fallback]
    source: dict[str, Any] = {"type": SourceType.CAMERA.value, "entity_id": camera}
    if roi:
        source["roi"] = roi
    return [source, fallback]


async def async_apply_initial_setup(
    hass: HomeAssistant,
    entry: ConfigEntry,
    inventory: InventoryService,
    shopping: ShoppingService,
) -> None:
    """Create the zones and essentials chosen in the setup, then forget them.

    A failing item is logged and skipped: the entry still loads, and the
    item can be added later from the dashboard or with an action.
    """
    if not any(key in entry.data for key in INITIAL_KEYS):
        return
    for zone in entry.data.get(DATA_INITIAL_ZONES, []):
        try:
            created = await inventory.async_add_zone(zone["name"])
            await inventory.async_update_zone(
                created.id,
                sources=zone_sources(
                    zone.get("camera"), zone.get("roi"), zone.get("check", "photo")
                ),
            )
        except InventoryError as err:
            _LOGGER.warning("Initial zone %s not created: %s", zone["name"], err)
    for name in entry.data.get(DATA_INITIAL_ESSENTIALS, []):
        try:
            try:
                await inventory.async_set_essential(name, essential=True, min_stock=1)
            except InventoryError:
                await inventory.async_add_product(name, essential=True, min_stock=1)
        except InventoryError as err:
            _LOGGER.warning("Initial essential %s not created: %s", name, err)
    if todo := entry.data.get(DATA_INITIAL_IMPORT):
        try:
            await shopping.async_import_from_todo(
                todo["entity_id"],
                ImportTarget(todo["target"]),
                min_stock=1,
                apply=True,
            )
        except InventoryError as err:
            _LOGGER.warning("Import from %s failed: %s", todo["entity_id"], err)
    hass.config_entries.async_update_entry(
        entry,
        data={
            key: value for key, value in entry.data.items() if key not in INITIAL_KEYS
        },
    )
