"""Schema version 5: drop per-zone settings of zones deleted earlier.

Settings keyed `<prefix>:<zone id>` (last_check, failures, ledger_check) go
when their zone is gone. The daily camera counters (source_reads, show_count)
were keyed by the source's position and are now keyed by the source itself:
all are dropped, which only restarts today's counts.
"""

from __future__ import annotations

STATEMENTS: tuple[str, ...] = (
    """
    DELETE FROM settings
    WHERE substr(key, 1, length('last_check:')) = 'last_check:'
        AND substr(key, length('last_check:') + 1) NOT IN (SELECT id FROM zones)
    """,
    """
    DELETE FROM settings
    WHERE substr(key, 1, length('failures:')) = 'failures:'
        AND substr(key, length('failures:') + 1) NOT IN (SELECT id FROM zones)
    """,
    """
    DELETE FROM settings
    WHERE substr(key, 1, length('ledger_check:')) = 'ledger_check:'
        AND substr(key, length('ledger_check:') + 1) NOT IN (SELECT id FROM zones)
    """,
    "DELETE FROM settings WHERE substr(key, 1, length('source_reads:')) = 'source_reads:'",
    "DELETE FROM settings WHERE substr(key, 1, length('show_count:')) = 'show_count:'",
)
