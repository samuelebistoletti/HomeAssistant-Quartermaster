"""Tests for the shopping list, its entities and the Phase 2 actions."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.inventory import (
    InventoryService,
    StockOperation,
)
from custom_components.quartermaster.storage.models import Channel

TODO = "todo.quartermaster_shopping_list"
TO_BUY = "sensor.quartermaster_to_buy"
SUGGESTIONS = "sensor.quartermaster_shopping_suggestions"
EXPIRING = "sensor.quartermaster_expiring_products"


async def _qm(
    hass: HomeAssistant, service: str, return_response: bool = True, **data: Any
) -> Any:
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=return_response
    )


async def _items(hass: HomeAssistant, entity_id: str = TODO) -> list[dict[str, Any]]:
    response = await hass.services.async_call(
        TODO_DOMAIN,
        "get_items",
        {"status": ["needs_action", "completed"]},
        target={"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    items: list[dict[str, Any]] = response[entity_id]["items"]  # type: ignore[index, assignment]
    return items


async def _todo(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(
        TODO_DOMAIN, service, data, target={"entity_id": TODO}, blocking=True
    )


async def _refresh(hass: HomeAssistant) -> None:
    await _qm(hass, "refresh_shopping_list", return_response=False)


@pytest.fixture
async def inventory(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> InventoryService:
    """Return the inventory of the loaded entry, with two zones and products."""
    inventory: InventoryService = init_integration.runtime_data.inventory
    await inventory.async_add_zone("Fridge")
    await inventory.async_add_zone("Pantry")
    await inventory.async_add_product(
        "Milk", aliases=["latte"], default_zone="fridge", unit="bottle"
    )
    await inventory.async_add_product("Coffee", default_zone="pantry", unit="pack")
    await inventory.async_add_product("Jam", default_zone="pantry")
    await _refresh(hass)
    return inventory


async def test_entities_are_created(
    hass: HomeAssistant, inventory: InventoryService
) -> None:
    """Every Phase 2 entity exists with its translated name."""
    for entity_id in (
        TODO,
        TO_BUY,
        SUGGESTIONS,
        EXPIRING,
        "button.quartermaster_go_shopping",
        "button.quartermaster_undo_last_operation",
        "select.quartermaster_update_mode",
        "calendar.quartermaster_best_before_dates",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
    assert hass.states.get(TODO).state == "0"  # type: ignore[union-attr]


async def test_essential_below_minimum_goes_on_the_list_once(
    hass: HomeAssistant, inventory: InventoryService
) -> None:
    """An essential below minimum is added once, updated, then removed when bought."""
    await _qm(hass, "set_essential", product="milk", min_stock=2, target_stock=4)
    await _refresh(hass)
    [item] = await _items(hass)
    assert (item["summary"], item["description"]) == ("Milk", "4 bottle")
    state = hass.states.get(TO_BUY)
    assert state is not None
    assert state.state == "1"
    assert state.attributes["items"][0]["quantity"] == 4

    await inventory.async_set_stock("milk", 1, channel=Channel.SERVICE)
    await _refresh(hass)
    [item] = await _items(hass)
    assert item["description"] == "3 bottle"

    # Deleted by hand, it comes back while still below the minimum.
    await _todo(hass, "remove_item", item=item["uid"])
    await _refresh(hass)
    assert len(await _items(hass)) == 1

    await inventory.async_set_stock("milk", 3, channel=Channel.SERVICE)
    await _refresh(hass)
    assert await _items(hass) == []
    assert hass.states.get(TO_BUY).state == "0"  # type: ignore[union-attr]


async def test_inventory_writes_refresh_the_list(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, inventory: InventoryService
) -> None:
    """A write through the inventory updates the list without asking."""
    await inventory.async_set_essential("coffee", essential=True, min_stock=1)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [item["summary"] for item in await _items(hass)] == ["Coffee"]


async def test_manual_items_and_checking_off(
    hass: HomeAssistant, init_integration: MockConfigEntry, inventory: InventoryService
) -> None:
    """Manual items match products; checking records a purchase in shopping mode."""
    await _todo(hass, "add_item", item="latte")
    await _todo(hass, "add_item", item="Birthday candles", description="blue")
    items = await _items(hass)
    assert [(i["summary"], i.get("description")) for i in items] == [
        ("latte", None),
        ("Birthday candles", "blue"),
    ]

    # Outside shopping mode (option off) checking does not record a purchase.
    await _todo(hass, "update_item", item=items[0]["uid"], status="completed")
    assert await inventory.async_get_stock() == []
    await _todo(hass, "update_item", item=items[0]["uid"], status="needs_action")

    response = await _qm(hass, "start_shopping")
    assert response["shopping_mode_until"] is not None
    await _todo(hass, "update_item", item=items[0]["uid"], status="completed")
    [level] = await inventory.async_get_stock()
    assert (level.zone_id, level.quantity) == ("fridge", 1)
    journal = await inventory.async_list_journal()
    assert journal[0].channel is Channel.DASHBOARD

    # Free text has no product: checking it records nothing.
    await _todo(hass, "update_item", item=items[1]["uid"], status="completed")
    assert len(await inventory.async_list_journal()) == 1

    await _todo(hass, "update_item", item=items[1]["uid"], rename="Candles")
    await _todo(hass, "remove_completed_items")
    assert await _items(hass) == []


async def test_purchase_without_zone_still_checks_the_item(
    hass: HomeAssistant, inventory: InventoryService
) -> None:
    """A product with no zone to buy into is checked off without a purchase."""
    await inventory.async_add_product("Bread")
    await _qm(hass, "start_shopping", return_response=False)
    await _todo(hass, "add_item", item="Bread")
    [item] = await _items(hass)
    await _todo(hass, "update_item", item=item["uid"], status="completed")
    assert (await _items(hass))[0]["status"] == "completed"
    assert await inventory.async_get_stock() == []


async def test_rename_and_move(
    hass: HomeAssistant, inventory: InventoryService
) -> None:
    """Renaming re-matches the product; moving changes the order."""
    await _qm(hass, "set_essential", product="milk", min_stock=1)
    await _refresh(hass)
    await _todo(hass, "add_item", item="Bread")
    await _todo(hass, "add_item", item="Jam")
    first, second, third = await _items(hass)
    await _todo(hass, "update_item", item=first["uid"], rename="Coffee")
    await _refresh(hass)

    entity = hass.data["entity_components"]["todo"].get_entity(TODO)
    await entity.async_move_todo_item(third["uid"], None)
    await entity.async_move_todo_item(first["uid"], second["uid"])
    summaries = [item["summary"] for item in await _items(hass)]
    # The renamed item is now a manual Coffee item; Milk is added again.
    assert summaries[:3] == ["Jam", "Bread", "Coffee"]
    assert "Milk" in summaries
    with pytest.raises(ServiceValidationError):
        await entity.async_move_todo_item("999", None)


async def test_add_to_shopping_list(
    hass: HomeAssistant, inventory: InventoryService
) -> None:
    """Products are added once; free text is accepted; one of the two is required."""
    item = await _qm(
        hass, "add_to_shopping_list", product="coffee", quantity=2, from_suggestion=True
    )
    assert (item["text"], item["quantity"], item["origin"]) == (
        "Coffee",
        2,
        "suggestion",
    )
    again = await _qm(hass, "add_to_shopping_list", product="Coffee", quantity=3)
    assert (again["item_id"], again["quantity"]) == (item["item_id"], 3)
    same = await _qm(hass, "add_to_shopping_list", text="coffee")
    assert same["item_id"] == item["item_id"]
    text = await _qm(hass, "add_to_shopping_list", text="Batteries")
    assert (text["product_id"], text["origin"]) == (None, "manual")
    await _qm(hass, "add_to_shopping_list", return_response=False, text="Tape")
    assert len(await _items(hass)) == 3

    with pytest.raises(ServiceValidationError):
        await _qm(hass, "add_to_shopping_list", text="   ")
    with pytest.raises(Exception, match="at least one"):
        await _qm(hass, "add_to_shopping_list", quantity=1)


async def test_get_suggestions(
    hass: HomeAssistant, inventory: InventoryService
) -> None:
    """The response holds to-buy, suggestions with reasons and the open list."""
    await _qm(hass, "set_essential", product="milk", min_stock=1, target_stock=2)
    await _qm(hass, "set_essential", product="coffee", min_stock=1, target_stock=3)
    await inventory.async_set_stock("coffee", 1, channel=Channel.SERVICE)
    await inventory.async_set_stock("jam", 1, channel=Channel.SERVICE)
    await inventory.async_adjust_stock(
        "jam", StockOperation.FINISH, channel=Channel.SERVICE
    )

    response = await _qm(hass, "get_suggestions")
    assert [entry["product"] for entry in response["to_buy"]] == ["Milk"]
    assert [(s["product"], s["reason"]) for s in response["suggestions"]] == [
        ("Coffee", "below_target"),
        ("Jam", "finished_recently"),
    ]
    assert [item["text"] for item in response["shopping_list"]] == ["Milk"]
    assert response["shopping_mode_until"] is None
    state = hass.states.get(SUGGESTIONS)
    assert state is not None
    assert state.state == "2"
    assert state.attributes["items"][0]["target"] == 3


async def test_expiry_sensor_and_calendar(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, inventory: InventoryService
) -> None:
    """Best-before dates feed the expiring sensor and the calendar."""
    freezer.move_to("2026-11-08 10:00:00+00:00")
    await inventory.async_set_stock(
        "milk", 2, channel=Channel.SERVICE, best_before=date(2026, 11, 10)
    )
    await inventory.async_set_stock(
        "coffee", 1, channel=Channel.SERVICE, best_before=date(2027, 3, 1)
    )
    await _refresh(hass)
    state = hass.states.get(EXPIRING)
    assert state is not None
    assert state.state == "1"
    assert state.attributes["items"][0]["days_left"] == 2

    calendar = hass.states.get("calendar.quartermaster_best_before_dates")
    assert calendar is not None
    assert calendar.attributes["message"] == "Milk"
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "start_date_time": "2026-11-01 00:00:00",
            "end_date_time": "2027-12-31 00:00:00",
        },
        target={"entity_id": "calendar.quartermaster_best_before_dates"},
        blocking=True,
        return_response=True,
    )
    events = response["calendar.quartermaster_best_before_dates"]["events"]  # type: ignore[index]
    assert [(e["summary"], e["start"], e["location"]) for e in events] == [  # type: ignore[union-attr]
        ("Milk", "2026-11-10", "Fridge"),
        ("Coffee", "2027-03-01", "Pantry"),
    ]

    await inventory.async_set_stock("milk", 0, channel=Channel.SERVICE)
    await inventory.async_set_stock("coffee", 0, channel=Channel.SERVICE)
    await _refresh(hass)
    calendar = hass.states.get("calendar.quartermaster_best_before_dates")
    assert calendar is not None
    assert calendar.state == "off"


async def test_buttons(hass: HomeAssistant, inventory: InventoryService) -> None:
    """Go shopping turns shopping mode on; undo reverts the last operation."""
    await inventory.async_set_stock("milk", 2, channel=Channel.SERVICE)
    await hass.services.async_call(
        "button",
        "press",
        target={"entity_id": "button.quartermaster_undo_last_operation"},
        blocking=True,
    )
    assert (await inventory.async_get_stock())[0].quantity == 0
    await hass.services.async_call(
        "button",
        "press",
        target={"entity_id": "button.quartermaster_go_shopping"},
        blocking=True,
    )
    response = await _qm(hass, "get_suggestions")
    assert response["shopping_mode_until"] is not None


async def test_shopping_mode_expires(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, inventory: InventoryService
) -> None:
    """Shopping mode ends after the configured time."""
    await _qm(hass, "start_shopping", return_response=False)
    freezer.tick(timedelta(minutes=121))
    response = await _qm(hass, "get_suggestions")
    assert response["shopping_mode_until"] is None


async def test_update_mode_select_persists(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The update mode is stored and survives a reload."""
    entity_id = "select.quartermaster_update_mode"
    assert hass.states.get(entity_id).state == "confirm"  # type: ignore[union-attr]
    await hass.services.async_call(
        "select",
        "select_option",
        {"option": "automatic"},
        target={"entity_id": entity_id},
        blocking=True,
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "automatic"  # type: ignore[union-attr]


async def _local_list(hass: HomeAssistant) -> str:
    """Set up Home Assistant's own shopping list as an external todo list."""
    entry = MockConfigEntry(domain="shopping_list")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return "todo.shopping_list"


async def _set_options(
    hass: HomeAssistant, entry: MockConfigEntry, **options: Any
) -> None:
    hass.config_entries.async_update_entry(entry, options={**entry.options, **options})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_external_copy(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Open items are copied one way; completions and deletions follow."""
    external = await _local_list(hass)
    await hass.services.async_call(
        TODO_DOMAIN,
        "add_item",
        {"item": "Own item"},
        target={"entity_id": external},
        blocking=True,
    )
    await _set_options(hass, init_integration, external_todo=external)
    assert init_integration.state is ConfigEntryState.LOADED

    await _qm(hass, "add_to_shopping_list", text="Bread")
    await _qm(hass, "add_to_shopping_list", text="Eggs")
    assert [i["summary"] for i in await _items(hass, external)] == [
        "Own item",
        "Bread",
        "Eggs",
    ]
    bread, eggs = await _items(hass)

    await _todo(hass, "update_item", item=bread["uid"], status="completed")
    await _refresh(hass)
    statuses = {i["summary"]: i["status"] for i in await _items(hass, external)}
    assert statuses == {
        "Own item": "needs_action",
        "Bread": "completed",
        "Eggs": "needs_action",
    }

    await _todo(hass, "remove_item", item=eggs["uid"])
    assert [i["summary"] for i in await _items(hass, external)] == ["Own item", "Bread"]

    # A missing external list only logs a warning.
    await _set_options(hass, init_integration, external_todo="todo.gone")
    await _qm(hass, "add_to_shopping_list", text="Milk")
    assert init_integration.state is ConfigEntryState.LOADED


async def test_import_from_todo(
    hass: HomeAssistant, inventory: InventoryService
) -> None:
    """Preview first, then apply, for each target."""
    external = await _local_list(hass)
    for text in ("Milk", "Rice", "rice", "  "):
        if text.strip():
            await hass.services.async_call(
                TODO_DOMAIN,
                "add_item",
                {"item": text},
                target={"entity_id": external},
                blocking=True,
            )

    preview = await _qm(
        hass, "import_from_todo", entity_id=external, target="essentials"
    )
    assert preview == {
        "applied": False,
        "items": [
            {"summary": "Milk", "action": "set_essential", "product": "Milk"},
            {"summary": "Rice", "action": "create_product", "product": "Rice"},
        ],
    }
    assert len(await inventory.async_list_products()) == 3

    await _qm(
        hass,
        "import_from_todo",
        entity_id=external,
        target="essentials",
        min_stock=2,
        apply=True,
    )
    rice = await inventory.async_find_product("rice")
    milk = await inventory.async_find_product("milk")
    assert (rice.essential, rice.min_stock, milk.essential, milk.min_stock) == (
        True,
        2,
        True,
        2,
    )

    again = await _qm(hass, "import_from_todo", entity_id=external, target="essentials")
    assert {item["action"] for item in again["items"]} == {"skip"}

    products = await _qm(
        hass, "import_from_todo", entity_id=external, target="products"
    )
    assert {item["action"] for item in products["items"]} == {"skip"}

    await _qm(
        hass,
        "import_from_todo",
        return_response=False,
        entity_id=external,
        target="shopping",
        apply=True,
    )
    assert {i["summary"] for i in await _items(hass)} >= {"Milk", "Rice"}

    with pytest.raises(ServiceValidationError) as err:
        await _qm(hass, "import_from_todo", entity_id="todo.missing", target="products")
    assert err.value.translation_key == "todo_unreadable"


async def test_options_flow(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Options are shown with defaults, saved, and the entry reloads."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "record_purchase_on_check": True,
            "shopping_mode_minutes": 30,
            "stale_zone_days": 5,
            "expiring_days": 2,
        },
    )
    await hass.async_block_till_done()
    assert init_integration.options["shopping_mode_minutes"] == 30
    assert init_integration.state is ConfigEntryState.LOADED
