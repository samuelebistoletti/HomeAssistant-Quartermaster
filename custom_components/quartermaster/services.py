"""Actions of the Quartermaster integration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_CONFIG_ENTRY_ID, ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv, service
from homeassistant.util.json import JsonValueType

if TYPE_CHECKING:
    from . import QuartermasterRuntimeData
import probatio

from .const import DOMAIN, TRANSFER_DIR
from .core.inventory import (
    MAX_QUANTITY,
    InventoryError,
    InventoryService,
    Movement,
    StockOperation,
)
from .core.shopping import ImportTarget
from .core.transfer import (
    async_export_csv,
    async_export_json,
    async_import_csv,
    async_import_json,
    export_filename,
    resolve_transfer_file,
)
from .storage.models import Channel, Product, ShoppingOrigin, ShoppingStatus, Zone
from .vision.analyze import async_camera_frame, async_photo_pipeline

SERVICE_ADJUST_STOCK = "adjust_stock"
SERVICE_ADD_ZONE = "add_zone"
SERVICE_REMOVE_ZONE = "remove_zone"
SERVICE_UNDO_LAST = "undo_last"
SERVICE_SET_STOCK = "set_stock"
SERVICE_ADD_PRODUCT = "add_product"
SERVICE_SET_ESSENTIAL = "set_essential"
SERVICE_EXPORT_DATA = "export_data"
SERVICE_IMPORT_DATA = "import_data"
SERVICE_GET_SUGGESTIONS = "get_suggestions"
SERVICE_REFRESH_SHOPPING_LIST = "refresh_shopping_list"
SERVICE_START_SHOPPING = "start_shopping"
SERVICE_ADD_TO_SHOPPING_LIST = "add_to_shopping_list"
SERVICE_IMPORT_FROM_TODO = "import_from_todo"
SERVICE_ASK = "ask"
SERVICE_ANALYZE_IMAGE = "analyze_image"
SERVICE_CONFIRM_PENDING = "confirm_pending"

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
ATTR_ZONE_ID = "zone_id"
ATTR_TEXT = "text"
ATTR_FROM_SUGGESTION = "from_suggestion"
ATTR_TARGET = "target"
ATTR_APPLY = "apply"
ATTR_CHANNEL = "channel"
ATTR_LANGUAGE = "language"
ATTR_SESSION_KEY = "session_key"
ATTR_FILE = "file"
ATTR_CAMERA = "camera_entity_id"
ATTR_PROPOSAL_ID = "proposal_id"
ATTR_DECISION = "decision"
ATTR_ITEMS = "items"
ATTR_CORRECTIONS = "corrections"
ATTR_KEY = "key"

ASK_CHANNELS = {
    "alexa": Channel.ALEXA,
    "assist": Channel.ASSIST,
    "voice": Channel.ASSIST,
    "telegram": Channel.TELEGRAM,
    "dashboard": Channel.DASHBOARD,
    "service": Channel.SERVICE,
}

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
ADD_ZONE_SCHEMA = probatio.Schema(
    {
        **ENTRY,
        probatio.Required(ATTR_NAME): cv.string,
        probatio.Optional(ATTR_ALIASES, default=list): probatio.All(
            cv.ensure_list, [cv.string]
        ),
        probatio.Optional(ATTR_ZONE_ID): cv.slug,
    }
)
REMOVE_ZONE_SCHEMA = probatio.Schema({**ENTRY, probatio.Required(ATTR_ZONE): cv.string})
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
ENTRY_ONLY_SCHEMA = probatio.Schema(ENTRY)
ADD_TO_SHOPPING_LIST_SCHEMA = probatio.All(
    probatio.Schema(
        {
            **ENTRY,
            probatio.Exclusive(ATTR_PRODUCT, "item"): cv.string,
            probatio.Exclusive(ATTR_TEXT, "item"): cv.string,
            probatio.Optional(ATTR_QUANTITY): QUANTITY,
            probatio.Optional(ATTR_FROM_SUGGESTION, default=False): cv.boolean,
        }
    ),
    cv.has_at_least_one_key(ATTR_PRODUCT, ATTR_TEXT),
)
IMPORT_FROM_TODO_SCHEMA = probatio.Schema(
    {
        **ENTRY,
        probatio.Required(ATTR_ENTITY_ID): cv.entity_domain("todo"),
        probatio.Required(ATTR_TARGET): probatio.Coerce(ImportTarget),
        probatio.Optional(ATTR_MIN_STOCK, default=1.0): QUANTITY,
        probatio.Optional(ATTR_APPLY, default=False): cv.boolean,
    }
)
ASK_SCHEMA = probatio.Schema(
    {
        **ENTRY,
        probatio.Required(ATTR_TEXT): cv.string,
        probatio.Optional(ATTR_CHANNEL, default="service"): probatio.In(
            list(ASK_CHANNELS)
        ),
        probatio.Optional(ATTR_LANGUAGE): cv.string,
        probatio.Optional(ATTR_SESSION_KEY): cv.string,
    }
)
ANALYZE_IMAGE_SCHEMA = probatio.All(
    probatio.Schema(
        {
            **ENTRY,
            probatio.Required(ATTR_ZONE): cv.string,
            probatio.Exclusive(ATTR_FILE, "image"): cv.string,
            probatio.Exclusive(ATTR_CAMERA, "image"): cv.entity_domain("camera"),
            probatio.Optional(ATTR_CHANNEL, default="service"): probatio.In(
                list(ASK_CHANNELS)
            ),
        }
    ),
    cv.has_at_least_one_key(ATTR_FILE, ATTR_CAMERA),
)
CONFIRM_PENDING_SCHEMA = probatio.Schema(
    {
        **ENTRY,
        probatio.Required(ATTR_PROPOSAL_ID): cv.positive_int,
        probatio.Optional(ATTR_DECISION, default="confirm"): probatio.In(
            ["confirm", "reject"]
        ),
        probatio.Optional(ATTR_ITEMS): [cv.positive_int],
        probatio.Optional(ATTR_CORRECTIONS, default=list): [
            {
                probatio.Required(ATTR_KEY): cv.positive_int,
                probatio.Optional(ATTR_PRODUCT): cv.string,
                probatio.Optional(ATTR_QUANTITY): QUANTITY,
            }
        ],
    }
)
IMPORT_DATA_SCHEMA = probatio.Schema(
    {**ENTRY, probatio.Required(ATTR_FILENAME): cv.string}
)


def _runtime(call: ServiceCall) -> QuartermasterRuntimeData:
    entry = service.async_get_config_entry(
        call.hass, DOMAIN, call.data.get(ATTR_CONFIG_ENTRY_ID)
    )
    runtime: QuartermasterRuntimeData = entry.runtime_data
    return runtime


def _inventory(call: ServiceCall) -> InventoryService:
    return _runtime(call).inventory


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


def _zone(zone: Zone) -> dict[str, Any]:
    return {"zone_id": zone.id, "name": zone.name, "aliases": zone.aliases}


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
    originals, undos = await _inventory(call).async_undo_last(
        channel=Channel.SERVICE, user_id=call.context.user_id
    )
    if not call.return_response:
        return None
    return {
        "undone": [_movement(movement) for movement in originals],
        "undo": [_movement(movement) for movement in undos],
    }


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


async def _async_add_zone(call: ServiceCall) -> ServiceResponse:
    zone = await _inventory(call).async_add_zone(
        call.data[ATTR_NAME],
        aliases=call.data[ATTR_ALIASES],
        zone_id=call.data.get(ATTR_ZONE_ID),
    )
    return _zone(zone) if call.return_response else None


async def _async_remove_zone(call: ServiceCall) -> ServiceResponse:
    zone = await _inventory(call).async_remove_zone(call.data[ATTR_ZONE])
    return _zone(zone) if call.return_response else None


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


def _shopping_response(runtime: QuartermasterRuntimeData) -> dict[str, Any]:
    data = runtime.coordinator.data
    return {
        "to_buy": [
            {
                "product_id": entry.product.id,
                "product": entry.product.name,
                "have": entry.total,
                "quantity": entry.quantity,
                "unit": entry.product.unit,
            }
            for entry in data.analysis.to_buy
        ],
        "suggestions": [
            {
                "product_id": entry.product.id,
                "product": entry.product.name,
                "have": entry.total,
                "unit": entry.product.unit,
                "reason": entry.reason.value,
                **entry.details,
            }
            for entry in data.analysis.suggestions
        ],
        "shopping_list": [
            {
                "item_id": item.id,
                "text": item.text,
                "product_id": item.product_id,
                "quantity": item.quantity,
                "origin": item.origin.value,
            }
            for item in data.shopping_items
            if item.status is ShoppingStatus.NEEDS_ACTION
        ],
        "shopping_mode_until": data.shopping_until.isoformat()
        if data.shopping_mode and data.shopping_until
        else None,
    }


async def _async_get_suggestions(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    await runtime.coordinator.async_refresh()
    return _shopping_response(runtime)


async def _async_refresh_shopping_list(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    await runtime.coordinator.async_refresh()
    return _shopping_response(runtime) if call.return_response else None


async def _async_start_shopping(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    await runtime.coordinator.async_start_shopping()
    return _shopping_response(runtime) if call.return_response else None


async def _async_add_to_shopping_list(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    item = await runtime.shopping.async_add(
        text=call.data.get(ATTR_TEXT),
        product_ref=call.data.get(ATTR_PRODUCT),
        quantity=call.data.get(ATTR_QUANTITY),
        origin=ShoppingOrigin.SUGGESTION
        if call.data[ATTR_FROM_SUGGESTION]
        else ShoppingOrigin.MANUAL,
    )
    await runtime.coordinator.async_refresh()
    if not call.return_response:
        return None
    return {
        "item_id": item.id,
        "text": item.text,
        "product_id": item.product_id,
        "quantity": item.quantity,
        "origin": item.origin.value,
    }


async def _async_import_from_todo(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    steps = await runtime.shopping.async_import_from_todo(
        call.data[ATTR_ENTITY_ID],
        call.data[ATTR_TARGET],
        min_stock=call.data[ATTR_MIN_STOCK],
        apply=call.data[ATTR_APPLY],
    )
    if call.data[ATTR_APPLY]:
        await runtime.coordinator.async_refresh()
    if not call.return_response:
        return None
    return {
        "applied": call.data[ATTR_APPLY],
        "items": [
            {
                "summary": step.summary,
                "action": step.action.value,
                "product": step.product,
            }
            for step in steps
        ],
    }


async def _async_ask(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    key = call.data.get(ATTR_SESSION_KEY) or call.context.user_id or "default"
    reply = await runtime.voice.async_ask(
        call.data[ATTR_TEXT],
        channel=ASK_CHANNELS[call.data[ATTR_CHANNEL]],
        key=key,
        language=call.data.get(ATTR_LANGUAGE),
        context=call.context,
    )
    return reply.as_dict() if call.return_response else None


async def _async_analyze_image(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    if path_text := call.data.get(ATTR_FILE):
        path = Path(path_text)
        if not call.hass.config.is_allowed_path(str(path)):
            raise InventoryError("path_not_allowed", path=str(path))
        try:
            data = await call.hass.async_add_executor_job(path.read_bytes)
        except OSError as err:
            raise InventoryError("import_unreadable", filename=path.name) from err
    else:
        data = await async_camera_frame(call.hass, call.data[ATTR_CAMERA])
    response = await async_photo_pipeline(
        runtime,
        data,
        call.data[ATTR_ZONE],
        channel=ASK_CHANNELS[call.data[ATTR_CHANNEL]],
        user_id=call.context.user_id,
    )
    return response if call.return_response else None


async def _async_confirm_pending(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    proposal_id = call.data[ATTR_PROPOSAL_ID]
    if call.data[ATTR_DECISION] == "reject":
        await runtime.proposals.async_reject(proposal_id)
        applied: list[dict[str, Any]] = []
    else:
        applied = await runtime.proposals.async_confirm(
            proposal_id,
            keys=call.data.get(ATTR_ITEMS),
            corrections={c[ATTR_KEY]: c for c in call.data[ATTR_CORRECTIONS]},
            channel=Channel.SERVICE,
            user_id=call.context.user_id,
        )
    await runtime.coordinator.async_refresh()
    if not call.return_response:
        return None
    return {"decision": call.data[ATTR_DECISION], "applied": applied}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration actions."""
    for name, handler, schema in (
        (SERVICE_ADJUST_STOCK, _async_adjust_stock, ADJUST_STOCK_SCHEMA),
        (SERVICE_UNDO_LAST, _async_undo_last, UNDO_LAST_SCHEMA),
        (SERVICE_SET_STOCK, _async_set_stock, SET_STOCK_SCHEMA),
        (SERVICE_ADD_ZONE, _async_add_zone, ADD_ZONE_SCHEMA),
        (SERVICE_REMOVE_ZONE, _async_remove_zone, REMOVE_ZONE_SCHEMA),
        (SERVICE_ADD_PRODUCT, _async_add_product, ADD_PRODUCT_SCHEMA),
        (SERVICE_SET_ESSENTIAL, _async_set_essential, SET_ESSENTIAL_SCHEMA),
        (
            SERVICE_REFRESH_SHOPPING_LIST,
            _async_refresh_shopping_list,
            ENTRY_ONLY_SCHEMA,
        ),
        (SERVICE_START_SHOPPING, _async_start_shopping, ENTRY_ONLY_SCHEMA),
        (
            SERVICE_ADD_TO_SHOPPING_LIST,
            _async_add_to_shopping_list,
            ADD_TO_SHOPPING_LIST_SCHEMA,
        ),
        (SERVICE_IMPORT_FROM_TODO, _async_import_from_todo, IMPORT_FROM_TODO_SCHEMA),
        (SERVICE_ASK, _async_ask, ASK_SCHEMA),
        (SERVICE_ANALYZE_IMAGE, _async_analyze_image, ANALYZE_IMAGE_SCHEMA),
        (SERVICE_CONFIRM_PENDING, _async_confirm_pending, CONFIRM_PENDING_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SUGGESTIONS,
        _async_get_suggestions,
        schema=ENTRY_ONLY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
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
