"""Confirmations: writes held until the user says yes.

Tool confirmations are single-use ids for LLM tool writes that the spec marks
as needing a yes (new products, essentials, large changes). Proposals from
photos and cameras (Phase 4) also live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

CONFIRMATION_TTL = timedelta(minutes=5)


@dataclass(slots=True)
class PendingConfirmation:
    """A write held until the user confirms it."""

    tool: str
    arguments: dict[str, Any]
    expires: datetime


@dataclass
class ToolConfirmations:
    """Single-use confirmation ids for writes that need a yes."""

    pending: dict[str, PendingConfirmation] = field(default_factory=dict)

    def create(self, tool: str, arguments: dict[str, Any]) -> str:
        """Store a write and return its id."""
        now = dt_util.utcnow()
        self.pending = {k: v for k, v in self.pending.items() if v.expires > now}
        confirmation_id = ulid_now()
        self.pending[confirmation_id] = PendingConfirmation(
            tool, arguments, now + CONFIRMATION_TTL
        )
        return confirmation_id

    def take(self, tool: str, confirmation_id: str) -> dict[str, Any] | None:
        """Return the stored arguments once, if the id is valid for the tool."""
        pending = self.pending.pop(confirmation_id, None)
        if (
            pending is None
            or pending.tool != tool
            or pending.expires <= dt_util.utcnow()
        ):
            return None
        return pending.arguments
