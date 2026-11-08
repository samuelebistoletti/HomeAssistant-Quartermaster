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

from custom_components.quartermaster.core.inventory import (
    InventoryError,
    InventoryService,
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
# Several products changing by more than half: never applied automatically.
LARGE_CHANGE_RATIO = 0.5


@dataclass(slots=True)
class ProposalOutcome:
    """What an analysis led to."""

    applied: list[dict[str, Any]]
    proposal: Proposal | None
    not_seen: list[str]
    unknown: list[str]


class ProposalService:
    """Turns analyses into proposals and applies confirmed ones."""

    def __init__(self, inventory: InventoryService) -> None:
        """Wrap the inventory."""
        self._inventory = inventory
        self._backend = inventory.backend

    async def _async_match(self, item: SeenItem) -> Product | None:
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
    ) -> ProposalOutcome:
        """Compare an analysis with the zone's stock and propose or apply."""
        stock = {
            level.product_id: level.quantity
            for level in await self._inventory.async_get_stock(zone_id=zone.id)
        }
        rows: list[dict[str, Any]] = []
        for key, item in enumerate(analysis.items):
            product = await self._async_match(item)
            current = stock.get(product.id, 0.0) if product else None
            if product is not None and current == item.quantity:
                continue
            rows.append(
                {
                    "key": key,
                    "product_id": product.id if product else None,
                    "product": product.name if product else None,
                    "seen_as": item.product,
                    "current": current,
                    "proposed": item.quantity,
                    "unit": (product.unit if product else None) or item.unit,
                    "confidence": item.confidence,
                    "notes": item.notes,
                }
            )
        large = [
            row
            for row in rows
            if row["product_id"] is not None
            and abs(row["proposed"] - row["current"])
            > LARGE_CHANGE_RATIO * max(row["current"], 1.0)
        ]
        auto_allowed = automatic and analysis.image_usable and len(large) <= 1
        to_apply = [
            row
            for row in rows
            if auto_allowed
            and row["product_id"] is not None
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
                    },
                    expires_at=now + PROPOSAL_TTL,
                )
            )
        return ProposalOutcome(
            applied=to_apply,
            proposal=proposal,
            not_seen=analysis.expected_not_seen,
            unknown=analysis.unknown_items,
        )

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
        proposal = await self._async_get(proposal_id)
        corrections = corrections or {}
        if proposal.zone_id is None:
            raise InventoryError("proposal_not_found", proposal=str(proposal_id))
        chosen = [
            row
            for row in proposal.payload["items"]
            if keys is None or row["key"] in keys
        ]
        counts: list[tuple[int, str, float, float | None]] = []
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
            counts.append((product_id, proposal.zone_id, quantity, row["confidence"]))
            applied.append({**row, "product_id": product_id, "proposed": quantity})
        if counts:
            await self._inventory.async_apply_counts(
                counts, channel=channel, user_id=user_id
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
        return applied
