"""Minimal five-field cron matcher for zone check schedules ("0 19 * * *").

Fields: minute, hour, day of month, month, day of week (0-7, 0 and 7 are
Sunday). Each field accepts `*`, numbers, ranges `a-b`, lists `a,b` and steps
`*/n` or `a-b/n`. As in cron, when both day fields are restricted a time
matches either of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_LIMITS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


class CronError(ValueError):
    """The expression is not valid."""


def _field(text: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(text)
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            first, last = part.split("-", 1)
            if not (first.isdigit() and last.isdigit()):
                raise CronError(text)
            start, end = int(first), int(last)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise CronError(text)
        if not low <= start <= end <= high:
            raise CronError(text)
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class Cron:
    """A parsed expression."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    any_day: bool
    any_weekday: bool

    @classmethod
    def parse(cls, expression: str) -> Cron:
        """Parse five fields; raise CronError when invalid."""
        parts = expression.split()
        if len(parts) != 5:
            raise CronError(expression)
        fields = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _LIMITS, strict=True)]
        weekdays = frozenset(d % 7 for d in fields[4])
        return cls(
            fields[0],
            fields[1],
            fields[2],
            fields[3],
            weekdays,
            any_day=parts[2] == "*",
            any_weekday=parts[4] == "*",
        )

    def matches(self, when: datetime) -> bool:
        """Return whether the minute of `when` is scheduled."""
        if (
            when.minute not in self.minutes
            or when.hour not in self.hours
            or when.month not in self.months
        ):
            return False
        day = when.day in self.days
        weekday = (when.isoweekday() % 7) in self.weekdays
        if self.any_day or self.any_weekday:
            return day and weekday
        return day or weekday


def is_valid(expression: str) -> bool:
    """Return whether the expression parses."""
    try:
        Cron.parse(expression)
    except CronError:
        return False
    return True
