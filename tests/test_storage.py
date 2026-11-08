"""Tests for the SQLite storage backend."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sqlite3

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest

from custom_components.quartermaster.storage.migrations import MIGRATIONS
from custom_components.quartermaster.storage.models import (
    Channel,
    MovementKind,
    Product,
    StockChange,
    Zone,
)
from custom_components.quartermaster.storage.sqlite import (
    SchemaTooNewError,
    SQLiteBackend,
)


def _change(**kwargs: object) -> StockChange:
    values: dict[str, object] = {
        "product_id": 1,
        "zone_id": "fridge",
        "kind": MovementKind.PURCHASE,
        "channel": Channel.SERVICE,
    }
    values.update(kwargs)
    return StockChange(**values)  # type: ignore[arg-type]


async def _seed(backend: SQLiteBackend) -> Product:
    await backend.async_save_zone(Zone(id="fridge", name="Fridge", aliases=["frigo"]))
    return await backend.async_add_product(
        Product(id=0, name="Milk", aliases=["latte"], unit="bottle")
    )


async def test_open_creates_schema_in_wal_mode(
    hass: HomeAssistant, backend: SQLiteBackend, tmp_path: Path
) -> None:
    """Opening creates the file, the latest schema and WAL mode."""
    path = tmp_path / "quartermaster" / "pantry.db"
    assert path.exists()
    conn = sqlite3.connect(path)
    try:
        version = conn.execute(
            "SELECT value FROM settings WHERE key = 'schema_version'"
        ).fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert int(version) == MIGRATIONS[-1][0]
    assert mode == "wal"


async def test_reopen_keeps_data(hass: HomeAssistant, tmp_path: Path) -> None:
    """Reopening an up-to-date database runs no migration and keeps the data."""
    path = tmp_path / "db" / "pantry.db"
    first = SQLiteBackend(hass, path)
    await first.async_open()
    await _seed(first)
    await first.async_close()
    await first.async_close()  # closing twice is harmless

    second = SQLiteBackend(hass, path)
    await second.async_open()
    assert [
        p.name for p in await second.async_list_products(include_archived=False)
    ] == ["Milk"]
    await second.async_close()


async def test_newer_schema_is_refused(hass: HomeAssistant, tmp_path: Path) -> None:
    """A database from a newer version is not opened."""
    path = tmp_path / "pantry.db"
    store = SQLiteBackend(hass, path)
    await store.async_open()
    await store.async_close()
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("UPDATE settings SET value = '999' WHERE key = 'schema_version'")
    conn.close()

    with pytest.raises(SchemaTooNewError) as err:
        await SQLiteBackend(hass, path).async_open()
    assert err.value.found == 999


async def test_zones(backend: SQLiteBackend) -> None:
    """Zones are inserted, updated in place and deleted with their stock."""
    product = await _seed(backend)
    await backend.async_apply_changes([_change(product_id=product.id, delta=2)])
    await backend.async_save_zone(
        Zone(id="fridge", name="Main fridge", sources=[{"type": "manual"}])
    )
    [zone] = await backend.async_list_zones()
    assert zone.name == "Main fridge"
    assert zone.sources == [{"type": "manual"}]
    assert len(await backend.async_list_stock(zone_id="fridge")) == 1

    await backend.async_delete_zone("fridge")
    assert await backend.async_list_zones() == []
    assert await backend.async_list_stock() == []


async def test_products(backend: SQLiteBackend) -> None:
    """Products get an id, can be updated and archived products are hidden."""
    product = await _seed(backend)
    assert product.id > 0
    product.archived = True
    product.essential = True
    product.min_stock = 1
    await backend.async_update_product(product)

    assert await backend.async_list_products(include_archived=False) == []
    [stored] = await backend.async_list_products(include_archived=True)
    assert stored == product


async def test_apply_changes(backend: SQLiteBackend) -> None:
    """Deltas add up, sets replace, results never go below zero."""
    product = await _seed(backend)
    expiry = date(2026, 12, 1)
    first, second = await backend.async_apply_changes(
        [
            _change(product_id=product.id, delta=3, best_before=expiry),
            _change(product_id=product.id, kind=MovementKind.CONSUME, delta=-1.25),
        ]
    )
    assert (first.delta, first.quantity_after) == (3, 3)
    assert (second.delta, second.quantity_after) == (-1.25, 1.75)

    [over] = await backend.async_apply_changes(
        [_change(product_id=product.id, kind=MovementKind.CONSUME, delta=-10)]
    )
    assert (over.delta, over.quantity_after) == (-1.75, 0)

    [counted] = await backend.async_apply_changes(
        [_change(product_id=product.id, kind=MovementKind.INVENTORY, set_quantity=4)]
    )
    assert (counted.delta, counted.quantity_after) == (4, 4)

    [level] = await backend.async_list_stock(product_id=product.id)
    assert level.quantity == 4
    assert level.best_before == expiry  # kept when a change gives none
    assert level.updated_at is not None


async def test_failed_batch_is_rolled_back(backend: SQLiteBackend) -> None:
    """A batch with an invalid change leaves stock and journal untouched."""
    product = await _seed(backend)
    with pytest.raises(sqlite3.IntegrityError):
        await backend.async_apply_changes(
            [
                _change(product_id=product.id, delta=1),
                _change(product_id=product.id, zone_id="missing", delta=1),
            ]
        )
    assert await backend.async_list_stock() == []
    assert await backend.async_list_journal(limit=10) == []


async def test_journal_and_last_undoable(backend: SQLiteBackend) -> None:
    """The newest movement that is not an undo and not undone is found."""
    product = await _seed(backend)
    first, second = await backend.async_apply_changes(
        [
            _change(product_id=product.id, delta=1, user_id="alice"),
            _change(
                product_id=product.id,
                delta=1,
                channel=Channel.TELEGRAM,
                user_id="bob",
            ),
        ]
    )
    assert await backend.async_last_undoable() == second
    assert await backend.async_last_undoable(channel=Channel.SERVICE) == first
    assert await backend.async_last_undoable(user_id="bob") == second
    assert (
        await backend.async_last_undoable(since=dt_util.utcnow() + timedelta(minutes=1))
        is None
    )

    await backend.async_apply_changes(
        [
            _change(
                product_id=product.id,
                kind=MovementKind.UNDO,
                delta=-1,
                undoes_id=second.id,
            )
        ]
    )
    assert await backend.async_last_undoable() == first
    journal = await backend.async_list_journal(limit=10)
    assert [entry.kind for entry in journal] == [
        MovementKind.UNDO,
        MovementKind.PURCHASE,
        MovementKind.PURCHASE,
    ]
    assert await backend.async_list_journal(limit=10, product_id=999) == []
    assert len(await backend.async_list_journal(limit=10, product_id=product.id)) == 3


async def test_dump_and_restore(backend: SQLiteBackend) -> None:
    """A dump restored over other data gives back the same tables."""
    product = await _seed(backend)
    await backend.async_apply_changes([_change(product_id=product.id, delta=2)])
    dump = await backend.async_dump()
    assert dump["products"][0]["aliases"] == ["latte"]
    assert dump["products"][0]["essential"] is False

    await backend.async_save_zone(Zone(id="pantry", name="Pantry"))
    await backend.async_restore(dump)
    assert await backend.async_dump() == dump


async def test_restore_refuses_unknown_columns(backend: SQLiteBackend) -> None:
    """Restoring a row with a column that does not exist fails and changes nothing."""
    await _seed(backend)
    before = await backend.async_dump()
    with pytest.raises(ValueError, match="unknown zones columns"):
        await backend.async_restore(
            {"zones": [{"id": "x", "name": "X", "name); DROP TABLE zones; --": 1}]}
        )
    assert await backend.async_dump() == before
