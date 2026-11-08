"""Schema version 4: requests for a photo of a zone."""

from __future__ import annotations

STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE photo_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zone_id TEXT NOT NULL REFERENCES zones (id) ON DELETE CASCADE,
        reason TEXT NOT NULL,
        channel TEXT NOT NULL,
        created TEXT NOT NULL,
        reminded_at TEXT,
        expires_at TEXT NOT NULL,
        status TEXT NOT NULL
    )
    """,
)
