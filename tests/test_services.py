"""Tests for the Quartermaster actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.auth.models import User
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.quartermaster.const import DOMAIN

pytestmark = pytest.mark.usefixtures("init_integration")


async def _call(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    service: str,
    *,
    context: Context | None = None,
    **data: Any,
) -> Any:
    return await hass.services.async_call(
        DOMAIN,
        service,
        {"config_entry_id": entry.entry_id, **data},
        blocking=True,
        return_response=True,
        context=context,
    )


async def _kitchen(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    inventory = entry.runtime_data.inventory
    await inventory.async_add_zone("Fridge", aliases=["frigo"])
    await inventory.async_add_zone("Pantry")


async def test_product_and_stock_actions(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_admin_user: User
) -> None:
    """Products are created, stocked, consumed and undone through actions."""
    entry = init_integration
    await _kitchen(hass, entry)
    context = Context(user_id=hass_admin_user.id)

    product = await _call(
        hass,
        entry,
        "add_product",
        name="Milk",
        aliases="latte",
        default_zone="frigo",
        unit="bottle",
        essential=True,
        min_stock=1,
    )
    assert product["aliases"] == ["latte"]
    assert product["default_zone_id"] == "fridge"
    assert product["essential"] is True

    bought = await _call(
        hass,
        entry,
        "adjust_stock",
        product="latte",
        operation="purchase",
        quantity="3",
        best_before="2026-11-20",
        context=context,
    )
    assert bought | {"journal_id": 0} == {
        "journal_id": 0,
        "kind": "purchase",
        "product_id": product["product_id"],
        "product": "Milk",
        "zone_id": "fridge",
        "zone": "Fridge",
        "delta": 3,
        "quantity": 3,
        "unit": "bottle",
    }

    consumed = await _call(hass, entry, "adjust_stock", product="milk")
    assert (consumed["kind"], consumed["quantity"]) == ("consume", 2)

    counted = await _call(hass, entry, "set_stock", product="milk", quantity=5)
    assert (counted["kind"], counted["delta"]) == ("inventory", 3)

    undone = await _call(hass, entry, "undo_last", context=context)
    assert [m["journal_id"] for m in undone["undone"]] == [counted["journal_id"]]
    assert [m["quantity"] for m in undone["undo"]] == [2]

    journal = await entry.runtime_data.inventory.async_list_journal()
    assert journal[0].user_id == hass_admin_user.id
    assert journal[-1].user_id == hass_admin_user.id

    essential = await _call(
        hass, entry, "set_essential", product="milk", min_stock=2, target_stock=6
    )
    assert (essential["min_stock"], essential["target_stock"]) == (2, 6)
    removed = await _call(hass, entry, "set_essential", product="milk", essential=False)
    assert removed["essential"] is False


async def test_actions_without_response(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Actions also run without asking for a response."""
    await _kitchen(hass, init_integration)
    for service, data in (
        ("add_product", {"name": "Salt", "default_zone": "pantry"}),
        ("set_stock", {"product": "salt", "quantity": 1}),
        ("adjust_stock", {"product": "salt"}),
        ("undo_last", {}),
        ("add_zone", {"name": "Cellar"}),
        ("remove_zone", {"zone": "cellar"}),
        ("set_essential", {"product": "salt", "min_stock": 1}),
        ("export_data", {}),
    ):
        assert (
            await hass.services.async_call(
                DOMAIN,
                service,
                {"config_entry_id": init_integration.entry_id, **data},
                blocking=True,
            )
            is None
        )


async def test_action_errors(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Inventory errors reach the caller as translated validation errors."""
    await _kitchen(hass, init_integration)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, init_integration, "adjust_stock", product="bread")
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "product_not_found"
    assert str(err.value) == "No product called bread"

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, init_integration, "undo_last")
    assert err.value.translation_key == "nothing_to_undo"


async def test_unknown_config_entry(hass: HomeAssistant) -> None:
    """An entry ID that does not exist is refused by Home Assistant."""
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "undo_last",
            {"config_entry_id": "missing"},
            blocking=True,
        )
    assert err.value.translation_key == "service_config_entry_not_found"


async def test_export_and_import(
    hass: HomeAssistant, init_integration: MockConfigEntry, config_dir: Path
) -> None:
    """Exports land in quartermaster/exports and import back to the same state."""
    entry = init_integration
    inventory = entry.runtime_data.inventory
    await _kitchen(hass, entry)
    await _call(hass, entry, "add_product", name="Rice", default_zone="pantry")
    await _call(hass, entry, "set_stock", product="rice", quantity=2)
    before = await inventory.async_dump()

    exported = await _call(hass, entry, "export_data")
    path = Path(exported["path"])
    assert path.parent == config_dir / "quartermaster" / "exports"
    assert json.loads(path.read_text())["format"] == "quartermaster"

    csv_export = await _call(hass, entry, "export_data", format="csv")
    assert csv_export["filename"].endswith(".csv")

    await _call(hass, entry, "adjust_stock", product="rice")
    imported = await _call(hass, entry, "import_data", filename=exported["filename"])
    assert imported["imported"]["products"] == 1
    assert await inventory.async_dump() == before

    imported = await _call(hass, entry, "import_data", filename=csv_export["filename"])
    assert imported["imported"] == {"zones": 0, "products": 0, "stock": 1}


async def test_import_errors(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Missing files and paths outside the folder are refused."""
    for filename, key in (
        ("missing.json", "import_unreadable"),
        ("../configuration.yaml", "invalid_filename"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, init_integration, "import_data", filename=filename)
        assert err.value.translation_key == key


async def test_transfer_actions_need_admin(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_read_only_user: User
) -> None:
    """Export and import are refused to non-admin users."""
    for service, data in (("export_data", {}), ("import_data", {"filename": "a.json"})):
        with pytest.raises(Unauthorized):
            await _call(
                hass,
                init_integration,
                service,
                context=Context(user_id=hass_read_only_user.id),
                **data,
            )


async def test_config_entry_is_optional(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Without config_entry_id the only loaded entry is used."""
    await _kitchen(hass, init_integration)
    response = await hass.services.async_call(
        DOMAIN,
        "add_product",
        {"name": "Salt", "default_zone": "pantry"},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    assert response["name"] == "Salt"


async def test_no_loaded_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Without config_entry_id and no loaded entry the action is refused."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "undo_last", {}, blocking=True)
    assert err.value.translation_key == "service_config_entry_not_loaded"


async def test_zone_actions(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Zones are added and removed through actions."""
    zone = await _call(
        hass, init_integration, "add_zone", name="Garage shelf", aliases=["garage"]
    )
    assert zone == {
        "zone_id": "garage_shelf",
        "name": "Garage shelf",
        "aliases": ["garage"],
    }
    own_id = await _call(
        hass, init_integration, "add_zone", name="Cellar", zone_id="cl"
    )
    assert own_id["zone_id"] == "cl"

    removed = await _call(hass, init_integration, "remove_zone", zone="garage")
    assert removed["zone_id"] == "garage_shelf"
    zones = await init_integration.runtime_data.inventory.async_list_zones()
    assert [z.id for z in zones] == ["cl"]

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, init_integration, "remove_zone", zone="garage")
    assert err.value.translation_key == "zone_not_found"


async def test_product_life_cycle_actions(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """update_product, move_stock and archive_product, with null clearing."""
    entry = init_integration
    await _kitchen(hass, entry)
    await _call(hass, entry, "add_product", name="Milk", category="dairy")
    await _call(hass, entry, "set_stock", product="milk", quantity=3, zone="frigo")
    updated = await _call(
        hass,
        entry,
        "update_product",
        product="milk",
        name="Whole milk",
        aliases=["latte"],
        category=None,
        default_zone="Pantry",
    )
    assert (updated["name"], updated["aliases"], updated["category"]) == (
        "Whole milk",
        ["latte"],
        None,
    )
    assert updated["default_zone_id"] == "pantry"
    moved = await _call(
        hass, entry, "move_stock", product="latte", to_zone="pantry", quantity=1
    )
    assert [(m["zone_id"], m["delta"], m["kind"]) for m in moved["movements"]] == [
        ("fridge", -1, "move"),
        ("pantry", 1, "move"),
    ]
    archived = await _call(hass, entry, "archive_product", product="latte")
    assert archived["archived"] is True
    assert sorted(m["delta"] for m in archived["movements"]) == [-2, -1]
    snapshot = entry.runtime_data.coordinator.data
    assert all(p.name != "Whole milk" for p in snapshot.products.values())
    back = await _call(
        hass, entry, "archive_product", product="whole milk", archived=False
    )
    assert back["archived"] is False
