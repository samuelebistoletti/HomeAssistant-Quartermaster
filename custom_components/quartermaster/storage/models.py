"""Records exchanged between the inventory service and a storage backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class MovementKind(StrEnum):
    """Kind of a journal movement."""

    PURCHASE = "purchase"
    CONSUME = "consume"
    INVENTORY = "inventory"
    CORRECTION = "correction"
    UNDO = "undo"
    IMPORT = "import"


class Channel(StrEnum):
    """Channel a movement came from."""

    ALEXA = "alexa"
    ASSIST = "assist"
    TELEGRAM = "telegram"
    DASHBOARD = "dashboard"
    CAMERA = "camera"
    SERVICE = "service"
    SCHEDULER = "scheduler"


@dataclass(slots=True)
class Zone:
    """A place where products are kept (pantry, fridge, shelf)."""

    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    check_schedule: str | None = None
    reminder_days: int | None = None
    confidence_threshold: float | None = None


@dataclass(slots=True)
class Product:
    """A product tracked by the inventory; `id` is 0 until stored."""

    id: int
    name: str
    aliases: list[str] = field(default_factory=list)
    category: str | None = None
    default_zone_id: str | None = None
    unit: str | None = None
    essential: bool = False
    min_stock: float | None = None
    target_stock: float | None = None
    visual_hints: str | None = None
    barcode: str | None = None
    shelf_life_days: int | None = None
    archived: bool = False


@dataclass(slots=True)
class StockLevel:
    """Quantity of one product in one zone."""

    product_id: int
    zone_id: str
    quantity: float
    best_before: date | None = None
    opened_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True, kw_only=True)
class StockChange:
    """A change to apply atomically with its journal entry.

    With `set_quantity` the quantity becomes that value; otherwise `delta` is
    added. The result is never below zero; the journal stores the applied delta.
    """

    product_id: int
    zone_id: str
    kind: MovementKind
    channel: Channel
    delta: float = 0.0
    set_quantity: float | None = None
    best_before: date | None = None
    user_id: str | None = None
    confidence: float | None = None
    image_ref: str | None = None
    undoes_id: int | None = None


@dataclass(slots=True, kw_only=True)
class JournalEntry:
    """One recorded movement; `operation_id` groups the movements of one request."""

    id: int
    operation_id: str
    ts: datetime
    product_id: int
    zone_id: str
    kind: MovementKind
    delta: float
    quantity_after: float
    channel: Channel
    user_id: str | None = None
    confidence: float | None = None
    image_ref: str | None = None
    undoes_id: int | None = None


class ShoppingStatus(StrEnum):
    """Status of a shopping list item (same values as the todo platform)."""

    NEEDS_ACTION = "needs_action"
    COMPLETED = "completed"


class ShoppingOrigin(StrEnum):
    """Why an item is on the shopping list."""

    AUTO = "auto"
    SUGGESTION = "suggestion"
    MANUAL = "manual"


@dataclass(slots=True, kw_only=True)
class ShoppingItem:
    """One shopping list item; `id` is 0 until stored."""

    id: int
    text: str
    status: ShoppingStatus
    origin: ShoppingOrigin
    product_id: int | None = None
    quantity: float | None = None
    note: str | None = None
    position: int = 0
    completed_at: datetime | None = None
    external_uid: str | None = None


class ProposalStatus(StrEnum):
    """State of a proposal of changes."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(slots=True, kw_only=True)
class Proposal:
    """Changes recognized from an image, waiting for confirmation."""

    id: int
    created: datetime
    channel: Channel
    zone_id: str | None
    payload: dict[str, Any]
    expires_at: datetime
    status: ProposalStatus = ProposalStatus.PENDING


@dataclass(slots=True, kw_only=True)
class ZoneSnapshot:
    """Last analysis of a zone, to skip unchanged images."""

    zone_id: str
    ts: datetime
    perceptual_hash: str | None
    overall_confidence: float | None
    result: dict[str, Any]
