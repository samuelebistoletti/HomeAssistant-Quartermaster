"""SQLite storage backend (standard library `sqlite3`)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .base import StorageBackend
from .migrations import MIGRATIONS
from .models import (
    Channel,
    JournalEntry,
    MovementKind,
    Product,
    Proposal,
    ProposalStatus,
    ShoppingItem,
    ShoppingOrigin,
    ShoppingStatus,
    StockChange,
    StockLevel,
    Zone,
    ZoneSnapshot,
)

SCHEMA_VERSION_KEY = "schema_version"
QUANTITY_DIGITS = 3

# Table and column names in SQL strings come only from these constants or are
# checked against PRAGMA table_info, never taken from input as they are.
# Tables in `async_dump`, in an order that satisfies the foreign keys on insert.
DUMP_TABLES: tuple[str, ...] = (
    "zones",
    "products",
    "stock",
    "journal",
    "shopping_items",
)
# Derived or short-lived tables, emptied by `async_restore`.
VOLATILE_TABLES: tuple[str, ...] = ("pending_changes", "zone_snapshots")
JSON_COLUMNS: dict[str, frozenset[str]] = {
    "zones": frozenset({"aliases", "sources"}),
    "products": frozenset({"aliases"}),
}
BOOL_COLUMNS: dict[str, frozenset[str]] = {
    "products": frozenset({"essential", "archived"}),
}


class SchemaTooNewError(Exception):
    """The database was written by a newer version of the integration."""

    def __init__(self, found: int, supported: int) -> None:
        """Store the versions."""
        super().__init__(f"schema version {found} is newer than {supported}")
        self.found = found
        self.supported = supported


def _quantity(value: float) -> float:
    return round(max(value, 0.0), QUANTITY_DIGITS)


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _zone(row: sqlite3.Row) -> Zone:
    return Zone(
        id=row["id"],
        name=row["name"],
        aliases=json.loads(row["aliases"]),
        sources=json.loads(row["sources"]),
        check_schedule=row["check_schedule"],
        reminder_days=row["reminder_days"],
        confidence_threshold=row["confidence_threshold"],
    )


def _product(row: sqlite3.Row) -> Product:
    return Product(
        id=row["id"],
        name=row["name"],
        aliases=json.loads(row["aliases"]),
        category=row["category"],
        default_zone_id=row["default_zone_id"],
        unit=row["unit"],
        essential=bool(row["essential"]),
        min_stock=row["min_stock"],
        target_stock=row["target_stock"],
        visual_hints=row["visual_hints"],
        barcode=row["barcode"],
        shelf_life_days=row["shelf_life_days"],
        archived=bool(row["archived"]),
    )


def _stock(row: sqlite3.Row) -> StockLevel:
    return StockLevel(
        product_id=row["product_id"],
        zone_id=row["zone_id"],
        quantity=row["quantity"],
        best_before=_date(row["best_before"]),
        opened_at=_datetime(row["opened_at"]),
        updated_at=_datetime(row["updated_at"]),
    )


def _journal(row: sqlite3.Row) -> JournalEntry:
    ts = _datetime(row["ts"])
    assert ts is not None
    return JournalEntry(
        id=row["id"],
        operation_id=row["operation_id"],
        ts=ts,
        product_id=row["product_id"],
        zone_id=row["zone_id"],
        kind=MovementKind(row["kind"]),
        delta=row["delta"],
        quantity_after=row["quantity_after"],
        channel=Channel(row["channel"]),
        user_id=row["user_id"],
        confidence=row["confidence"],
        image_ref=row["image_ref"],
        undoes_id=row["undoes_id"],
    )


def _shopping_item(row: sqlite3.Row) -> ShoppingItem:
    return ShoppingItem(
        id=row["id"],
        text=row["text"],
        status=ShoppingStatus(row["status"]),
        origin=ShoppingOrigin(row["origin"]),
        product_id=row["product_id"],
        quantity=row["quantity"],
        note=row["note"],
        position=row["position"],
        completed_at=_datetime(row["completed_at"]),
        external_uid=row["external_uid"],
    )


def _proposal(row: sqlite3.Row) -> Proposal:
    created = _datetime(row["created"])
    expires = _datetime(row["expires_at"])
    assert created is not None
    assert expires is not None
    return Proposal(
        id=row["id"],
        created=created,
        channel=Channel(row["channel"]),
        zone_id=row["zone_id"],
        payload=json.loads(row["payload"]),
        expires_at=expires,
        status=ProposalStatus(row["status"]),
    )


def _shopping_values(item: ShoppingItem) -> tuple[Any, ...]:
    return (
        item.product_id,
        item.text,
        item.quantity,
        item.status.value,
        item.origin.value,
        item.external_uid,
        item.position,
        _iso(item.completed_at),
        item.note,
    )


class SQLiteBackend(StorageBackend):
    """Store in a single SQLite file, in WAL mode, used from the executor only."""

    def __init__(self, hass: HomeAssistant, path: Path) -> None:
        """Prepare the backend; nothing is opened until `async_open`."""
        self._hass = hass
        self._path = path
        self._conn: sqlite3.Connection | None = None
        # One connection is shared by executor threads, one call at a time.
        self._lock = asyncio.Lock()

    async def _run[T](self, func: Callable[..., T], *args: Any) -> T:
        async with self._lock:
            return await self._hass.async_add_executor_job(func, *args)

    @property
    def _db(self) -> sqlite3.Connection:
        assert self._conn is not None, "backend is not open"
        return self._conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._db
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    # Opening and migrations

    async def async_open(self) -> None:
        """Open the database file and apply pending migrations."""
        await self._run(self._open)

    def _open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, autocommit=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
            self._migrate()
        except BaseException:
            self._conn = None
            conn.close()
            raise

    def _schema_version(self) -> int:
        exists = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings'"
        ).fetchone()
        if exists is None:
            return 0
        row = self._db.execute(
            "SELECT value FROM settings WHERE key = ?", (SCHEMA_VERSION_KEY,)
        ).fetchone()
        return int(row["value"]) if row else 0

    def _migrate(self) -> None:
        current = self._schema_version()
        latest = MIGRATIONS[-1][0]
        if current > latest:
            raise SchemaTooNewError(current, latest)
        for version, statements in MIGRATIONS:
            if version <= current:
                continue
            with self._transaction() as conn:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (SCHEMA_VERSION_KEY, str(version)),
                )

    async def async_close(self) -> None:
        """Close the database file."""
        await self._run(self._close)

    def _close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # Zones

    async def async_list_zones(self) -> list[Zone]:
        """Return every zone, by name."""
        return await self._run(self._list_zones)

    def _list_zones(self) -> list[Zone]:
        rows = self._db.execute("SELECT * FROM zones ORDER BY name").fetchall()
        return [_zone(row) for row in rows]

    async def async_save_zone(self, zone: Zone) -> None:
        """Insert or update a zone, keeping its stock."""
        await self._run(self._save_zone, zone)

    def _save_zone(self, zone: Zone) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO zones (id, name, aliases, sources, check_schedule,
                    reminder_days, confidence_threshold)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name,
                    aliases = excluded.aliases,
                    sources = excluded.sources,
                    check_schedule = excluded.check_schedule,
                    reminder_days = excluded.reminder_days,
                    confidence_threshold = excluded.confidence_threshold
                """,
                (
                    zone.id,
                    zone.name,
                    json.dumps(zone.aliases),
                    json.dumps(zone.sources),
                    zone.check_schedule,
                    zone.reminder_days,
                    zone.confidence_threshold,
                ),
            )

    async def async_delete_zone(self, zone_id: str) -> None:
        """Delete a zone; its stock rows go with it."""
        await self._run(self._delete_zone, zone_id)

    def _delete_zone(self, zone_id: str) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM zones WHERE id = ?", (zone_id,))

    # Products

    async def async_list_products(self, *, include_archived: bool) -> list[Product]:
        """Return the products, by name."""
        return await self._run(self._list_products, include_archived)

    def _list_products(self, include_archived: bool) -> list[Product]:
        query = "SELECT * FROM products"
        if not include_archived:
            query += " WHERE archived = 0"
        rows = self._db.execute(f"{query} ORDER BY name, id").fetchall()
        return [_product(row) for row in rows]

    async def async_add_product(self, product: Product) -> Product:
        """Insert a product and return it with its id."""
        return await self._run(self._add_product, product)

    def _add_product(self, product: Product) -> Product:
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO products (name, aliases, category, default_zone_id,
                    unit, essential, min_stock, target_stock, visual_hints,
                    barcode, shelf_life_days, archived)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._product_values(product),
            )
        assert cursor.lastrowid is not None
        product.id = cursor.lastrowid
        return product

    async def async_update_product(self, product: Product) -> None:
        """Replace a stored product."""
        await self._run(self._update_product, product)

    def _update_product(self, product: Product) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE products SET name = ?, aliases = ?, category = ?,
                    default_zone_id = ?, unit = ?, essential = ?, min_stock = ?,
                    target_stock = ?, visual_hints = ?, barcode = ?,
                    shelf_life_days = ?, archived = ?
                WHERE id = ?
                """,
                (*self._product_values(product), product.id),
            )

    @staticmethod
    def _product_values(product: Product) -> tuple[Any, ...]:
        return (
            product.name,
            json.dumps(product.aliases),
            product.category,
            product.default_zone_id,
            product.unit,
            int(product.essential),
            product.min_stock,
            product.target_stock,
            product.visual_hints,
            product.barcode,
            product.shelf_life_days,
            int(product.archived),
        )

    # Stock and journal

    async def async_list_stock(
        self, *, product_id: int | None = None, zone_id: str | None = None
    ) -> list[StockLevel]:
        """Return stock rows, optionally filtered by product or zone."""
        return await self._run(self._list_stock, product_id, zone_id)

    def _list_stock(
        self, product_id: int | None, zone_id: str | None
    ) -> list[StockLevel]:
        clauses: list[str] = []
        params: list[Any] = []
        if product_id is not None:
            clauses.append("product_id = ?")
            params.append(product_id)
        if zone_id is not None:
            clauses.append("zone_id = ?")
            params.append(zone_id)
        query = "SELECT * FROM stock"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = self._db.execute(
            f"{query} ORDER BY product_id, zone_id", params
        ).fetchall()
        return [_stock(row) for row in rows]

    async def async_set_opened(
        self, product_id: int, zone_id: str, when: datetime
    ) -> None:
        """Record when a product was opened in a zone."""
        await self._run(self._set_opened, product_id, zone_id, when)

    def _set_opened(self, product_id: int, zone_id: str, when: datetime) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE stock SET opened_at = ? WHERE product_id = ? AND zone_id = ?",
                (when.isoformat(), product_id, zone_id),
            )

    async def async_apply_changes(
        self, changes: list[StockChange]
    ) -> list[JournalEntry]:
        """Apply the changes and their journal entries in one transaction."""
        return await self._run(self._apply_changes, changes)

    def _apply_changes(self, changes: list[StockChange]) -> list[JournalEntry]:
        now = dt_util.utcnow()
        operation_id = ulid_now()
        ids: list[int] = []
        with self._transaction() as conn:
            for change in changes:
                row = conn.execute(
                    "SELECT quantity FROM stock WHERE product_id = ? AND zone_id = ?",
                    (change.product_id, change.zone_id),
                ).fetchone()
                before = row["quantity"] if row else 0.0
                if change.set_quantity is not None:
                    after = _quantity(change.set_quantity)
                else:
                    after = _quantity(before + change.delta)
                conn.execute(
                    """
                    INSERT INTO stock (product_id, zone_id, quantity, best_before,
                        updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (product_id, zone_id) DO UPDATE SET
                        quantity = excluded.quantity,
                        best_before = coalesce(excluded.best_before, best_before),
                        updated_at = excluded.updated_at
                    """,
                    (
                        change.product_id,
                        change.zone_id,
                        after,
                        _iso(change.best_before),
                        now.isoformat(),
                    ),
                )
                cursor = conn.execute(
                    """
                    INSERT INTO journal (ts, product_id, zone_id, kind, delta,
                        quantity_after, channel, user_id, confidence, image_ref,
                        undoes_id, operation_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now.isoformat(),
                        change.product_id,
                        change.zone_id,
                        change.kind.value,
                        round(after - before, QUANTITY_DIGITS),
                        after,
                        change.channel.value,
                        change.user_id,
                        change.confidence,
                        change.image_ref,
                        change.undoes_id,
                        operation_id,
                    ),
                )
                assert cursor.lastrowid is not None
                ids.append(cursor.lastrowid)
        rows = self._db.execute(
            f"SELECT * FROM journal WHERE id IN ({','.join('?' * len(ids))}) "  # noqa: S608  # nosec B608
            "ORDER BY id",
            ids,
        ).fetchall()
        return [_journal(row) for row in rows]

    async def async_list_journal(
        self,
        *,
        limit: int,
        product_id: int | None = None,
        since: datetime | None = None,
    ) -> list[JournalEntry]:
        """Return the newest journal entries first."""
        return await self._run(self._list_journal, limit, product_id, since)

    def _list_journal(
        self, limit: int, product_id: int | None, since: datetime | None
    ) -> list[JournalEntry]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if product_id is not None:
            clauses.append("product_id = ?")
            params.append(product_id)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since.isoformat())
        rows = self._db.execute(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} "  # noqa: S608  # nosec B608
            "ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [_journal(row) for row in rows]

    async def async_last_undoable(
        self,
        *,
        channel: Channel | None = None,
        user_id: str | None = None,
        since: datetime | None = None,
    ) -> list[JournalEntry]:
        """Return the movements of the newest operation that is not undone."""
        return await self._run(self._last_undoable, channel, user_id, since)

    def _last_undoable(
        self, channel: Channel | None, user_id: str | None, since: datetime | None
    ) -> list[JournalEntry]:
        clauses = [
            "j.kind != ?",
            "NOT EXISTS (SELECT 1 FROM journal u WHERE u.undoes_id = j.id)",
        ]
        params: list[Any] = [MovementKind.UNDO.value]
        if channel is not None:
            clauses.append("j.channel = ?")
            params.append(channel.value)
        if user_id is not None:
            clauses.append("j.user_id = ?")
            params.append(user_id)
        if since is not None:
            clauses.append("j.ts >= ?")
            params.append(since.isoformat())
        row = self._db.execute(
            f"SELECT j.* FROM journal j WHERE {' AND '.join(clauses)} "  # noqa: S608  # nosec B608
            "ORDER BY j.id DESC LIMIT 1",
            params,
        ).fetchone()
        if row is None:
            return []
        rows = self._db.execute(
            """
            SELECT j.* FROM journal j
            WHERE j.operation_id = ? AND j.kind != ?
                AND NOT EXISTS (SELECT 1 FROM journal u WHERE u.undoes_id = j.id)
            ORDER BY j.id
            """,
            (row["operation_id"], MovementKind.UNDO.value),
        ).fetchall()
        return [_journal(row) for row in rows]

    # Shopping list

    async def async_list_shopping_items(self) -> list[ShoppingItem]:
        """Return the shopping list in display order."""
        return await self._run(self._list_shopping_items)

    def _list_shopping_items(self) -> list[ShoppingItem]:
        rows = self._db.execute(
            "SELECT * FROM shopping_items ORDER BY position, id"
        ).fetchall()
        return [_shopping_item(row) for row in rows]

    async def async_add_shopping_item(self, item: ShoppingItem) -> ShoppingItem:
        """Append an item and return it with its id and position."""
        return await self._run(self._add_shopping_item, item)

    def _add_shopping_item(self, item: ShoppingItem) -> ShoppingItem:
        with self._transaction() as conn:
            item.position = conn.execute(
                "SELECT coalesce(max(position), 0) + 1 FROM shopping_items"
            ).fetchone()[0]
            cursor = conn.execute(
                """
                INSERT INTO shopping_items (product_id, text, quantity, status,
                    origin, external_uid, position, completed_at, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _shopping_values(item),
            )
        assert cursor.lastrowid is not None
        item.id = cursor.lastrowid
        return item

    async def async_update_shopping_items(self, items: list[ShoppingItem]) -> None:
        """Replace stored items."""
        await self._run(self._update_shopping_items, items)

    def _update_shopping_items(self, items: list[ShoppingItem]) -> None:
        with self._transaction() as conn:
            conn.executemany(
                """
                UPDATE shopping_items SET product_id = ?, text = ?, quantity = ?,
                    status = ?, origin = ?, external_uid = ?, position = ?,
                    completed_at = ?, note = ?
                WHERE id = ?
                """,
                [(*_shopping_values(item), item.id) for item in items],
            )

    async def async_delete_shopping_items(self, item_ids: list[int]) -> None:
        """Delete items."""
        await self._run(self._delete_shopping_items, item_ids)

    def _delete_shopping_items(self, item_ids: list[int]) -> None:
        with self._transaction() as conn:
            conn.executemany(
                "DELETE FROM shopping_items WHERE id = ?",
                [(item_id,) for item_id in item_ids],
            )

    async def async_reorder_shopping_items(self, item_ids: list[int]) -> None:
        """Store the display order."""
        await self._run(self._reorder_shopping_items, item_ids)

    def _reorder_shopping_items(self, item_ids: list[int]) -> None:
        with self._transaction() as conn:
            conn.executemany(
                "UPDATE shopping_items SET position = ? WHERE id = ?",
                [(position, item_id) for position, item_id in enumerate(item_ids, 1)],
            )

    # Proposals and zone snapshots

    async def async_add_proposal(self, proposal: Proposal) -> Proposal:
        """Store a proposal and return it with its id."""
        return await self._run(self._add_proposal, proposal)

    def _add_proposal(self, proposal: Proposal) -> Proposal:
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO pending_changes (created, channel, zone_id, payload,
                    expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.created.isoformat(),
                    proposal.channel.value,
                    proposal.zone_id,
                    json.dumps(proposal.payload),
                    proposal.expires_at.isoformat(),
                    proposal.status.value,
                ),
            )
        assert cursor.lastrowid is not None
        proposal.id = cursor.lastrowid
        return proposal

    async def async_list_proposals(
        self, *, status: ProposalStatus | None = None
    ) -> list[Proposal]:
        """Return proposals, newest first."""
        return await self._run(self._list_proposals, status)

    def _list_proposals(self, status: ProposalStatus | None) -> list[Proposal]:
        if status is None:
            rows = self._db.execute(
                "SELECT * FROM pending_changes ORDER BY id DESC"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM pending_changes WHERE status = ? ORDER BY id DESC",
                (status.value,),
            ).fetchall()
        return [_proposal(row) for row in rows]

    async def async_update_proposal(self, proposal: Proposal) -> None:
        """Store the new status and payload of a proposal."""
        await self._run(self._update_proposal, proposal)

    def _update_proposal(self, proposal: Proposal) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE pending_changes SET status = ?, payload = ? WHERE id = ?",
                (proposal.status.value, json.dumps(proposal.payload), proposal.id),
            )

    async def async_get_snapshot(self, zone_id: str) -> ZoneSnapshot | None:
        """Return the last analysis of a zone."""
        return await self._run(self._get_snapshot, zone_id)

    def _get_snapshot(self, zone_id: str) -> ZoneSnapshot | None:
        row = self._db.execute(
            "SELECT * FROM zone_snapshots WHERE zone_id = ?", (zone_id,)
        ).fetchone()
        if row is None:
            return None
        ts = _datetime(row["ts"])
        assert ts is not None
        return ZoneSnapshot(
            zone_id=row["zone_id"],
            ts=ts,
            perceptual_hash=row["perceptual_hash"],
            overall_confidence=row["overall_confidence"],
            result=json.loads(row["result"] or "{}"),
        )

    async def async_save_snapshot(self, snapshot: ZoneSnapshot) -> None:
        """Replace the last analysis of a zone."""
        await self._run(self._save_snapshot, snapshot)

    def _save_snapshot(self, snapshot: ZoneSnapshot) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO zone_snapshots (zone_id, ts, perceptual_hash,
                    overall_confidence, result)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.zone_id,
                    snapshot.ts.isoformat(),
                    snapshot.perceptual_hash,
                    snapshot.overall_confidence,
                    json.dumps(snapshot.result),
                ),
            )

    # Settings

    async def async_get_setting(self, key: str) -> str | None:
        """Return an internal setting."""
        return await self._run(self._get_setting, key)

    def _get_setting(self, key: str) -> str | None:
        row = self._db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    async def async_set_setting(self, key: str, value: str) -> None:
        """Store an internal setting."""
        await self._run(self._set_setting, key, value)

    def _set_setting(self, key: str, value: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    # Export and import

    async def async_dump(self) -> dict[str, list[dict[str, Any]]]:
        """Return every persistent table as lists of plain dicts."""
        return await self._run(self._dump)

    def _dump(self) -> dict[str, list[dict[str, Any]]]:
        tables: dict[str, list[dict[str, Any]]] = {}
        for table in DUMP_TABLES:
            json_columns = JSON_COLUMNS.get(table, frozenset())
            bool_columns = BOOL_COLUMNS.get(table, frozenset())
            rows = []
            for row in self._db.execute(f"SELECT * FROM {table} ORDER BY rowid"):  # noqa: S608  # nosec B608
                record = dict(row)
                for column in json_columns:
                    record[column] = json.loads(record[column])
                for column in bool_columns:
                    record[column] = bool(record[column])
                rows.append(record)
            tables[table] = rows
        return tables

    async def async_restore(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        """Replace the inventory with the given tables in one transaction."""
        await self._run(self._restore, tables)

    def _restore(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        with self._transaction() as conn:
            for table in (*VOLATILE_TABLES, *reversed(DUMP_TABLES)):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608  # nosec B608
            for table in DUMP_TABLES:
                json_columns = JSON_COLUMNS.get(table, frozenset())
                bool_columns = BOOL_COLUMNS.get(table, frozenset())
                known = {
                    row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
                }
                for record in tables.get(table, []):
                    if unknown := record.keys() - known:
                        raise ValueError(f"unknown {table} columns: {sorted(unknown)}")
                    values = dict(record)
                    for column in json_columns & values.keys():
                        values[column] = json.dumps(values[column])
                    for column in bool_columns & values.keys():
                        values[column] = int(values[column])
                    columns = ", ".join(values)
                    marks = ", ".join("?" * len(values))
                    conn.execute(
                        f"INSERT INTO {table} ({columns}) VALUES ({marks})",  # noqa: S608  # nosec B608
                        tuple(values.values()),
                    )
