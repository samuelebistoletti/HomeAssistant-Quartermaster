"""Schema version 6: what the external todo copy of an item last received."""

from __future__ import annotations

STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE shopping_items ADD COLUMN external_sent TEXT",
)
