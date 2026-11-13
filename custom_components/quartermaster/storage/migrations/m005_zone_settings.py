"""Schema version 5: drop per-zone settings of zones deleted earlier.

Settings keyed `<prefix>:<zone id>` (last_check, failures, ledger_check) and
`<prefix>:<zone id>:<source>` (source_reads, show_count) whose zone is gone.
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
    """
    DELETE FROM settings
    WHERE substr(key, 1, length('source_reads:')) = 'source_reads:'
        AND instr(substr(key, length('source_reads:') + 1), ':') > 0
        AND substr(
            substr(key, length('source_reads:') + 1),
            1,
            instr(substr(key, length('source_reads:') + 1), ':') - 1
        ) NOT IN (SELECT id FROM zones)
    """,
    """
    DELETE FROM settings
    WHERE substr(key, 1, length('show_count:')) = 'show_count:'
        AND instr(substr(key, length('show_count:') + 1), ':') > 0
        AND substr(
            substr(key, length('show_count:') + 1),
            1,
            instr(substr(key, length('show_count:') + 1), ':') - 1
        ) NOT IN (SELECT id FROM zones)
    """,
)
