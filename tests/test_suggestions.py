"""Tests for the to-buy, suggestion and expiry rules."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.util import dt as dt_util

from custom_components.quartermaster.core.suggestions import (
    SuggestionReason,
    analyze,
    zone_last_updates,
)
from custom_components.quartermaster.storage.models import (
    Channel,
    JournalEntry,
    MovementKind,
    Product,
    StockLevel,
    Zone,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=dt_util.UTC)
ZONES = [Zone(id="fridge", name="Fridge"), Zone(id="pantry", name="Pantry")]


def _level(
    product_id: int, quantity: float, zone: str = "fridge", **kw: object
) -> StockLevel:
    return StockLevel(
        product_id=product_id,
        zone_id=zone,
        quantity=quantity,
        updated_at=kw.get("updated_at", NOW),  # type: ignore[arg-type]
        best_before=kw.get("best_before"),  # type: ignore[arg-type]
    )


def _finished(product_id: int, when: datetime) -> JournalEntry:
    return JournalEntry(
        id=product_id,
        operation_id="op",
        ts=when,
        product_id=product_id,
        zone_id="fridge",
        kind=MovementKind.CONSUME,
        delta=-1,
        quantity_after=0,
        channel=Channel.SERVICE,
    )


def _run(
    products: list[Product],
    stock: list[StockLevel],
    journal: list[JournalEntry] | None = None,
    listed: set[int] | None = None,
):
    return analyze(
        products=products,
        zones=ZONES,
        stock=stock,
        journal=journal or [],
        listed_product_ids=listed or set(),
        zone_updates=zone_last_updates(stock),
        now=NOW,
        stale_days=7,
        expiring_days=3,
    )


def test_essential_below_minimum_is_to_buy() -> None:
    """Below the minimum: to buy, up to the target, summed over zones."""
    milk = Product(id=1, name="Milk", essential=True, min_stock=2, target_stock=6)
    eggs = Product(id=2, name="Eggs", essential=True, min_stock=2)
    salt = Product(id=3, name="Salt", essential=True, min_stock=1)
    result = _run(
        [milk, eggs, salt],
        [_level(1, 0.5), _level(1, 0.5, "pantry"), _level(2, 1.5), _level(3, 1)],
    )
    assert [(e.product.name, e.total, e.quantity) for e in result.to_buy] == [
        ("Milk", 1.0, 5.0),
        ("Eggs", 1.5, 1.0),  # min - total is 0.5, at least 1
    ]
    assert result.suggestions == []


def test_essential_between_minimum_and_target_is_suggested() -> None:
    """Between minimum and target: suggested only, with have and target."""
    coffee = Product(id=1, name="Coffee", essential=True, min_stock=1, target_stock=4)
    result = _run([coffee], [_level(1, 1, "pantry")])
    assert result.to_buy == []
    [suggestion] = result.suggestions
    assert suggestion.reason is SuggestionReason.BELOW_TARGET
    assert suggestion.details == {"have": 1, "target": 4}


def test_essential_in_stale_zone_is_suggested() -> None:
    """An essential in a zone not updated for more than N days is suggested."""
    butter = Product(id=1, name="Butter", essential=True, min_stock=1)
    old = NOW - timedelta(days=9, hours=1)
    result = _run([butter], [_level(1, 2, updated_at=old)])
    [suggestion] = result.suggestions
    assert suggestion.reason is SuggestionReason.STALE_ZONE
    assert suggestion.details == {"zone": "Fridge", "days": 9}

    recent = _run([butter], [_level(1, 2, updated_at=NOW - timedelta(days=6))])
    assert recent.suggestions == []


def test_finished_recently_is_suggested() -> None:
    """A non-essential finished in the last 30 days and still at zero is suggested."""
    jam = Product(id=1, name="Jam")
    tea = Product(id=2, name="Tea")
    oil = Product(id=3, name="Oil")
    result = _run(
        [jam, tea, oil],
        [_level(1, 0), _level(2, 0), _level(3, 1)],
        journal=[
            _finished(1, NOW - timedelta(days=6)),
            _finished(2, NOW - timedelta(days=40)),
            _finished(3, NOW - timedelta(days=1)),
        ],
    )
    [suggestion] = result.suggestions
    assert suggestion.product.name == "Jam"
    assert suggestion.reason is SuggestionReason.FINISHED_RECENTLY
    assert suggestion.details == {"date": "2026-11-02"}


def test_listed_products_are_not_suggested() -> None:
    """A product already on the list is not suggested, but stays to buy."""
    coffee = Product(id=1, name="Coffee", essential=True, min_stock=1, target_stock=4)
    jam = Product(id=2, name="Jam")
    milk = Product(id=3, name="Milk", essential=True, min_stock=1)
    result = _run(
        [coffee, jam, milk],
        [_level(1, 2), _level(2, 0), _level(3, 0)],
        journal=[_finished(2, NOW - timedelta(days=1))],
        listed={1, 2, 3},
    )
    assert result.suggestions == []
    assert [entry.product.name for entry in result.to_buy] == ["Milk"]


def test_expiring() -> None:
    """Stock expiring within the threshold or already expired is listed, in date order."""
    yogurt = Product(id=1, name="Yogurt")
    cheese = Product(id=2, name="Cheese")
    result = _run(
        [yogurt, cheese],
        [
            _level(1, 2, best_before=date(2026, 11, 10)),
            _level(2, 1, best_before=date(2026, 11, 7)),
            _level(2, 1, "pantry", best_before=date(2026, 12, 1)),
            _level(1, 0, "pantry", best_before=date(2026, 11, 8)),
        ],
    )
    assert [(e.product.name, e.zone.id, e.days_left) for e in result.expiring] == [
        ("Cheese", "fridge", -1),
        ("Yogurt", "fridge", 2),
    ]


def test_zone_last_updates() -> None:
    """The newest update per zone wins; rows without a time are ignored."""
    stock = [
        _level(1, 1, updated_at=NOW - timedelta(days=2)),
        _level(2, 1, updated_at=NOW),
        StockLevel(product_id=3, zone_id="pantry", quantity=1),
    ]
    assert zone_last_updates(stock) == {"fridge": NOW}
