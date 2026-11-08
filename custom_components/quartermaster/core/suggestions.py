"""Essentials to buy, purchase suggestions and expiring stock.

Pure functions over a snapshot of the inventory, so the rules of spec §6.8
are easy to test:

- to buy: an essential whose total quantity (all zones) is below its minimum;
  quantity = target - total, or minimum - total without a target, at least 1;
- suggested, always with a reason: an essential above its minimum but below
  its target; an essential kept in a zone not updated for more than N days;
  a non-essential product finished in the last 30 days;
- products already on the shopping list are not suggested;
- forecast (spec §15): with at least three consumptions in the last 60 days,
  a product that at the usual pace runs out within the forecast days is
  suggested (reason running_out); an essential goes straight to the list.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
import math

from custom_components.quartermaster.storage.models import (
    JournalEntry,
    MovementKind,
    Product,
    StockLevel,
    Zone,
)

FINISHED_LOOKBACK = timedelta(days=30)
FORECAST_LOOKBACK = timedelta(days=60)
FORECAST_MIN_EVENTS = 3
# Rates over a few days are noisy: spread them over at least a week.
FORECAST_MIN_SPAN_DAYS = 7
JOURNAL_LOOKBACK = max(FINISHED_LOOKBACK, FORECAST_LOOKBACK)


class SuggestionReason(StrEnum):
    """Why a product is suggested; the text comes from the translations."""

    BELOW_TARGET = "below_target"
    STALE_ZONE = "stale_zone"
    FINISHED_RECENTLY = "finished_recently"
    RUNNING_OUT = "running_out"


@dataclass(slots=True)
class ToBuy:
    """An essential below its minimum stock."""

    product: Product
    total: float
    quantity: float


@dataclass(slots=True)
class Suggestion:
    """A product worth buying, with the reason and its values."""

    product: Product
    reason: SuggestionReason
    total: float
    details: dict[str, str | float | int] = field(default_factory=dict)


@dataclass(slots=True)
class Expiring:
    """Stock that expires within the threshold (or already expired)."""

    product: Product
    zone: Zone
    quantity: float
    best_before: date
    days_left: int


@dataclass(slots=True)
class Analysis:
    """Result of one computation."""

    totals: dict[int, float]
    to_buy: list[ToBuy]
    suggestions: list[Suggestion]
    expiring: list[Expiring]


def zone_last_updates(stock: list[StockLevel]) -> dict[str, datetime]:
    """Return the newest stock update per zone."""
    updates: dict[str, datetime] = {}
    for level in stock:
        if level.updated_at is None:
            continue
        current = updates.get(level.zone_id)
        if current is None or level.updated_at > current:
            updates[level.zone_id] = level.updated_at
    return updates


def analyze(
    *,
    products: list[Product],
    zones: list[Zone],
    stock: list[StockLevel],
    journal: list[JournalEntry],
    listed_product_ids: set[int],
    zone_updates: dict[str, datetime],
    now: datetime,
    stale_days: int,
    expiring_days: int,
    forecast_days: int = 0,
) -> Analysis:
    """Compute what to buy, what to suggest and what is expiring."""
    zones_by_id = {zone.id: zone for zone in zones}
    totals: dict[int, float] = defaultdict(float)
    held_in: dict[int, list[str]] = defaultdict(list)
    for level in stock:
        totals[level.product_id] += level.quantity
        if level.quantity > 0:
            held_in[level.product_id].append(level.zone_id)

    finished = _finished_dates(journal, now)
    rates = consumption_rates(journal, now) if forecast_days > 0 else {}
    to_buy: list[ToBuy] = []
    suggestions: list[Suggestion] = []
    for product in products:
        total = round(totals.get(product.id, 0.0), 3)
        days_left = _days_left(total, rates.get(product.id))
        running_out = days_left is not None and days_left <= forecast_days
        if product.essential and product.min_stock is not None:
            if total < product.min_stock or running_out:
                goal = product.target_stock or product.min_stock
                to_buy.append(ToBuy(product, total, max(goal - total, 1.0)))
                continue
            if product.id in listed_product_ids:
                continue
            if product.target_stock is not None and total < product.target_stock:
                suggestions.append(
                    Suggestion(
                        product,
                        SuggestionReason.BELOW_TARGET,
                        total,
                        {"have": total, "target": product.target_stock},
                    )
                )
                continue
            stale = _stalest_zone(held_in[product.id], zone_updates, now, stale_days)
            if stale is not None:
                zone_id, days = stale
                suggestions.append(
                    Suggestion(
                        product,
                        SuggestionReason.STALE_ZONE,
                        total,
                        {"zone": zones_by_id[zone_id].name, "days": days},
                    )
                )
            continue
        if product.id in listed_product_ids:
            continue
        if running_out and total > 0:
            suggestions.append(
                Suggestion(
                    product,
                    SuggestionReason.RUNNING_OUT,
                    total,
                    {
                        "days_left": days_left or 0,
                        "per_day": round(rates[product.id], 2),
                    },
                )
            )
            continue
        if total > 0:
            continue
        if (finished_on := finished.get(product.id)) is not None:
            suggestions.append(
                Suggestion(
                    product,
                    SuggestionReason.FINISHED_RECENTLY,
                    total,
                    {"date": finished_on.isoformat()},
                )
            )

    return Analysis(
        totals=dict(totals),
        to_buy=to_buy,
        suggestions=suggestions,
        expiring=_expiring(products, zones_by_id, stock, now.date(), expiring_days),
    )


def consumption_rates(journal: list[JournalEntry], now: datetime) -> dict[int, float]:
    """Return units consumed per day, for products consumed often enough."""
    since = now - FORECAST_LOOKBACK
    used: dict[int, float] = defaultdict(float)
    events: dict[int, int] = defaultdict(int)
    first: dict[int, datetime] = {}
    undone = {entry.undoes_id for entry in journal if entry.undoes_id is not None}
    for entry in journal:
        if (
            entry.kind is not MovementKind.CONSUME
            or entry.ts < since
            or entry.id in undone
        ):
            continue
        used[entry.product_id] += -entry.delta
        events[entry.product_id] += 1
        if entry.product_id not in first or entry.ts < first[entry.product_id]:
            first[entry.product_id] = entry.ts
    rates: dict[int, float] = {}
    for product_id, count in events.items():
        if count < FORECAST_MIN_EVENTS or used[product_id] <= 0:
            continue
        span = max((now - first[product_id]).total_seconds() / 86400, 1.0)
        rates[product_id] = used[product_id] / max(span, FORECAST_MIN_SPAN_DAYS)
    return rates


def _days_left(total: float, rate: float | None) -> int | None:
    if rate is None or total <= 0:
        return None
    return math.floor(total / rate)


def _finished_dates(journal: list[JournalEntry], now: datetime) -> dict[int, date]:
    """Return the latest date each product was finished within the lookback."""
    since = now - FINISHED_LOOKBACK
    dates: dict[int, date] = {}
    for entry in journal:
        if (
            entry.kind is MovementKind.CONSUME
            and entry.quantity_after == 0
            and entry.ts >= since
        ):
            day = entry.ts.date()
            if entry.product_id not in dates or day > dates[entry.product_id]:
                dates[entry.product_id] = day
    return dates


def _stalest_zone(
    zone_ids: list[str],
    zone_updates: dict[str, datetime],
    now: datetime,
    stale_days: int,
) -> tuple[str, int] | None:
    stalest: tuple[str, int] | None = None
    for zone_id in zone_ids:
        updated = zone_updates.get(zone_id)
        if updated is None:
            continue
        days = math.floor((now - updated).total_seconds() / 86400)
        if days > stale_days and (stalest is None or days > stalest[1]):
            stalest = (zone_id, days)
    return stalest


def _expiring(
    products: list[Product],
    zones: dict[str, Zone],
    stock: list[StockLevel],
    today: date,
    expiring_days: int,
) -> list[Expiring]:
    by_id = {product.id: product for product in products}
    result = [
        Expiring(
            product=by_id[level.product_id],
            zone=zones[level.zone_id],
            quantity=level.quantity,
            best_before=level.best_before,
            days_left=(level.best_before - today).days,
        )
        for level in stock
        if level.best_before is not None
        and level.quantity > 0
        and level.product_id in by_id
        and (level.best_before - today).days <= expiring_days
    ]
    return sorted(result, key=lambda item: (item.best_before, item.product.name))
