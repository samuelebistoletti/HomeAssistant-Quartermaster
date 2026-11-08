"""Schema version 3: shopping list order, completion time and user note."""

from __future__ import annotations

STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE shopping_items ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE shopping_items ADD COLUMN completed_at TEXT",
    "ALTER TABLE shopping_items ADD COLUMN note TEXT",
    "UPDATE shopping_items SET position = id",
)
