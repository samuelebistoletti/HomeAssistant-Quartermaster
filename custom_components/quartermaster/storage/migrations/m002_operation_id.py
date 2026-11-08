"""Schema version 2: movements carry the id of the operation they belong to.

One operation (for example a confirmed photo proposal) can change several
products; undo reverts the whole operation. Existing movements become
operations of their own.
"""

from __future__ import annotations

STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE journal ADD COLUMN operation_id TEXT",
    "UPDATE journal SET operation_id = lower(hex(randomblob(16)))",
    "CREATE INDEX journal_operation_id ON journal (operation_id)",
)
