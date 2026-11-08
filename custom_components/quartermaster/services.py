"""Actions of the Quartermaster integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv, service
from homeassistant.util.json import JsonValueType
import probatio

from .const import DOMAIN, TRANSFER_DIR
from .core.inventory import (
    MAX_QUANTITY,
    InventoryError,
    InventoryService,
    Movement,
    StockOperation,
)
from .core.transfer import (
    async_export_csv,
    async_export_json,
    async_import_csv,
    async_import_json,
    export_filename,
    resolve_transfer_file,
)
from .storage.models import Channel, Product

SERVICE_ADJUST_STOCK = "adjust_stock"
SERVICE_UNDO_LAST = "undo_last"
SERVICE_SET_STOCK = "set_stock"
SERVICE_ADD_PRODUCT = "add_product"
SERVICE_SET_ESSENTIAL = "set_essential"
SERVICE_EXPORT_DATA = "export_data"
SERVICE_IMPORT_DATA = "import_data"

ATTR_PRODUCT = "product"
ATTR_OPERATION = "operation"
ATTR_QUANTITY = "quantity"
ATTR_ZONE = "zone"
ATTR_BEST_BEFORE = "best_before"
ATTR_NAME = "name"
ATTR_ALIASES = "aliases"
ATTR_CATEGORY = "category"
ATTR_DEFAULT_ZONE = "default_zone"
ATTR_UNIT = "unit"
ATTR_ESSENTIAL = "essential"
ATTR_MIN_STOCK = "min_stock"
ATTR_TARGET_STOCK = "target_stock"
ATTR_VISUAL_HINTS = "visual_hints"
ATTR_BARCODE = "barcode"
ATTR_SHELF_LIFE_DAYS = "shelf_life_days"
ATTR_FORMAT = "format"
ATTR_FILENAME = "filename"

FORMAT_JSON = "json"
FORMAT_CSV = "csv"

QUANTITY = probatio.All(probatio.Coerce(float), probatio.Range(min=0, max=MAX_QUANTITY))
# Optional: Quartermaster allows a single entry, which is used when omitted
# (deviation from the action-setup rule, see TECHNICAL_NOTES).
ENTRY = {probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}

ADJUST_STOCK_SCHEMA = probatio.Schema(
    {
        **ENTRY,
        probatio.Required(ATTR_PRODUCT): cv.string,
        probatio.Optional(
            ATTR_OPERATION, default=StockOperation.CONSUME
        ): probatio.Coerce(StockOperation),
        probatio.Optional(ATTR_QUANTITY): QUANTITY,
        probatio.Optional(ATTR_ZONE): cv.string,
        probatio.Optional(ATTR_BEST_BEFORE): cv.date,
    }
)
UNDO_LAST_SCHEMA = probatio.Schema(ENTRY)
SET_STOCK_SCHEMA = probatio.Schema(
    {
        **ENTRY,
        probatio.Required(ATTR_PRODUCT): cv.string,
        probatio.Required(ATTR_QUANTITY): QUANTITY,
        probatio.Optional(ATTR_ZONE): cv.string,
        probatio.Optional(ATTR_BEST_BEFORE): cv.date,
    }
)
ADD_PRODUCT_SCHEMA = probatio.Schema(
    {
        **ENTRY,
        probatio.Required(ATTR_NAME): cv.string,
        probatio.Optional(ATTR_ALIASES, default=list): probatio.All(
            cv.ensure_list, [cv.string]
        ),
        probatio.Optional(ATTR_CATEGORY): cv.string,
        probatio.Optional(ATTR_DEFAULT_ZONE): cv.string,
        probatio.Optional(ATTR_UNIT): cv.string,
        probatio.Optional(ATTR_ESSENTIAL, default=False): cv.boolean,
        probatio.Optional(ATTR_MIN_STOCK): QUANTITY,
        probatio.Optional(ATTR_TARGET_STOCK): QUANTITY,
        probatio.Optional(ATTR_VISUAL_HINTS): cv.string,
        probatio.Optional(ATTR_BARCODE): cv.string,
        probatio.Optional(ATTR_SHELF_LIFE_DAYS): cv.positive_int,
    }
)
SET_ESSENTIAL_SCHEMA = probatio.Schema(
    {
        **ENTRY,
        probatio.Required(ATTR_PRODUCT): cv.string,
        probatio.Optional(ATTR_ESSENTIAL, default=True): cv.boolean,
        probatio.Optional(ATTR_MIN_STOCK): QUANTITY,
        probatio.Optional(ATTR_TARGET_STOCK): QUANTITY,
    }
)
EXPORT_DATA_SCHEMA = probatio.Schema(
    {
        **ENTRY,
        probatio.Optional(ATTR_FORMAT, default=FORMAT_JSON): probatio.In(
            [FORMAT_JSON, FORMAT_CSV]
        ),
    }
)
IMPORT_DATA_SCHEMA = probatio.Schema(
    {**ENTRY, probatio.Required(ATTR_FILENAME): cv.string}
)


def _inventory(call: ServiceCall) -> InventoryService:
    entry = service.async_get_config_entry(
        call.hass, DOMAIN, call.data.get(ATTR_CONFIG_ENTRY_ID)
    )
    inventory: InventoryService = entry.runtime_data.inventory
    return inventory


def _movement(movement: Movement) -> dict[str, Any]:
    entry = movement.entry
    return {
        "journal_id": entry.id,
        "kind": entry.kind.value,
        "product_id": movement.product.id,
        "product": movement.product.name,
        "zone_id": movement.zone.id,
        "zone": movement.zone.name,
        "delta": entry.delta,
        "quantity": entry.quantity_after,
        "unit": movement.product.unit,
    }


def _product(product: Product) -> dict[str, Any]:
    return {
        "product_id": product.id,
        "name": product.name,
        "aliases": product.aliases,
        "category": product.category,
        "default_zone_id": product.default_zone_id,
        "unit": product.unit,
        "essential": product.essential,
        "min_stock": product.min_stock,
        "target_stock": product.target_stock,
    }


def _transfer_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(DOMAIN, TRANSFER_DIR))


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


async def _async_adjust_stock(call: ServiceCall) -> ServiceResponse:
    movement = await _inventory(call).async_adjust_stock(
        call.data[ATTR_PRODUCT],
        call.data[ATTR_OPERATION],
        quantity=call.data.get(ATTR_QUANTITY),
        zone_ref=call.data.get(ATTR_ZONE),
        best_before=call.data.get(ATTR_BEST_BEFORE),
        channel=Channel.SERVICE,
        user_id=call.context.user_id,
    )
    return _movement(movement) if call.return_response else None


async def _async_undo_last(call: ServiceCall) -> ServiceResponse:
    original, undo = await _inventory(call).async_undo_last(
        channel=Channel.SERVICE, user_id=call.context.user_id
    )
    if not call.return_response:
        return None
    return {"undone": _movement(original), "undo": _movement(undo)}


async def _async_set_stock(call: ServiceCall) -> ServiceResponse:
    movement = await _inventory(call).async_set_stock(
        call.data[ATTR_PRODUCT],
        call.data[ATTR_QUANTITY],
        zone_ref=call.data.get(ATTR_ZONE),
        best_before=call.data.get(ATTR_BEST_BEFORE),
        channel=Channel.SERVICE,
        user_id=call.context.user_id,
    )
    return _movement(movement) if call.return_response else None


async def _async_add_product(call: ServiceCall) -> ServiceResponse:
    product = await _inventory(call).async_add_product(
        call.data[ATTR_NAME],
        aliases=call.data[ATTR_ALIASES],
        category=call.data.get(ATTR_CATEGORY),
        default_zone=call.data.get(ATTR_DEFAULT_ZONE),
        unit=call.data.get(ATTR_UNIT),
        essential=call.data[ATTR_ESSENTIAL],
        min_stock=call.data.get(ATTR_MIN_STOCK),
        target_stock=call.data.get(ATTR_TARGET_STOCK),
        visual_hints=call.data.get(ATTR_VISUAL_HINTS),
        barcode=call.data.get(ATTR_BARCODE),
        shelf_life_days=call.data.get(ATTR_SHELF_LIFE_DAYS),
    )
    return _product(product) if call.return_response else None


async def _async_set_essential(call: ServiceCall) -> ServiceResponse:
    product = await _inventory(call).async_set_essential(
        call.data[ATTR_PRODUCT],
        essential=call.data[ATTR_ESSENTIAL],
        min_stock=call.data.get(ATTR_MIN_STOCK),
        target_stock=call.data.get(ATTR_TARGET_STOCK),
    )
    return _product(product) if call.return_response else None


async def _async_export_data(call: ServiceCall) -> ServiceResponse:
    inventory = _inventory(call)
    if call.data[ATTR_FORMAT] == FORMAT_CSV:
        text = await async_export_csv(inventory)
    else:
        text = await async_export_json(inventory)
    filename = export_filename(call.data[ATTR_FORMAT])
    path = _transfer_dir(call.hass) / filename
    await call.hass.async_add_executor_job(_write_file, path, text)
    return {"filename": filename, "path": str(path)} if call.return_response else None


async def _async_import_data(call: ServiceCall) -> ServiceResponse:
    inventory = _inventory(call)
    path = resolve_transfer_file(_transfer_dir(call.hass), call.data[ATTR_FILENAME])
    try:
        text = await call.hass.async_add_executor_job(_read_file, path)
    except (OSError, UnicodeDecodeError) as err:
        raise InventoryError("import_unreadable", filename=path.name) from err
    if path.suffix.lower() == f".{FORMAT_CSV}":
        counts = await async_import_csv(inventory, text, user_id=call.context.user_id)
    else:
        counts = await async_import_json(inventory, text)
    imported: dict[str, JsonValueType] = {**counts}
    return {"imported": imported} if call.return_response else None


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration actions."""
    for name, handler, schema in (
        (SERVICE_ADJUST_STOCK, _async_adjust_stock, ADJUST_STOCK_SCHEMA),
        (SERVICE_UNDO_LAST, _async_undo_last, UNDO_LAST_SCHEMA),
        (SERVICE_SET_STOCK, _async_set_stock, SET_STOCK_SCHEMA),
        (SERVICE_ADD_PRODUCT, _async_add_product, ADD_PRODUCT_SCHEMA),
        (SERVICE_SET_ESSENTIAL, _async_set_essential, SET_ESSENTIAL_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
    # Writing and replacing files in the config folder is for admins only.
    for name, handler, schema in (
        (SERVICE_EXPORT_DATA, _async_export_data, EXPORT_DATA_SCHEMA),
        (SERVICE_IMPORT_DATA, _async_import_data, IMPORT_DATA_SCHEMA),
    ):
        service.async_register_admin_service(
            hass,
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
