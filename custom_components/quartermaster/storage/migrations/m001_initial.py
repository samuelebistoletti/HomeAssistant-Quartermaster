"""Schema version 1: every table of the data model."""

from __future__ import annotations

STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE zones (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        aliases TEXT NOT NULL DEFAULT '[]',
        sources TEXT NOT NULL DEFAULT '[]',
        check_schedule TEXT,
        reminder_days INTEGER,
        confidence_threshold REAL
    )
    """,
    """
    CREATE TABLE products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        aliases TEXT NOT NULL DEFAULT '[]',
        category TEXT,
        default_zone_id TEXT REFERENCES zones (id) ON DELETE SET NULL,
        unit TEXT,
        essential INTEGER NOT NULL DEFAULT 0,
        min_stock REAL,
        target_stock REAL,
        visual_hints TEXT,
        barcode TEXT,
        shelf_life_days INTEGER,
        archived INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE stock (
        product_id INTEGER NOT NULL REFERENCES products (id) ON DELETE CASCADE,
        zone_id TEXT NOT NULL REFERENCES zones (id) ON DELETE CASCADE,
        quantity REAL NOT NULL,
        best_before TEXT,
        opened_at TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (product_id, zone_id)
    )
    """,
    """
    CREATE TABLE journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        product_id INTEGER NOT NULL,
        zone_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        delta REAL NOT NULL,
        quantity_after REAL NOT NULL,
        channel TEXT NOT NULL,
        user_id TEXT,
        confidence REAL,
        image_ref TEXT,
        undoes_id INTEGER REFERENCES journal (id)
    )
    """,
    "CREATE INDEX journal_undoes_id ON journal (undoes_id)",
    "CREATE INDEX journal_product_id ON journal (product_id)",
    """
    CREATE TABLE shopping_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER REFERENCES products (id) ON DELETE SET NULL,
        text TEXT NOT NULL,
        quantity REAL,
        status TEXT NOT NULL,
        origin TEXT NOT NULL,
        external_uid TEXT
    )
    """,
    """
    CREATE TABLE pending_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT NOT NULL,
        channel TEXT NOT NULL,
        zone_id TEXT,
        payload TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        status TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE zone_snapshots (
        zone_id TEXT PRIMARY KEY REFERENCES zones (id) ON DELETE CASCADE,
        ts TEXT NOT NULL,
        perceptual_hash TEXT,
        overall_confidence REAL,
        result TEXT
    )
    """,
)
