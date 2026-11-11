"""Output structure for ai_task.generate_data and validation of the result.

Spec §4.2: the nested structure lists the items seen; for providers that
handle nested structures badly the text mode asks for the items as a JSON
array in a text field. Either way the result is validated here and anything
inconsistent is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
from typing import Any

from custom_components.quartermaster.core.inventory import MAX_QUANTITY


class StructureMode(StrEnum):
    """How the items are requested from the model."""

    NESTED = "nested"
    TEXT = "text"


_NUMBER = {"number": {"min": 0, "max": MAX_QUANTITY, "step": 0.01, "mode": "box"}}
_SCORE = {"number": {"min": 0, "max": 1, "step": 0.01, "mode": "box"}}
_TEXT_LIST = {"text": {"multiple": True}}

_COMMON: dict[str, Any] = {
    "image_usable": {
        "description": "Whether the image shows the zone clearly enough to analyze",
        "selector": {"boolean": {}},
        "required": True,
    },
    "image_issues": {
        "description": "Problems with the image, such as glare, blur or obstruction",
        "selector": _TEXT_LIST,
    },
    "expected_not_seen": {
        "description": "Expected products that are not visible",
        "selector": _TEXT_LIST,
    },
    "unknown_items": {
        "description": "Things seen that are not recognizable products",
        "selector": _TEXT_LIST,
    },
    "overall_confidence": {
        "description": "Confidence in the whole answer, from 0 to 1",
        "selector": _SCORE,
        "required": True,
    },
}

_ITEM_FIELDS: dict[str, Any] = {
    "product": {"selector": {"text": {}}, "required": True},
    "matched_product_id": {"selector": _NUMBER},
    "quantity": {"selector": _NUMBER, "required": True},
    "unit": {"selector": {"text": {}}},
    "confidence": {"selector": _SCORE, "required": True},
    "notes": {"selector": {"text": {}}},
}

NESTED_STRUCTURE: dict[str, Any] = {
    **_COMMON,
    "items": {
        "description": "Products seen, with the number of packages",
        "required": True,
        "selector": {"object": {"multiple": True, "fields": _ITEM_FIELDS}},
    },
}

TEXT_STRUCTURE: dict[str, Any] = {
    **_COMMON,
    "items_json": {
        "description": (
            "JSON array of objects with product, matched_product_id, quantity, "
            "unit, confidence (0 to 1) and notes"
        ),
        "selector": {"text": {"multiline": True}},
        "required": True,
    },
}


def structure_for(mode: StructureMode) -> dict[str, Any]:
    """Return the structure to send for the mode."""
    return NESTED_STRUCTURE if mode is StructureMode.NESTED else TEXT_STRUCTURE


@dataclass(slots=True)
class SeenItem:
    """A product the model saw."""

    product: str
    quantity: float
    confidence: float
    matched_product_id: int | None = None
    unit: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class ZoneAnalysis:
    """A validated analysis result."""

    image_usable: bool
    overall_confidence: float
    items: list[SeenItem] = field(default_factory=list)
    image_issues: list[str] = field(default_factory=list)
    expected_not_seen: list[str] = field(default_factory=list)
    unknown_items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the result as stored in zone snapshots."""
        return {
            "image_usable": self.image_usable,
            "overall_confidence": self.overall_confidence,
            "items": [
                {
                    "product": item.product,
                    "matched_product_id": item.matched_product_id,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "confidence": item.confidence,
                    "notes": item.notes,
                }
                for item in self.items
            ],
            "image_issues": self.image_issues,
            "expected_not_seen": self.expected_not_seen,
            "unknown_items": self.unknown_items,
        }


class InvalidResultError(ValueError):
    """The model's answer cannot be used."""


def _score(value: Any) -> float:
    try:
        number = float(value)
    except TypeError, ValueError:
        return 0.0
    return min(max(number, 0.0), 1.0)


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [" ".join(str(v).split()) for v in value if str(v).strip()][:50]


def _item(raw: Any, expected_ids: set[int]) -> SeenItem | None:
    if not isinstance(raw, dict):
        return None
    name = " ".join(str(raw.get("product") or "").split())
    try:
        quantity = float(raw.get("quantity"))  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None
    if not name or not 0 <= quantity <= MAX_QUANTITY:
        return None
    matched: int | None = None
    try:
        candidate = int(raw.get("matched_product_id"))  # type: ignore[arg-type]
    except TypeError, ValueError:
        candidate = None
    # Only ids we offered count; anything else is an invented match.
    if candidate in expected_ids:
        matched = candidate
    unit = raw.get("unit")
    notes = raw.get("notes")
    return SeenItem(
        product=name[:100],
        quantity=round(quantity, 3),
        confidence=_score(raw.get("confidence")),
        matched_product_id=matched,
        unit=str(unit)[:30] if unit else None,
        notes=str(notes)[:200] if notes else None,
    )


def parse_result(data: Any, expected_ids: set[int]) -> ZoneAnalysis:
    """Validate the `data` of an ai_task response in either mode."""
    if not isinstance(data, dict):
        raise InvalidResultError("not an object")
    if "items" in data:
        raw_items = data["items"]
    elif "items_json" in data:
        try:
            raw_items = json.loads(str(data["items_json"]))
        except ValueError as err:
            raise InvalidResultError("items_json is not JSON") from err
    else:
        raise InvalidResultError("no items")
    if not isinstance(raw_items, list):
        raise InvalidResultError("items is not a list")
    items = [item for raw in raw_items if (item := _item(raw, expected_ids))]
    return ZoneAnalysis(
        image_usable=_flag(data.get("image_usable", True)),
        overall_confidence=_score(data.get("overall_confidence")),
        items=items[:100],
        image_issues=_texts(data.get("image_issues")),
        expected_not_seen=_texts(data.get("expected_not_seen")),
        unknown_items=_texts(data.get("unknown_items")),
    )


def result_from_dict(data: dict[str, Any]) -> ZoneAnalysis:
    """Rebuild a stored result (zone snapshot)."""
    return parse_result(
        data,
        {
            item["matched_product_id"]
            for item in data.get("items", [])
            if isinstance(item, dict)
            and isinstance(item.get("matched_product_id"), int)
        },
    )


def _flag(value: Any) -> bool:
    """Read a boolean that some models send as text ("false")."""
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "0", "")
    return bool(value)
