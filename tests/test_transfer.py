"""Tests for exporting and importing the inventory."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from custom_components.quartermaster.core.inventory import (
    InventoryError,
    InventoryService,
    StockOperation,
)
from custom_components.quartermaster.core.transfer import (
    async_export_csv,
    async_export_json,
    async_import_csv,
    async_import_json,
    export_filename,
    resolve_transfer_file,
)
from custom_components.quartermaster.storage.models import Channel


async def _populate(inventory: InventoryService) -> None:
    await inventory.async_add_zone("Fridge", aliases=["frigo"])
    await inventory.async_add_zone("Pantry")
    await inventory.async_add_product(
        "Milk",
        aliases=["latte", "whole milk"],
        default_zone="fridge",
        unit="bottle",
        essential=True,
        min_stock=1,
        target_stock=4,
    )
    await inventory.async_add_product(
        "Coffee", category="drinks", unit="pack", barcode="0001", shelf_life_days=365
    )
    await inventory.async_add_product("Salt")
    await inventory.async_adjust_stock(
        "milk",
        StockOperation.PURCHASE,
        quantity=2,
        best_before=date(2026, 11, 20),
        channel=Channel.SERVICE,
    )
    await inventory.async_set_stock(
        "coffee", 1.5, zone_ref="pantry", channel=Channel.TELEGRAM, user_id="u1"
    )
    await inventory.async_adjust_stock(
        "milk", StockOperation.CONSUME, channel=Channel.ALEXA
    )
    await inventory.async_undo_last(channel=Channel.SERVICE)


async def test_json_round_trip(inventory: InventoryService) -> None:
    """Export then import into another state gives back the same state."""
    await _populate(inventory)
    before = await inventory.async_dump()
    text = await async_export_json(inventory)

    await inventory.async_add_zone("Cellar")
    await inventory.async_set_stock(
        "salt", 3, zone_ref="cellar", channel=Channel.SERVICE
    )
    counts = await async_import_json(inventory, text)

    assert await inventory.async_dump() == before
    assert counts == {
        "zones": 2,
        "products": 3,
        "stock": 2,
        "journal": 4,
        "shopping_items": 0,
    }
    # The restored journal still undoes correctly.
    original, _ = await inventory.async_undo_last(channel=Channel.SERVICE)
    assert original.product.name == "Coffee"


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        json.dumps({"format": "other", "version": 1, "tables": {}}),
        json.dumps({"format": "quartermaster", "version": 99, "tables": {}}),
        json.dumps({"format": "quartermaster", "version": 1, "tables": {"zones": []}}),
    ],
)
async def test_json_import_refuses_invalid_documents(
    inventory: InventoryService, document: str
) -> None:
    """Malformed documents are refused before anything changes."""
    await _populate(inventory)
    before = await inventory.async_dump()
    with pytest.raises(InventoryError) as err:
        await async_import_json(inventory, document)
    assert err.value.translation_key == "invalid_import"
    assert await inventory.async_dump() == before


async def test_json_import_refuses_broken_references(
    inventory: InventoryService,
) -> None:
    """Rows pointing at missing zones, products or movements are refused."""
    await _populate(inventory)
    document = json.loads(await async_export_json(inventory))
    tables = document["tables"]
    tables["zones"] = [z for z in tables["zones"] if z["id"] != "fridge"]
    tables["journal"][0]["undoes_id"] = 999
    tables["shopping_items"] = [
        {
            "id": 1,
            "product_id": 42,
            "text": "Bread",
            "quantity": None,
            "status": "needs_action",
            "origin": "manual",
            "external_uid": None,
        }
    ]
    with pytest.raises(InventoryError) as err:
        await async_import_json(inventory, json.dumps(document))
    error = (err.value.translation_placeholders or {})["error"]
    assert "zone fridge" in error
    assert "undoes 999" in error
    assert "product 42" in error


async def test_csv_export(inventory: InventoryService) -> None:
    """CSV has one row per product and zone, products without stock included."""
    await _populate(inventory)
    lines = (await async_export_csv(inventory)).splitlines()
    assert lines == [
        "product,aliases,category,unit,essential,min_stock,target_stock,barcode,"
        "shelf_life_days,zone,quantity,best_before",
        "Coffee,,drinks,pack,0,,,0001,365,Pantry,1.5,",
        "Milk,latte;whole milk,,bottle,1,1,4,,,Fridge,2,2026-11-20",
        "Salt,,,,0,,,,,,,",
    ]


async def test_csv_round_trip_into_empty_inventory(
    inventory: InventoryService,
) -> None:
    """A CSV export imported into an empty inventory rebuilds products and stock."""
    await _populate(inventory)
    text = await async_export_csv(inventory)
    await inventory.async_restore(
        {"zones": [], "products": [], "stock": [], "journal": [], "shopping_items": []}
    )

    counts = await async_import_csv(inventory, text, user_id="u2")
    assert counts == {"zones": 2, "products": 3, "stock": 2}
    assert await async_export_csv(inventory) == text
    journal = await inventory.async_list_journal()
    assert {entry.kind.value for entry in journal} == {"import"}
    assert {entry.user_id for entry in journal} == {"u2"}

    # Importing again only updates quantities.
    counts = await async_import_csv(inventory, text, user_id=None)
    assert counts == {"zones": 0, "products": 0, "stock": 2}


async def test_csv_import_minimal_columns(inventory: InventoryService) -> None:
    """Only the product column is required; decimal commas are accepted."""
    text = 'product,zone,quantity\nRice,Pantry,"1,5"\n,,\nBeans,,\n'
    assert await async_import_csv(inventory, text, user_id=None) == {
        "zones": 1,
        "products": 2,
        "stock": 1,
    }
    rice = await inventory.async_find_product("rice")
    assert rice.default_zone_id == "pantry"
    [level] = await inventory.async_get_stock(product_id=rice.id)
    assert level.quantity == 1.5


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("name,quantity\nRice,1\n", "missing column: product"),
        ("product,zone,quantity\nRice,Pantry,lots\n", "line 2"),
        ("product,zone,quantity\nRice,Pantry,1000000\n", "line 2"),
        ("product,essential\nRice,1\n", "line 2"),
    ],
)
async def test_csv_import_errors(
    inventory: InventoryService, text: str, fragment: str
) -> None:
    """Bad CSV rows are reported with their line number."""
    with pytest.raises(InventoryError) as err:
        await async_import_csv(inventory, text, user_id=None)
    assert fragment in (err.value.translation_placeholders or {})["error"]


def test_transfer_file_names(tmp_path: Path) -> None:
    """Only plain file names inside the transfer folder are accepted."""
    assert resolve_transfer_file(tmp_path, "a.json") == tmp_path / "a.json"
    for name in ("", "../a.json", "/etc/passwd", "sub/a.json", ".hidden"):
        with pytest.raises(InventoryError):
            resolve_transfer_file(tmp_path, name)
    assert export_filename("csv").startswith("quartermaster-")
    assert export_filename("csv").endswith(".csv")
