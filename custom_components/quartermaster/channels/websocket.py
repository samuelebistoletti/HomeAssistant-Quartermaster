"""Websocket commands for the dashboard cards.

Reading is open to every authenticated user; writing (photo upload) needs an
administrator, or any user when the `non_admin_writes` option is on. Other
writes go through the integration actions, which apply the same rule.
"""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING, Any

from homeassistant.auth.models import User
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import entity_registry as er, service
import probatio

from custom_components.quartermaster.const import (
    CONF_NON_ADMIN_WRITES,
    DEFAULT_NON_ADMIN_WRITES,
    DOMAIN,
)
from custom_components.quartermaster.core.inventory import InventoryError
from custom_components.quartermaster.core.zones import ZONE_ERROR_FAILURES
from custom_components.quartermaster.storage.models import Channel, ShoppingStatus
from custom_components.quartermaster.vision.analyze import async_photo_pipeline
from custom_components.quartermaster.vision.quality import MAX_UPLOAD_BYTES

if TYPE_CHECKING:
    from custom_components.quartermaster import QuartermasterRuntimeData

API_VERSION = 1
JOURNAL_MAX = 500


def _runtime(hass: HomeAssistant) -> QuartermasterRuntimeData:
    entry = service.async_get_config_entry(hass, DOMAIN, None)
    runtime: QuartermasterRuntimeData = entry.runtime_data
    return runtime


def can_write(runtime: QuartermasterRuntimeData, user: User | None) -> bool:
    """Return whether a user may change the inventory from the dashboard."""
    if user is None or user.is_admin:
        return True
    return bool(
        runtime.coordinator.config_entry.options.get(
            CONF_NON_ADMIN_WRITES, DEFAULT_NON_ADMIN_WRITES
        )
    )


ENTITY_KEYS: tuple[tuple[str, str], ...] = (
    ("todo", "shopping_list"),
    ("sensor", "to_buy"),
    ("sensor", "suggestions"),
    ("sensor", "expiring"),
    ("sensor", "pending_proposals"),
    ("sensor", "analyses_today"),
    ("button", "start_shopping"),
    ("button", "undo_last"),
    ("select", "update_mode"),
    ("calendar", "expiry"),
    ("sensor", "photo_requests"),
)


def entity_ids(hass: HomeAssistant, entry_id: str) -> dict[str, str | None]:
    """Return the entity id of each entity by key (names depend on language)."""
    registry = er.async_get(hass)
    return {
        key: registry.async_get_entity_id(domain, DOMAIN, f"{entry_id}_{key}")
        for domain, key in ENTITY_KEYS
    }


def snapshot(runtime: QuartermasterRuntimeData) -> dict[str, Any]:
    """Return everything the cards show, as plain JSON."""
    data = runtime.coordinator.data
    analysis = data.analysis
    entry = runtime.coordinator.config_entry
    show = runtime.show
    return {
        "api_version": API_VERSION,
        "entry_id": entry.entry_id,
        "entities": entity_ids(runtime.coordinator.hass, entry.entry_id),
        "zones": [
            {
                "zone_id": zone.id,
                "name": zone.name,
                "aliases": zone.aliases,
                "sources": [source.get("type") for source in zone.sources],
                "updated": data.zone_updates[zone.id].isoformat()
                if zone.id in data.zone_updates
                else None,
                "last_check": data.zone_checks.get(zone.id),
                "in_error": data.zone_failures.get(zone.id, 0) >= ZONE_ERROR_FAILURES,
                "show_armed_until": show.armed_until[zone.id].isoformat()
                if show.is_armed(zone.id)
                else None,
                "last_show": show.last_show.get(zone.id),
            }
            for zone in data.zones.values()
        ],
        "photo_requests": [
            {
                "request_id": request.id,
                "zone_id": request.zone_id,
                "reason": request.reason,
                "created": request.created.isoformat(),
            }
            for request in data.photo_requests
        ],
        "products": [
            {
                "product_id": product.id,
                "name": product.name,
                "aliases": product.aliases,
                "category": product.category,
                "unit": product.unit,
                "default_zone_id": product.default_zone_id,
                "essential": product.essential,
                "min_stock": product.min_stock,
                "target_stock": product.target_stock,
                "total": round(analysis.totals.get(product.id, 0.0), 3),
                "stock": [
                    {
                        "zone_id": level.zone_id,
                        "quantity": level.quantity,
                        "best_before": level.best_before.isoformat()
                        if level.best_before
                        else None,
                        "updated": level.updated_at.isoformat()
                        if level.updated_at
                        else None,
                    }
                    for level in data.stock
                    if level.product_id == product.id
                ],
            }
            for product in data.products.values()
        ],
        "to_buy": [
            {"product_id": e.product.id, "have": e.total, "quantity": e.quantity}
            for e in analysis.to_buy
        ],
        "suggestions": [
            {
                "product_id": e.product.id,
                "have": e.total,
                "reason": e.reason.value,
                **e.details,
            }
            for e in analysis.suggestions
        ],
        "expiring": [
            {
                "product_id": e.product.id,
                "zone_id": e.zone.id,
                "quantity": e.quantity,
                "best_before": e.best_before.isoformat(),
                "days_left": e.days_left,
            }
            for e in analysis.expiring
        ],
        "shopping_list": [
            {
                "item_id": item.id,
                "text": item.text,
                "product_id": item.product_id,
                "quantity": item.quantity,
                "origin": item.origin.value,
                "done": item.status is ShoppingStatus.COMPLETED,
            }
            for item in data.shopping_items
        ],
        "proposals": [
            {
                "proposal_id": proposal.id,
                "zone_id": proposal.zone_id,
                "channel": proposal.channel.value,
                "created": proposal.created.isoformat(),
                "items": proposal.payload["items"],
                "not_seen": proposal.payload.get("not_seen", []),
            }
            for proposal in data.proposals
        ],
        "shopping_mode_until": data.shopping_until.isoformat()
        if data.shopping_mode and data.shopping_until
        else None,
        "analyses_today": data.analyses_today,
    }


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register the commands."""
    websocket_api.async_register_command(hass, ws_snapshot)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_journal)
    websocket_api.async_register_command(hass, ws_upload_photo)


def _loaded(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg_id: int
) -> QuartermasterRuntimeData | None:
    try:
        return _runtime(hass)
    except HomeAssistantError:
        connection.send_error(msg_id, "not_loaded", "Quartermaster is not loaded")
        return None


@websocket_api.websocket_command({probatio.Required("type"): f"{DOMAIN}/snapshot"})
@callback
def ws_snapshot(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the current snapshot and whether the user may write."""
    if (runtime := _loaded(hass, connection, msg["id"])) is None:
        return
    connection.send_result(
        msg["id"],
        snapshot(runtime) | {"can_write": can_write(runtime, connection.user)},
    )


@websocket_api.websocket_command({probatio.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Send the snapshot now and after every recalculation."""
    if (runtime := _loaded(hass, connection, msg["id"])) is None:
        return
    writer = can_write(runtime, connection.user)

    @callback
    def send() -> None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"], snapshot(runtime) | {"can_write": writer}
            )
        )

    connection.subscriptions[msg["id"]] = runtime.coordinator.async_add_listener(send)
    connection.send_result(msg["id"])
    send()


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/journal",
        probatio.Optional("limit", default=50): probatio.All(
            int, probatio.Range(min=1, max=JOURNAL_MAX)
        ),
        probatio.Optional("product_id"): int,
    }
)
@websocket_api.async_response
async def ws_journal(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the newest movements with product and zone names."""
    if (runtime := _loaded(hass, connection, msg["id"])) is None:
        return
    entries = await runtime.inventory.async_list_journal(
        limit=msg["limit"], product_id=msg.get("product_id")
    )
    undone = {entry.undoes_id for entry in entries if entry.undoes_id}
    products = {
        p.id: p
        for p in await runtime.inventory.async_list_products(include_archived=True)
    }
    zones = {zone.id: zone for zone in await runtime.inventory.async_list_zones()}
    connection.send_result(
        msg["id"],
        {
            "entries": [
                {
                    "id": entry.id,
                    "operation_id": entry.operation_id,
                    "ts": entry.ts.isoformat(),
                    "product_id": entry.product_id,
                    "product": products[entry.product_id].name
                    if entry.product_id in products
                    else None,
                    "zone_id": entry.zone_id,
                    "zone": zones[entry.zone_id].name
                    if entry.zone_id in zones
                    else None,
                    "kind": entry.kind.value,
                    "delta": entry.delta,
                    "quantity_after": entry.quantity_after,
                    "channel": entry.channel.value,
                    "undone": entry.id in undone,
                }
                for entry in entries
            ]
        },
    )


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/upload_photo",
        probatio.Required("zone"): str,
        probatio.Required("image"): str,
    }
)
@websocket_api.async_response
async def ws_upload_photo(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Analyze a photo resized in the browser (base64 JPEG, at most 2 MB)."""
    if (runtime := _loaded(hass, connection, msg["id"])) is None:
        return
    if not can_write(runtime, connection.user):
        raise Unauthorized(context=connection.context(msg))
    try:
        data = base64.b64decode(msg["image"], validate=True)
    except binascii.Error:
        connection.send_error(msg["id"], "invalid_image", "Not base64")
        return
    if len(data) > MAX_UPLOAD_BYTES:
        connection.send_error(msg["id"], "too_large", "Images are limited to 2 MB")
        return
    try:
        result = await async_photo_pipeline(
            runtime,
            data,
            msg["zone"],
            channel=Channel.DASHBOARD,
            user_id=connection.user.id if connection.user else None,
        )
    except InventoryError as err:
        connection.send_error(
            msg["id"],
            err.translation_key or "error",
            str(err),
            translation_key=err.translation_key,
            translation_domain=DOMAIN,
            translation_placeholders=err.translation_placeholders,
        )
        return
    connection.send_result(msg["id"], result)
