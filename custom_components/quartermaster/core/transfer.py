"""Export and import of the inventory as JSON (full state) or CSV (stock list).

JSON holds every persistent table and an import replaces the inventory with
it, so export followed by import gives back the same state. CSV holds one row
per product and zone and an import merges it: missing zones and products are
created and the quantities are journaled as `import` movements.
"""

from __future__ import annotations

import csv
from datetime import date
import io
import json
from pathlib import Path
from typing import Any, Final

from homeassistant.util import dt as dt_util
import probatio

from custom_components.quartermaster.storage.models import Channel, MovementKind

from .inventory import MAX_QUANTITY, InventoryError, InventoryService, normalize

FORMAT_NAME: Final = "quartermaster"
FORMAT_VERSION: Final = 1
CSV_COLUMNS: Final = (
    "product",
    "aliases",
    "category",
    "unit",
    "essential",
    "min_stock",
    "target_stock",
    "barcode",
    "shelf_life_days",
    "zone",
    "quantity",
    "best_before",
)
ALIAS_SEPARATOR: Final = ";"

_OPT_STR = probatio.Any(None, str)
_OPT_NUM = probatio.Any(None, probatio.Coerce(float))
_OPT_INT = probatio.Any(None, int)

TABLES_SCHEMA = probatio.Schema(
    {
        probatio.Required("zones"): [
            {
                probatio.Required("id"): str,
                probatio.Required("name"): str,
                probatio.Required("aliases"): [str],
                probatio.Required("sources"): [dict],
                probatio.Required("check_schedule"): _OPT_STR,
                probatio.Required("reminder_days"): _OPT_INT,
                probatio.Required("confidence_threshold"): _OPT_NUM,
            }
        ],
        probatio.Required("products"): [
            {
                probatio.Required("id"): int,
                probatio.Required("name"): str,
                probatio.Required("aliases"): [str],
                probatio.Required("category"): _OPT_STR,
                probatio.Required("default_zone_id"): _OPT_STR,
                probatio.Required("unit"): _OPT_STR,
                probatio.Required("essential"): bool,
                probatio.Required("min_stock"): _OPT_NUM,
                probatio.Required("target_stock"): _OPT_NUM,
                probatio.Required("visual_hints"): _OPT_STR,
                probatio.Required("barcode"): _OPT_STR,
                probatio.Required("shelf_life_days"): _OPT_INT,
                probatio.Required("archived"): bool,
            }
        ],
        probatio.Required("stock"): [
            {
                probatio.Required("product_id"): int,
                probatio.Required("zone_id"): str,
                probatio.Required("quantity"): probatio.Coerce(float),
                probatio.Required("best_before"): _OPT_STR,
                probatio.Required("opened_at"): _OPT_STR,
                probatio.Required("updated_at"): str,
            }
        ],
        probatio.Required("journal"): [
            {
                probatio.Required("id"): int,
                probatio.Required("ts"): str,
                probatio.Required("product_id"): int,
                probatio.Required("zone_id"): str,
                probatio.Required("kind"): probatio.In([k.value for k in MovementKind]),
                probatio.Required("delta"): probatio.Coerce(float),
                probatio.Required("quantity_after"): probatio.Coerce(float),
                probatio.Required("channel"): probatio.In([c.value for c in Channel]),
                probatio.Required("user_id"): _OPT_STR,
                probatio.Required("confidence"): _OPT_NUM,
                probatio.Required("image_ref"): _OPT_STR,
                probatio.Required("undoes_id"): _OPT_INT,
            }
        ],
        probatio.Required("shopping_items"): [
            {
                probatio.Required("id"): int,
                probatio.Required("product_id"): _OPT_INT,
                probatio.Required("text"): str,
                probatio.Required("quantity"): _OPT_NUM,
                probatio.Required("status"): str,
                probatio.Required("origin"): str,
                probatio.Required("external_uid"): _OPT_STR,
            }
        ],
    }
)

DOCUMENT_SCHEMA = probatio.Schema(
    {
        probatio.Required("format"): FORMAT_NAME,
        probatio.Required("version"): FORMAT_VERSION,
        probatio.Optional("exported_at"): str,
        probatio.Required("tables"): TABLES_SCHEMA,
    }
)


def export_filename(extension: str) -> str:
    """Return a timestamped export file name."""
    return f"quartermaster-{dt_util.now().strftime('%Y%m%d-%H%M%S')}.{extension}"


async def async_export_json(service: InventoryService) -> str:
    """Return the whole inventory as a JSON document."""
    document = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "exported_at": dt_util.utcnow().isoformat(),
        "tables": await service.async_dump(),
    }
    return json.dumps(document, ensure_ascii=False, indent=2)


async def async_export_csv(service: InventoryService) -> str:
    """Return one CSV row per product and zone (products without stock too)."""
    zones = {zone.id: zone for zone in await service.async_list_zones()}
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for product in await service.async_list_products():
        base = {
            "product": product.name,
            "aliases": ALIAS_SEPARATOR.join(product.aliases),
            "category": product.category or "",
            "unit": product.unit or "",
            "essential": "1" if product.essential else "0",
            "min_stock": _number(product.min_stock),
            "target_stock": _number(product.target_stock),
            "barcode": product.barcode or "",
            "shelf_life_days": product.shelf_life_days or "",
        }
        levels = await service.async_get_stock(product_id=product.id)
        if not levels:
            writer.writerow({**base, "zone": "", "quantity": "", "best_before": ""})
        for level in levels:
            writer.writerow(
                {
                    **base,
                    "zone": zones[level.zone_id].name,
                    "quantity": _number(level.quantity),
                    "best_before": level.best_before.isoformat()
                    if level.best_before
                    else "",
                }
            )
    return buffer.getvalue()


def _number(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value == int(value) else str(value)


async def async_import_json(service: InventoryService, text: str) -> dict[str, int]:
    """Replace the inventory with a JSON export; return the row counts."""
    try:
        document = DOCUMENT_SCHEMA(json.loads(text))
    except (ValueError, probatio.Invalid) as err:
        raise InventoryError("invalid_import", error=str(err)) from err
    tables: dict[str, list[dict[str, Any]]] = document["tables"]
    _check_references(tables)
    await service.async_restore(tables)
    return {table: len(rows) for table, rows in tables.items()}


def _check_references(tables: dict[str, list[dict[str, Any]]]) -> None:
    zone_ids = {zone["id"] for zone in tables["zones"]}
    product_ids = {product["id"] for product in tables["products"]}
    journal_ids = {entry["id"] for entry in tables["journal"]}
    problems = [
        f"product {p['id']}: zone {p['default_zone_id']}"
        for p in tables["products"]
        if p["default_zone_id"] is not None and p["default_zone_id"] not in zone_ids
    ]
    problems += [
        f"stock: product {s['product_id']}, zone {s['zone_id']}"
        for s in tables["stock"]
        if s["product_id"] not in product_ids or s["zone_id"] not in zone_ids
    ]
    problems += [
        f"journal {j['id']}: undoes {j['undoes_id']}"
        for j in tables["journal"]
        if j["undoes_id"] is not None and j["undoes_id"] not in journal_ids
    ]
    problems += [
        f"shopping item {i['id']}: product {i['product_id']}"
        for i in tables["shopping_items"]
        if i["product_id"] is not None and i["product_id"] not in product_ids
    ]
    if problems:
        raise InventoryError("invalid_import", error="; ".join(problems[:5]))


async def async_import_csv(
    service: InventoryService, text: str, *, user_id: str | None
) -> dict[str, int]:
    """Merge a CSV stock list; return how many zones, products and rows changed."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "product" not in reader.fieldnames:
        raise InventoryError("invalid_import", error="missing column: product")
    counts = {"zones": 0, "products": 0, "stock": 0}
    for line, row in enumerate(reader, start=2):
        try:
            await _import_csv_row(service, row, counts, user_id)
        except (ValueError, InventoryError) as err:
            raise InventoryError("invalid_import", error=f"line {line}: {err}") from err
    return counts


async def _import_csv_row(
    service: InventoryService,
    row: dict[str, str | None],
    counts: dict[str, int],
    user_id: str | None,
) -> None:
    def cell(column: str) -> str:
        return (row.get(column) or "").strip()

    name = cell("product")
    if not name:
        return
    zone_name = cell("zone")
    zone_ref: str | None = None
    if zone_name:
        known = {normalize(z.name): z for z in await service.async_list_zones()}
        if normalize(zone_name) not in known:
            await service.async_add_zone(zone_name)
            counts["zones"] += 1
        zone_ref = zone_name
    products = {normalize(p.name) for p in await service.async_list_products()}
    if normalize(name) not in products:
        min_stock = _optional_float(cell("min_stock"))
        await service.async_add_product(
            name,
            aliases=[a for a in cell("aliases").split(ALIAS_SEPARATOR) if a.strip()],
            category=cell("category") or None,
            default_zone=zone_ref,
            unit=cell("unit") or None,
            essential=cell("essential").lower() in {"1", "true", "yes"},
            min_stock=min_stock,
            target_stock=_optional_float(cell("target_stock")),
            barcode=cell("barcode") or None,
            shelf_life_days=int(cell("shelf_life_days"))
            if cell("shelf_life_days")
            else None,
        )
        counts["products"] += 1
    quantity = _optional_float(cell("quantity"))
    if zone_ref and quantity is not None:
        if quantity > MAX_QUANTITY:
            raise ValueError(f"quantity {quantity}")
        best_before = cell("best_before")
        await service.async_set_stock(
            name,
            quantity,
            zone_ref=zone_ref,
            best_before=date.fromisoformat(best_before) if best_before else None,
            channel=Channel.SERVICE,
            user_id=user_id,
            kind=MovementKind.IMPORT,
        )
        counts["stock"] += 1


def _optional_float(value: str) -> float | None:
    return float(value.replace(",", ".")) if value else None


def resolve_transfer_file(folder: Path, filename: str) -> Path:
    """Return a file inside the transfer folder, refusing any other path."""
    if not filename or Path(filename).name != filename or filename.startswith("."):
        raise InventoryError("invalid_filename", filename=filename)
    return folder / filename
