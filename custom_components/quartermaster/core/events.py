"""Helpers for entities whose state is the time of their last event."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util

# An event entity that comes back from "unavailable", or is added again, shows
# its restored state: the time of an event that was handled before. A state
# this recent is a new event instead.
FRESH_EVENT = timedelta(seconds=30)


def is_fresh_event(state: str) -> bool:
    """Return whether an event entity's state is the time of an event just fired."""
    when = dt_util.parse_datetime(state)
    if when is None:
        return False
    age = dt_util.utcnow() - dt_util.as_utc(when)
    return abs(age) <= FRESH_EVENT
