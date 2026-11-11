"""Confirmations: writes held until the user says yes.

Tool confirmations are single-use ids for LLM tool writes that the spec marks
as needing a yes (new products, essentials, large changes). Proposals from
photos and cameras (Phase 4) also live here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from custom_components.quartermaster.core.inventory import (
    InventoryError,
    InventoryService,
    StockOperation,
    normalize,
)
from custom_components.quartermaster.storage.models import (
    Channel,
    Product,
    Proposal,
    ProposalStatus,
    Zone,
)
from custom_components.quartermaster.vision.schema import SeenItem, ZoneAnalysis

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


PROPOSAL_TTL = timedelta(hours=24)
# Payload kind of proposals from receipts and order emails; others are counts.
PURCHASE = "purchase"
# Payload kind of a camera sighting: a change the user confirms (a product
# taken, or put back), not a count.
MOVEMENT = "movement"
TAKEN = "taken"
RETURNED = "returned"
# Several products changing by more than half: never applied automatically.
LARGE_CHANGE_RATIO = 0.5
# Row flag: a product recorded in the zone that the picture did not show. It
# is proposed at zero but applied only when chosen by key: confirming the
# whole proposal leaves it out (it may only be hidden behind something).
MISSING = "missing"


@dataclass(slots=True)
class ProposalOutcome:
    """What an analysis led to."""

    applied: list[dict[str, Any]]
    proposal: Proposal | None
    not_seen: list[str]
    unknown: list[str]
    # Products a camera saw in a larger quantity than recorded (not proposed).
    more_than_stock: list[str] = field(default_factory=list)


class ProposalService:
    """Turns analyses into proposals and applies confirmed ones."""

    def __init__(self, inventory: InventoryService) -> None:
        """Wrap the inventory."""
        self._inventory = inventory
        self._backend = inventory.backend
        # Called when a proposal is decided or expires (deletes its crop).
        self.on_closed: Callable[[Proposal], Awaitable[None]] | None = None
        # Called when a camera sighting is confirmed, with the quantity moved
        # and the operation written (None for a product put back).
        self.on_movement: (
            Callable[[Proposal, float, str | None], Awaitable[None]] | None
        ) = None
        # Two confirmations of one proposal (two taps, two channels) must not
        # both apply it.
        self._confirm_lock = asyncio.Lock()

    async def async_match(self, item: SeenItem) -> Product | None:
        if item.matched_product_id is not None:
            try:
                return await self._inventory.async_find_product(item.matched_product_id)
            except InventoryError:
                return None
        candidates = await self._inventory.async_search(item.product)
        return candidates[0].product if len(candidates) == 1 else None

    async def async_create(
        self,
        zone: Zone,
        analysis: ZoneAnalysis,
        *,
        channel: Channel,
        user_id: str | None,
        automatic: bool,
        threshold: float,
        allow_increase: bool = True,
    ) -> ProposalOutcome:
        """Compare an analysis with the zone's stock and propose or apply.

        Without `allow_increase` (counts by a camera, which never loads
        goods) rows above the recorded stock are left out and reported.
        """
        stock = {
            level.product_id: level.quantity
            for level in await self._inventory.async_get_stock(zone_id=zone.id)
        }
        rows: list[dict[str, Any]] = []
        by_product: dict[int, dict[str, Any]] = {}
        for key, item in enumerate(analysis.items):
            product = await self.async_match(item)
            if product is not None and (seen := by_product.get(product.id)):
                # The same product listed twice (two shelves, two names):
                # the count of the zone is the sum.
                seen["proposed"] = round(seen["proposed"] + item.quantity, 3)
                seen["confidence"] = min(seen["confidence"], item.confidence)
                continue
            row = {
                "key": key,
                "product_id": product.id if product else None,
                "product": product.name if product else None,
                "seen_as": item.product,
                "current": stock.get(product.id, 0.0) if product else None,
                "proposed": item.quantity,
                "unit": (product.unit if product else None) or item.unit,
                "confidence": item.confidence,
                "notes": item.notes,
            }
            rows.append(row)
            if product is not None:
                by_product[product.id] = row
        rows = [
            row
            for row in rows
            if row["product_id"] is None or row["current"] != row["proposed"]
        ]
        more: list[str] = []
        if not allow_increase:
            more = [
                row["product"]
                for row in rows
                if row["product_id"] is not None and row["proposed"] > row["current"]
            ]
            rows = [
                row
                for row in rows
                if row["product_id"] is not None and row["proposed"] < row["current"]
            ]
        rows += await self._async_missing_rows(
            zone, analysis, stock, seen=set(by_product), first_key=len(analysis.items)
        )
        # A new count of the zone replaces the earlier ones still waiting.
        for older in await self.async_pending():
            if older.zone_id == zone.id and "not_seen" in older.payload:
                older.status = ProposalStatus.EXPIRED
                await self._backend.async_update_proposal(older)
                await self._async_closed(older)
        large = [
            row
            for row in rows
            if row["product_id"] is not None
            and not row.get(MISSING)
            and abs(row["proposed"] - row["current"])
            > LARGE_CHANGE_RATIO * max(row["current"], 1.0)
        ]
        auto_allowed = automatic and analysis.image_usable and len(large) <= 1
        to_apply = [
            row
            for row in rows
            if auto_allowed
            and row["product_id"] is not None
            and not row.get(MISSING)
            and row["confidence"] >= threshold
        ]
        if to_apply:
            await self._inventory.async_apply_counts(
                [
                    (r["product_id"], zone.id, r["proposed"], r["confidence"])
                    for r in to_apply
                ],
                channel=channel,
                user_id=user_id,
            )
        pending = [row for row in rows if row not in to_apply]
        proposal = None
        if pending:
            now = dt_util.utcnow()
            proposal = await self._backend.async_add_proposal(
                Proposal(
                    id=0,
                    created=now,
                    channel=channel,
                    zone_id=zone.id,
                    payload={
                        "items": pending,
                        "not_seen": analysis.expected_not_seen,
                        "unknown": analysis.unknown_items,
                        "overall_confidence": analysis.overall_confidence,
                        "user_id": user_id,
                        "more_than_stock": more,
                    },
                    expires_at=now + PROPOSAL_TTL,
                )
            )
        return ProposalOutcome(
            applied=to_apply,
            proposal=proposal,
            not_seen=analysis.expected_not_seen,
            unknown=analysis.unknown_items,
            more_than_stock=more,
        )

    async def _async_missing_rows(
        self,
        zone: Zone,
        analysis: ZoneAnalysis,
        stock: dict[int, float],
        *,
        seen: set[int],
        first_key: int,
    ) -> list[dict[str, Any]]:
        """Rows at zero for recorded products the picture did not show."""
        rows: list[dict[str, Any]] = []
        # By name or alias only: a category or a stem could name another
        # product than the one the model was told to expect.
        recorded = [
            p
            for p in await self._inventory.async_list_products()
            if stock.get(p.id, 0.0) > 0
        ]
        for name in analysis.expected_not_seen:
            key = normalize(name)
            candidates = [
                p
                for p in recorded
                if p.id not in seen
                and any(normalize(n) == key for n in (p.name, *p.aliases))
            ]
            if len(candidates) != 1:
                continue
            product = candidates[0]
            seen.add(product.id)
            rows.append(
                {
                    "key": first_key + len(rows),
                    "product_id": product.id,
                    "product": product.name,
                    "seen_as": name,
                    "current": stock[product.id],
                    "proposed": 0.0,
                    "unit": product.unit,
                    "confidence": analysis.overall_confidence,
                    "notes": None,
                    MISSING: True,
                }
            )
        return rows

    async def async_propose(
        self,
        zone_id: str,
        rows: list[dict[str, Any]],
        *,
        channel: Channel,
        user_id: str | None,
        extra: dict[str, Any] | None = None,
    ) -> Proposal:
        """Store a proposal built by a caller (camera observations)."""
        now = dt_util.utcnow()
        return await self._backend.async_add_proposal(
            Proposal(
                id=0,
                created=now,
                channel=channel,
                zone_id=zone_id,
                payload={"items": rows, "user_id": user_id, **(extra or {})},
                expires_at=now + PROPOSAL_TTL,
            )
        )

    async def async_propose_movement(
        self,
        zone_id: str,
        product: Product,
        *,
        action: str,
        quantity: float,
        current: float,
        confidence: float,
        seen_as: str,
        source: str,
        operation_id: str | None = None,
    ) -> Proposal:
        """Store a camera sighting of a product taken or put back.

        Confirming a taken product consumes the quantity (the one seen, or
        another the user gives); confirming one put back undoes
        `operation_id`, the consumption confirmed a moment before.
        """
        now = dt_util.utcnow()
        sign = -1 if action == TAKEN else 1
        return await self._backend.async_add_proposal(
            Proposal(
                id=0,
                created=now,
                channel=Channel.CAMERA,
                zone_id=zone_id,
                payload={
                    "kind": MOVEMENT,
                    "action": action,
                    "source": source,
                    "operation_id": operation_id,
                    "user_id": None,
                    "items": [
                        {
                            "key": 0,
                            "product_id": product.id,
                            "product": product.name,
                            "seen_as": seen_as or product.name,
                            "current": current,
                            "proposed": max(current + sign * quantity, 0.0),
                            "unit": product.unit,
                            "confidence": confidence,
                            "notes": action,
                            "moved": quantity,
                        }
                    ],
                },
                expires_at=now + PROPOSAL_TTL,
            )
        )

    async def async_propose_purchases(
        self,
        rows: list[dict[str, Any]],
        *,
        channel: Channel,
        user_id: str | None,
        source: str,
    ) -> Proposal:
        """Store purchases read from a receipt or an order email."""
        now = dt_util.utcnow()
        return await self._backend.async_add_proposal(
            Proposal(
                id=0,
                created=now,
                channel=channel,
                zone_id=None,
                payload={
                    "kind": PURCHASE,
                    "source": source,
                    "items": rows,
                    "user_id": user_id,
                },
                expires_at=now + PROPOSAL_TTL,
            )
        )

    async def _async_confirm_movement(
        self,
        proposal: Proposal,
        correction: dict[str, Any],
        channel: Channel,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        """Apply a confirmed camera sighting as a consumption or an undo."""
        assert proposal.zone_id is not None
        row = proposal.payload["items"][0]
        action = proposal.payload["action"]
        operation_id: str | None = None
        moved = float(row["moved"])
        if action == RETURNED:
            await self._inventory.async_undo_last(
                channel=channel,
                user_id=user_id,
                operation_id=proposal.payload["operation_id"],
            )
        elif "quantity" in correction:
            # A corrected count (the dashboard's field): what is left now.
            left = float(correction["quantity"])
            [movement] = await self._inventory.async_apply_counts(
                [(row["product_id"], proposal.zone_id, left, row["confidence"])],
                channel=channel,
                user_id=user_id,
            )
            operation_id = movement.entry.operation_id
            moved = -movement.entry.delta
        else:
            moved = float(correction.get("moved", moved))
            movement = await self._inventory.async_adjust_stock(
                row["product_id"],
                StockOperation.CONSUME,
                quantity=moved,
                zone_ref=proposal.zone_id,
                channel=channel,
                user_id=user_id,
            )
            operation_id = movement.entry.operation_id
        proposal.status = ProposalStatus.CONFIRMED
        await self._backend.async_update_proposal(proposal)
        await self._async_closed(proposal)
        if self.on_movement is not None:
            await self.on_movement(proposal, moved, operation_id)
        return [{**row, "moved": moved}]

    async def _async_closed(self, proposal: Proposal) -> None:
        if self.on_closed is not None:
            await self.on_closed(proposal)

    async def async_pending(self) -> list[Proposal]:
        """Return pending proposals, expiring the old ones."""
        now = dt_util.utcnow()
        pending = []
        for proposal in await self._backend.async_list_proposals(
            status=ProposalStatus.PENDING
        ):
            if proposal.expires_at <= now:
                proposal.status = ProposalStatus.EXPIRED
                await self._backend.async_update_proposal(proposal)
                await self._async_closed(proposal)
            else:
                pending.append(proposal)
        return pending

    async def _async_get(self, proposal_id: int) -> Proposal:
        for proposal in await self.async_pending():
            if proposal.id == proposal_id:
                return proposal
        raise InventoryError("proposal_not_found", proposal=str(proposal_id))

    async def async_reject(self, proposal_id: int) -> Proposal:
        """Reject a proposal; nothing is written."""
        proposal = await self._async_get(proposal_id)
        proposal.status = ProposalStatus.REJECTED
        await self._backend.async_update_proposal(proposal)
        await self._async_closed(proposal)
        return proposal

    async def async_confirm(
        self,
        proposal_id: int,
        *,
        keys: list[int] | None = None,
        corrections: dict[int, dict[str, Any]] | None = None,
        channel: Channel,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        """Apply the chosen items (all by default), with corrections.

        A correction may name the right product and quantity; the name the
        model used is then learned as an alias of that product.
        """
        async with self._confirm_lock:
            return await self._async_confirm(
                proposal_id, keys, corrections or {}, channel, user_id
            )

    async def _async_confirm(
        self,
        proposal_id: int,
        keys: list[int] | None,
        corrections: dict[int, dict[str, Any]],
        channel: Channel,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        proposal = await self._async_get(proposal_id)
        if proposal.payload.get("kind") == MOVEMENT:
            return await self._async_confirm_movement(
                proposal, corrections.get(0, {}), channel, user_id
            )
        purchase = proposal.payload.get("kind") == PURCHASE
        if proposal.zone_id is None and not purchase:
            raise InventoryError("proposal_not_found", proposal=str(proposal_id))
        # Without keys, everything but the rows not seen: those only by key.
        chosen = [
            row
            for row in proposal.payload["items"]
            if (row["key"] in keys if keys is not None else not row.get(MISSING))
        ]
        counts: list[tuple[int, str, float, float | None]] = []
        bought: list[tuple[int, float]] = []
        applied: list[dict[str, Any]] = []
        for row in chosen:
            correction = corrections.get(row["key"], {})
            product_id = row["product_id"]
            if correction.get("product"):
                product = await self._inventory.async_find_product(
                    correction["product"]
                )
                if product.id != product_id:
                    await self._inventory.async_add_alias(product.id, row["seen_as"])
                product_id = product.id
            if product_id is None:
                continue  # an unknown product needs a correction naming it
            quantity = float(correction.get("quantity", row["proposed"]))
            if purchase:
                bought.append((product_id, quantity))
            else:
                assert proposal.zone_id is not None
                counts.append(
                    (product_id, proposal.zone_id, quantity, row["confidence"])
                )
            applied.append({**row, "product_id": product_id, "proposed": quantity})
        if counts:
            await self._inventory.async_apply_counts(
                counts, channel=channel, user_id=user_id
            )
        if bought:
            await self._inventory.async_record_purchases(
                bought, channel=channel, user_id=user_id
            )
        chosen_keys = {row["key"] for row in chosen}
        remaining = [
            row for row in proposal.payload["items"] if row["key"] not in chosen_keys
        ]
        if remaining:
            # Items not chosen stay pending in the same proposal.
            proposal.payload = {**proposal.payload, "items": remaining}
        else:
            proposal.status = ProposalStatus.CONFIRMED
        await self._backend.async_update_proposal(proposal)
        if proposal.status is ProposalStatus.CONFIRMED:
            await self._async_closed(proposal)
        return applied
