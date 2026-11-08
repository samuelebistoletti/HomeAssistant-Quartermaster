"""Numbered SQLite schema migrations.

Each module holds the statements that bring the schema from the previous
version to its own. Never edit a released migration: add a new module and
append it to `MIGRATIONS`.
"""

from __future__ import annotations

from . import m001_initial

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = ((1, m001_initial.STATEMENTS),)
