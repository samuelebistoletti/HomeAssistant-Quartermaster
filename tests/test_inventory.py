"""Tests for the inventory service."""

from __future__ import annotations

from datetime import date, timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest

from custom_components.quartermaster.core.inventory import (
    InventoryError,
    InventoryService,
    StockOperation,
)
from custom_components.quartermaster.storage.models import (
    Channel,
    MovementKind,
    StockChange,
)


async def _quantity(inventory: InventoryService, product: str, zone: str) -> float:
    found = await inventory.async_find_product(product)
    levels = await inventory.async_get_stock(product_id=found.id, zone_id=zone)
    return levels[0].quantity if levels else 0.0


async def _kitchen(inventory: InventoryService) -> None:
    await inventory.async_add_zone("Fridge", aliases=["frigo"])
    await inventory.async_add_zone("Pantry", aliases=["dispensa"])
    await inventory.async_add_product(
        "Milk", aliases=["latte"], default_zone="frigo", unit="bottle"
    )
    await inventory.async_add_product("Parmesan", category="cheese", aliases=["cheese"])
    await inventory.async_add_product(
        "Mozzarella", category="cheese", aliases=["cheese"]
    )


def _key(err: pytest.ExceptionInfo[InventoryError]) -> str | None:
    return err.value.translation_key


# Zones


async def test_add_and_find_zones(inventory: InventoryService) -> None:
    """Zones are found by id, name or alias, ignoring case and spaces."""
    zone = await inventory.async_add_zone("  Garage   shelf ", aliases=["garage"])
    assert (zone.id, zone.name) == ("garage_shelf", "Garage shelf")
    for ref in ("garage_shelf", "garage SHELF", "Garage"):
        assert (await inventory.async_find_zone(ref)).id == "garage_shelf"
    assert [z.id for z in await inventory.async_list_zones()] == ["garage_shelf"]


async def test_zone_errors(inventory: InventoryService) -> None:
    """Duplicate, unknown, ambiguous and empty zone names are refused."""
    await inventory.async_add_zone("Fridge", aliases=["cold"])
    await inventory.async_add_zone("Freezer", aliases=["cold"])
    with pytest.raises(InventoryError) as err:
        await inventory.async_add_zone("fridge")
    assert _key(err) == "zone_exists"
    with pytest.raises(InventoryError) as err:
        await inventory.async_add_zone("Other", zone_id="fridge")
    assert _key(err) == "zone_exists"
    with pytest.raises(InventoryError) as err:
        await inventory.async_add_zone("   ")
    assert _key(err) == "invalid_name"
    with pytest.raises(InventoryError) as err:
        await inventory.async_find_zone("cold")
    assert _key(err) == "ambiguous_zone"
    assert err.value.translation_placeholders == {
        "zone": "cold",
        "candidates": "Freezer, Fridge",
    }
    with pytest.raises(InventoryError) as err:
        await inventory.async_find_zone("cellar")
    assert _key(err) == "zone_not_found"


async def test_remove_zone(inventory: InventoryService) -> None:
    """A zone is removed only when it holds nothing."""
    await _kitchen(inventory)
    await inventory.async_adjust_stock(
        "milk", StockOperation.PURCHASE, channel=Channel.SERVICE
    )
    with pytest.raises(InventoryError) as err:
        await inventory.async_remove_zone("fridge")
    assert _key(err) == "zone_not_empty"

    await inventory.async_adjust_stock(
        "milk", StockOperation.FINISH, channel=Channel.SERVICE
    )
    await inventory.async_remove_zone("fridge")
    assert [z.id for z in await inventory.async_list_zones()] == ["pantry"]


# Products and essentials


async def test_add_and_find_products(inventory: InventoryService) -> None:
    """Products are found by id, name or a unique alias."""
    await _kitchen(inventory)
    milk = await inventory.async_find_product("LATTE")
    assert milk.default_zone_id == "fridge"
    assert (await inventory.async_find_product(milk.id)).name == "Milk"
    assert (await inventory.async_find_product(str(milk.id))).name == "Milk"
    assert (await inventory.async_find_product(" milk ")).id == milk.id
    assert [p.name for p in await inventory.async_list_products()] == [
        "Milk",
        "Mozzarella",
        "Parmesan",
    ]


async def test_product_errors(inventory: InventoryService) -> None:
    """Duplicates, unknown, ambiguous and invalid products are refused."""
    await _kitchen(inventory)
    cases = {
        "product_exists": inventory.async_add_product("milk"),
        "invalid_name": inventory.async_add_product(""),
        "zone_not_found": inventory.async_add_product("Eggs", default_zone="cellar"),
        "min_stock_required": inventory.async_add_product("Coffee", essential=True),
        "target_below_min": inventory.async_add_product(
            "Coffee", essential=True, min_stock=3, target_stock=2
        ),
        "invalid_quantity": inventory.async_add_product(
            "Coffee", essential=True, min_stock=-1
        ),
        "ambiguous_product": inventory.async_find_product("cheese"),
        "product_not_found": inventory.async_find_product("bread"),
    }
    for key, call in cases.items():
        with pytest.raises(InventoryError) as err:
            await call
        assert _key(err) == key
    with pytest.raises(InventoryError) as err:
        await inventory.async_find_product(999)
    assert _key(err) == "product_not_found"


async def test_archived_products_are_not_found(inventory: InventoryService) -> None:
    """Archived products are hidden from resolution and listing."""
    await _kitchen(inventory)
    milk = await inventory.async_find_product("milk")
    milk.archived = True
    await inventory.backend.async_update_product(milk)
    with pytest.raises(InventoryError):
        await inventory.async_find_product("milk")
    assert len(await inventory.async_list_products(include_archived=True)) == 3
    # The name is free again for a new product.
    await inventory.async_add_product("Milk")


async def test_set_essential(inventory: InventoryService) -> None:
    """Essentials need a minimum stock; earlier levels are kept when omitted."""
    await _kitchen(inventory)
    with pytest.raises(InventoryError) as err:
        await inventory.async_set_essential("milk", essential=True)
    assert _key(err) == "min_stock_required"

    milk = await inventory.async_set_essential(
        "milk", essential=True, min_stock=1, target_stock=4
    )
    assert (milk.essential, milk.min_stock, milk.target_stock) == (True, 1, 4)
    milk = await inventory.async_set_essential("milk", essential=True, min_stock=2)
    assert (milk.min_stock, milk.target_stock) == (2, 4)
    with pytest.raises(InventoryError) as err:
        await inventory.async_set_essential("milk", essential=True, min_stock=5)
    assert _key(err) == "target_below_min"

    milk = await inventory.async_set_essential("milk", essential=False)
    assert (milk.essential, milk.min_stock, milk.target_stock) == (False, None, None)
    assert not (await inventory.async_find_product("milk")).essential


# Movements


async def test_purchase(inventory: InventoryService) -> None:
    """Purchases go to the given zone, the default zone or the only known zone."""
    await _kitchen(inventory)
    movement = await inventory.async_adjust_stock(
        "milk",
        StockOperation.PURCHASE,
        quantity=2,
        best_before=date(2026, 11, 20),
        channel=Channel.SERVICE,
        user_id="user-1",
    )
    assert movement.zone.id == "fridge"
    assert movement.entry.kind is MovementKind.PURCHASE
    assert (movement.entry.delta, movement.entry.quantity_after) == (2, 2)
    assert movement.entry.user_id == "user-1"

    await inventory.async_adjust_stock(
        "parmesan",
        StockOperation.PURCHASE,
        zone_ref="dispensa",
        channel=Channel.SERVICE,
    )
    # No default zone, but the product is known in one zone only.
    again = await inventory.async_adjust_stock(
        "parmesan", StockOperation.PURCHASE, channel=Channel.SERVICE
    )
    assert (again.zone.id, again.entry.quantity_after) == ("pantry", 2)

    with pytest.raises(InventoryError) as err:
        await inventory.async_adjust_stock(
            "mozzarella", StockOperation.PURCHASE, channel=Channel.SERVICE
        )
    assert _key(err) == "zone_required"


async def test_consume_and_finish(inventory: InventoryService) -> None:
    """Consumption picks the only zone holding the product and never goes negative."""
    await _kitchen(inventory)
    with pytest.raises(InventoryError) as err:
        await inventory.async_adjust_stock(
            "milk", StockOperation.CONSUME, channel=Channel.SERVICE
        )
    assert _key(err) == "not_in_stock"

    await inventory.async_set_stock("milk", 3, channel=Channel.SERVICE)
    movement = await inventory.async_adjust_stock(
        "milk", StockOperation.CONSUME, channel=Channel.ALEXA
    )
    assert (movement.entry.delta, movement.entry.quantity_after) == (-1, 2)
    assert movement.entry.channel is Channel.ALEXA

    movement = await inventory.async_adjust_stock(
        "milk", StockOperation.CONSUME, quantity=5, channel=Channel.SERVICE
    )
    assert (movement.entry.delta, movement.entry.quantity_after) == (-2, 0)

    await inventory.async_set_stock(
        "milk", 1, zone_ref="fridge", channel=Channel.SERVICE
    )
    await inventory.async_set_stock(
        "milk", 2, zone_ref="pantry", channel=Channel.SERVICE
    )
    with pytest.raises(InventoryError) as err:
        await inventory.async_adjust_stock(
            "milk", StockOperation.FINISH, channel=Channel.SERVICE
        )
    assert _key(err) == "product_in_many_zones"
    assert err.value.translation_placeholders == {
        "product": "Milk",
        "candidates": "Fridge, Pantry",
    }

    finished = await inventory.async_adjust_stock(
        "milk", StockOperation.FINISH, zone_ref="pantry", channel=Channel.SERVICE
    )
    assert finished.entry.kind is MovementKind.CONSUME
    assert (finished.entry.delta, finished.entry.quantity_after) == (-2, 0)
    assert await _quantity(inventory, "milk", "fridge") == 1


async def test_set_stock(inventory: InventoryService) -> None:
    """Counting sets the quantity and journals the difference."""
    await _kitchen(inventory)
    movement = await inventory.async_set_stock(
        "milk", 2.5, channel=Channel.SERVICE, best_before=date(2026, 12, 1)
    )
    assert movement.entry.kind is MovementKind.INVENTORY
    assert (movement.entry.delta, movement.entry.quantity_after) == (2.5, 2.5)
    movement = await inventory.async_set_stock("milk", 1, channel=Channel.SERVICE)
    assert movement.entry.delta == -1.5
    [level] = await inventory.async_get_stock(product_id=movement.product.id)
    assert level.best_before == date(2026, 12, 1)

    for quantity in (-1, 100_001):
        with pytest.raises(InventoryError) as err:
            await inventory.async_set_stock("milk", quantity, channel=Channel.SERVICE)
        assert _key(err) == "invalid_quantity"
    with pytest.raises(InventoryError) as err:
        await inventory.async_adjust_stock(
            "milk", StockOperation.CONSUME, quantity=-1, channel=Channel.SERVICE
        )
    assert _key(err) == "invalid_quantity"


# Undo


async def test_every_movement_can_be_undone(inventory: InventoryService) -> None:
    """Undoing each movement in turn returns to an empty inventory."""
    await _kitchen(inventory)
    await inventory.async_adjust_stock(
        "milk", StockOperation.PURCHASE, quantity=4, channel=Channel.SERVICE
    )
    await inventory.async_adjust_stock(
        "milk", StockOperation.CONSUME, channel=Channel.SERVICE
    )
    await inventory.async_set_stock("milk", 10, channel=Channel.SERVICE)
    await inventory.async_adjust_stock(
        "milk", StockOperation.FINISH, channel=Channel.SERVICE
    )
    await inventory.async_adjust_stock(
        "parmesan",
        StockOperation.PURCHASE,
        quantity=0.5,
        zone_ref="pantry",
        channel=Channel.SERVICE,
    )

    expected = [0.5, 0, 10, 3, 4]
    after_undo = [0, 10, 3, 4, 0]
    for before, after in zip(expected, after_undo, strict=True):
        [original], [undo] = await inventory.async_undo_last(channel=Channel.SERVICE)
        assert original.entry.quantity_after == before
        assert undo.entry.kind is MovementKind.UNDO
        assert undo.entry.undoes_id == original.entry.id
        assert undo.entry.quantity_after == after
        assert undo.entry.delta == -original.entry.delta

    assert await _quantity(inventory, "milk", "fridge") == 0
    assert await _quantity(inventory, "parmesan", "pantry") == 0
    with pytest.raises(InventoryError) as err:
        await inventory.async_undo_last(channel=Channel.SERVICE)
    assert _key(err) == "nothing_to_undo"
    assert len(await inventory.async_list_journal()) == 10


async def test_undo_by_channel_and_user(inventory: InventoryService) -> None:
    """With only_channel, undo touches the caller's own movements only."""
    await _kitchen(inventory)
    await inventory.async_set_stock("milk", 2, channel=Channel.TELEGRAM, user_id="a")
    await inventory.async_set_stock("milk", 5, channel=Channel.ALEXA, user_id="b")

    [original], [undo] = await inventory.async_undo_last(
        channel=Channel.TELEGRAM, only_channel=True, user_id="a"
    )
    assert original.entry.channel is Channel.TELEGRAM
    assert (undo.entry.channel, undo.entry.user_id) == (Channel.TELEGRAM, "a")
    assert undo.entry.quantity_after == 3
    with pytest.raises(InventoryError):
        await inventory.async_undo_last(
            channel=Channel.TELEGRAM, only_channel=True, user_id="a"
        )


async def test_undo_window(
    inventory: InventoryService, freezer: FrozenDateTimeFactory
) -> None:
    """Movements older than the window are not undone."""
    await _kitchen(inventory)
    await inventory.async_set_stock("milk", 2, channel=Channel.ASSIST)
    freezer.tick(timedelta(minutes=11))
    with pytest.raises(InventoryError) as err:
        await inventory.async_undo_last(
            channel=Channel.ASSIST, within=timedelta(minutes=10)
        )
    assert _key(err) == "nothing_to_undo"
    await inventory.async_undo_last(
        channel=Channel.ASSIST, within=timedelta(minutes=20)
    )


async def test_undo_after_zone_removed(inventory: InventoryService) -> None:
    """A movement in a deleted zone cannot be undone."""
    await _kitchen(inventory)
    await inventory.async_set_stock(
        "milk", 0, zone_ref="pantry", channel=Channel.SERVICE
    )
    await inventory.async_remove_zone("pantry")
    with pytest.raises(InventoryError) as err:
        await inventory.async_undo_last(channel=Channel.SERVICE)
    assert _key(err) == "undo_zone_missing"


async def test_undo_reverts_a_whole_operation(inventory: InventoryService) -> None:
    """A multi-product operation is undone at once, in a single undo operation."""
    await _kitchen(inventory)
    milk = await inventory.async_find_product("milk")
    parmesan = await inventory.async_find_product("parmesan")
    await inventory.async_set_stock("milk", 1, channel=Channel.SERVICE)
    await inventory.backend.async_apply_changes(
        [
            StockChange(
                product_id=milk.id,
                zone_id="fridge",
                kind=MovementKind.PURCHASE,
                delta=2,
                channel=Channel.TELEGRAM,
            ),
            StockChange(
                product_id=parmesan.id,
                zone_id="pantry",
                kind=MovementKind.PURCHASE,
                delta=1,
                channel=Channel.TELEGRAM,
            ),
        ]
    )

    originals, undos = await inventory.async_undo_last(channel=Channel.SERVICE)
    assert [m.product.name for m in originals] == ["Milk", "Parmesan"]
    assert [m.product.name for m in undos] == ["Parmesan", "Milk"]
    assert len({m.entry.operation_id for m in undos}) == 1
    assert undos[0].entry.operation_id != originals[0].entry.operation_id
    assert await _quantity(inventory, "milk", "fridge") == 1
    assert await _quantity(inventory, "parmesan", "pantry") == 0

    # The next undo reaches the earlier, single-product operation.
    [original], _ = await inventory.async_undo_last(channel=Channel.SERVICE)
    assert original.entry.kind is MovementKind.INVENTORY
