"""Tests for the dashboard websocket commands and write permissions."""

from __future__ import annotations

import base64
from typing import Any

from homeassistant.auth.models import User
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.quartermaster.const import CONF_WRITE_USERS, DOMAIN
from custom_components.quartermaster.core.inventory import InventoryService
from custom_components.quartermaster.storage.models import Channel


@pytest.fixture
async def stocked(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> InventoryService:
    """Fridge with milk (essential, below minimum) and jam."""
    inventory: InventoryService = init_integration.runtime_data.inventory
    await inventory.async_add_zone("Fridge")
    await inventory.async_add_product(
        "Milk", default_zone="fridge", unit="bottle", essential=True, min_stock=2
    )
    await inventory.async_add_product("Jam", default_zone="fridge")
    await inventory.async_set_stock("milk", 1, channel=Channel.SERVICE)
    await init_integration.runtime_data.coordinator.async_refresh()
    return inventory


async def test_snapshot_and_subscribe(
    hass: HomeAssistant, hass_ws_client: Any, stocked: InventoryService
) -> None:
    """The snapshot holds zones, products, list and derived data; updates push."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/snapshot"})
    result = (await client.receive_json())["result"]
    assert result["api_version"] == 1
    assert result["can_write"] is True
    assert result["entities"]["shopping_list"] == "todo.quartermaster_shopping_list"
    assert result["entities"]["to_buy"] == "sensor.quartermaster_to_buy"
    assert [z["zone_id"] for z in result["zones"]] == ["fridge"]
    milk = next(p for p in result["products"] if p["name"] == "Milk")
    assert (milk["total"], milk["stock"][0]["quantity"]) == (1, 1)
    assert result["to_buy"] == [
        {"product_id": milk["product_id"], "have": 1.0, "quantity": 1.0}
    ]
    assert [item["text"] for item in result["shopping_list"]] == ["Milk"]

    await client.send_json_auto_id({"type": f"{DOMAIN}/subscribe"})
    assert (await client.receive_json())["success"] is True
    first = await client.receive_json()
    assert first["type"] == "event"
    await stocked.async_set_stock("milk", 3, channel=Channel.SERVICE)
    await hass.config_entries.async_entries(DOMAIN)[
        0
    ].runtime_data.coordinator.async_refresh()
    update = await client.receive_json()
    assert update["event"]["to_buy"] == []


async def test_journal(
    hass: HomeAssistant, hass_ws_client: Any, stocked: InventoryService
) -> None:
    """Movements come with names, and undone ones are marked."""
    await stocked.async_set_stock("jam", 2, channel=Channel.DASHBOARD)
    _, [undo] = await stocked.async_undo_last(channel=Channel.SERVICE)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/journal", "limit": 10})
    entries = (await client.receive_json())["result"]["entries"]
    assert [(e["kind"], e["product"], e["undone"]) for e in entries] == [
        ("undo", "Jam", False),
        ("inventory", "Jam", True),
        ("inventory", "Milk", False),
    ]
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/journal", "product_id": undo.product.id}
    )
    assert len((await client.receive_json())["result"]["entries"]) == 2


async def test_upload_photo_errors(
    hass: HomeAssistant, hass_ws_client: Any, stocked: InventoryService
) -> None:
    """Bad base64, oversized and unanalyzable images are refused."""
    client = await hass_ws_client(hass)
    for image, code in (
        ("***", "invalid_image"),
        (base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode(), "too_large"),
        (base64.b64encode(b"not a jpeg").decode(), "image_rejected"),
    ):
        await client.send_json_auto_id(
            {"type": f"{DOMAIN}/upload_photo", "zone": "fridge", "image": image}
        )
        response = await client.receive_json()
        assert response["success"] is False
        assert response["error"]["code"] == code


async def test_non_admin_cannot_write(
    hass: HomeAssistant,
    hass_ws_client: Any,
    hass_read_only_access_token: str,
    hass_read_only_user: User,
    hass_admin_user: User,
    init_integration: MockConfigEntry,
    stocked: InventoryService,
) -> None:
    """Non-admins read but cannot write, unless they are chosen in the options."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": f"{DOMAIN}/snapshot"})
    assert (await client.receive_json())["result"]["can_write"] is False
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/upload_photo", "zone": "fridge", "image": "eA=="}
    )
    assert (await client.receive_json())["error"]["code"] == "unauthorized"
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "adjust_stock",
            {"product": "milk"},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )

    # The permissions step lists the non-admin users; the chosen ones may write.
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "permissions"}
    )
    field = result["data_schema"].schema[CONF_WRITE_USERS]
    users = [option["value"] for option in field.config["options"]]
    assert hass_read_only_user.id in users
    assert hass_admin_user.id not in users
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WRITE_USERS: [hass_read_only_user.id]}
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        DOMAIN,
        "adjust_stock",
        {"product": "milk"},
        blocking=True,
        context=Context(user_id=hass_read_only_user.id),
    )


async def test_undo_specific_operation(
    hass: HomeAssistant, stocked: InventoryService
) -> None:
    """The undo action can revert a given operation from the journal."""
    first = await stocked.async_set_stock("jam", 2, channel=Channel.SERVICE)
    await stocked.async_set_stock("milk", 5, channel=Channel.SERVICE)
    response = await hass.services.async_call(
        DOMAIN,
        "undo_last",
        {"operation_id": first.entry.operation_id},
        blocking=True,
        return_response=True,
    )
    assert [m["product"] for m in response["undone"]] == ["Jam"]  # type: ignore[index, union-attr]


async def test_not_loaded(
    hass: HomeAssistant, hass_ws_client: Any, init_integration: MockConfigEntry
) -> None:
    """Commands answer an error when the entry is not loaded."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    client = await hass_ws_client(hass)
    for command in ("snapshot", "subscribe", "journal"):
        await client.send_json_auto_id({"type": f"{DOMAIN}/{command}"})
        assert (await client.receive_json())["error"]["code"] == "not_loaded"
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/upload_photo", "zone": "x", "image": "eA=="}
    )
    assert (await client.receive_json())["error"]["code"] == "not_loaded"
