"""Inventory service: zones, products, essentials, stock, movements and undo.

Every write goes through this service, which journals it in the same
transaction as the stock change.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util, slugify

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.storage.base import StorageBackend
from custom_components.quartermaster.storage.models import (
    Channel,
    JournalEntry,
    MovementKind,
    Product,
    StockChange,
    StockLevel,
    Zone,
)

MAX_QUANTITY = 100_000.0


class StockOperation(StrEnum):
    """What a stock adjustment does."""

    CONSUME = "consume"
    PURCHASE = "purchase"
    FINISH = "finish"


class InventoryError(ServiceValidationError):
    """A request the inventory cannot carry out, with a translated message."""

    def __init__(self, key: str, **placeholders: str) -> None:
        """Build the error from a key of the `exceptions` translations."""
        super().__init__(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders=placeholders or None,
        )


@dataclass(slots=True)
class Movement:
    """A journal entry with the product and zone it refers to."""

    entry: JournalEntry
    product: Product
    zone: Zone


def normalize(text: str) -> str:
    """Return text for case-insensitive, whitespace-insensitive matching."""
    return " ".join(text.split()).casefold()


def _check_quantity(value: float) -> None:
    if not 0 <= value <= MAX_QUANTITY:
        raise InventoryError("invalid_quantity", quantity=str(value))


class InventoryService:
    """Domain operations on top of a storage backend."""

    def __init__(self, backend: StorageBackend) -> None:
        """Wrap an opened backend."""
        self.backend = backend
        # Serializes read-then-write sequences (resolution, undo).
        self._write_lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call `listener` after every successful write; return the remover."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @asynccontextmanager
    async def _writing(self) -> AsyncIterator[None]:
        async with self._write_lock:
            yield
        for listener in list(self._listeners):
            listener()

    # Zones

    async def async_list_zones(self) -> list[Zone]:
        """Return every zone."""
        return await self.backend.async_list_zones()

    async def async_add_zone(
        self, name: str, *, aliases: list[str] | None = None, zone_id: str | None = None
    ) -> Zone:
        """Create a zone; its id is derived from the name unless given."""
        name = " ".join(name.split())
        if not name:
            raise InventoryError("invalid_name")
        zone_id = zone_id or slugify(name)
        async with self._writing():
            zones = await self.backend.async_list_zones()
            if any(
                zone.id == zone_id or normalize(zone.name) == normalize(name)
                for zone in zones
            ):
                raise InventoryError("zone_exists", zone=name)
            zone = Zone(id=zone_id, name=name, aliases=list(aliases or []))
            await self.backend.async_save_zone(zone)
        return zone

    async def async_remove_zone(self, zone_ref: str) -> Zone:
        """Delete a zone that holds no stock and return it."""
        async with self._writing():
            zone = await self.async_find_zone(zone_ref)
            stock = await self.backend.async_list_stock(zone_id=zone.id)
            if any(level.quantity > 0 for level in stock):
                raise InventoryError("zone_not_empty", zone=zone.name)
            await self.backend.async_delete_zone(zone.id)
        return zone

    async def async_find_zone(self, ref: str) -> Zone:
        """Resolve a zone by id, name or alias."""
        key = normalize(ref)
        zones = await self.backend.async_list_zones()
        for zone in zones:
            if zone.id == ref or normalize(zone.name) == key:
                return zone
        matches = [
            zone for zone in zones if key in {normalize(a) for a in zone.aliases}
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise InventoryError(
                "ambiguous_zone",
                zone=ref,
                candidates=", ".join(zone.name for zone in matches),
            )
        raise InventoryError("zone_not_found", zone=ref)

    # Products and essentials

    async def async_list_products(
        self, *, include_archived: bool = False
    ) -> list[Product]:
        """Return the products."""
        return await self.backend.async_list_products(include_archived=include_archived)

    async def async_find_product(self, ref: str | int) -> Product:
        """Resolve an active product by id, name or alias."""
        products = await self.backend.async_list_products(include_archived=False)
        if isinstance(ref, int) or ref.isdigit():
            product_id = int(ref)
            for product in products:
                if product.id == product_id:
                    return product
            raise InventoryError("product_not_found", product=str(ref))
        key = normalize(ref)
        for product in products:
            if normalize(product.name) == key:
                return product
        matches = [
            product
            for product in products
            if key in {normalize(alias) for alias in product.aliases}
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise InventoryError(
                "ambiguous_product",
                product=ref,
                candidates=", ".join(product.name for product in matches),
            )
        raise InventoryError("product_not_found", product=ref)

    async def async_add_product(
        self,
        name: str,
        *,
        aliases: list[str] | None = None,
        category: str | None = None,
        default_zone: str | None = None,
        unit: str | None = None,
        essential: bool = False,
        min_stock: float | None = None,
        target_stock: float | None = None,
        visual_hints: str | None = None,
        barcode: str | None = None,
        shelf_life_days: int | None = None,
    ) -> Product:
        """Create a product with a unique name."""
        name = " ".join(name.split())
        if not name:
            raise InventoryError("invalid_name")
        _check_essential(name, essential, min_stock, target_stock)
        async with self._writing():
            zone_id = (
                (await self.async_find_zone(default_zone)).id if default_zone else None
            )
            products = await self.backend.async_list_products(include_archived=False)
            if any(normalize(p.name) == normalize(name) for p in products):
                raise InventoryError("product_exists", product=name)
            return await self.backend.async_add_product(
                Product(
                    id=0,
                    name=name,
                    aliases=[" ".join(alias.split()) for alias in aliases or []],
                    category=category,
                    default_zone_id=zone_id,
                    unit=unit,
                    essential=essential,
                    min_stock=min_stock,
                    target_stock=target_stock,
                    visual_hints=visual_hints,
                    barcode=barcode,
                    shelf_life_days=shelf_life_days,
                )
            )

    async def async_set_essential(
        self,
        product_ref: str | int,
        *,
        essential: bool,
        min_stock: float | None = None,
        target_stock: float | None = None,
    ) -> Product:
        """Mark a product as essential with its stock levels, or unmark it."""
        async with self._writing():
            product = await self.async_find_product(product_ref)
            if essential:
                min_stock = product.min_stock if min_stock is None else min_stock
                target_stock = (
                    product.target_stock if target_stock is None else target_stock
                )
            _check_essential(product.name, essential, min_stock, target_stock)
            product.essential = essential
            product.min_stock = min_stock
            product.target_stock = target_stock
            await self.backend.async_update_product(product)
        return product

    # Stock and movements

    async def async_get_stock(
        self, *, product_id: int | None = None, zone_id: str | None = None
    ) -> list[StockLevel]:
        """Return stock rows."""
        return await self.backend.async_list_stock(
            product_id=product_id, zone_id=zone_id
        )

    async def async_adjust_stock(
        self,
        product_ref: str | int,
        operation: StockOperation,
        *,
        quantity: float | None = None,
        zone_ref: str | None = None,
        best_before: date | None = None,
        channel: Channel,
        user_id: str | None = None,
    ) -> Movement:
        """Consume, buy or finish a product in one zone."""
        if quantity is not None:
            _check_quantity(quantity)
        async with self._writing():
            product = await self.async_find_product(product_ref)
            zone = await self._movement_zone(product, operation, zone_ref)
            if operation is StockOperation.PURCHASE:
                change = StockChange(
                    product_id=product.id,
                    zone_id=zone.id,
                    kind=MovementKind.PURCHASE,
                    delta=1.0 if quantity is None else quantity,
                    best_before=best_before,
                    channel=channel,
                    user_id=user_id,
                )
            elif operation is StockOperation.CONSUME:
                change = StockChange(
                    product_id=product.id,
                    zone_id=zone.id,
                    kind=MovementKind.CONSUME,
                    delta=-(1.0 if quantity is None else quantity),
                    channel=channel,
                    user_id=user_id,
                )
            else:
                change = StockChange(
                    product_id=product.id,
                    zone_id=zone.id,
                    kind=MovementKind.CONSUME,
                    set_quantity=0.0,
                    channel=channel,
                    user_id=user_id,
                )
            [entry] = await self.backend.async_apply_changes([change])
        return Movement(entry, product, zone)

    async def _movement_zone(
        self, product: Product, operation: StockOperation, zone_ref: str | None
    ) -> Zone:
        if zone_ref:
            return await self.async_find_zone(zone_ref)
        zones = {zone.id: zone for zone in await self.backend.async_list_zones()}
        stock = await self.backend.async_list_stock(product_id=product.id)
        if operation is StockOperation.PURCHASE:
            if product.default_zone_id in zones:
                return zones[product.default_zone_id]
            known = [level.zone_id for level in stock]
            if len(known) == 1:
                return zones[known[0]]
            raise InventoryError("zone_required", product=product.name)
        held = [level.zone_id for level in stock if level.quantity > 0]
        if not held:
            raise InventoryError("not_in_stock", product=product.name)
        if len(held) > 1:
            raise InventoryError(
                "product_in_many_zones",
                product=product.name,
                candidates=", ".join(zones[zone_id].name for zone_id in held),
            )
        return zones[held[0]]

    async def async_set_stock(
        self,
        product_ref: str | int,
        quantity: float,
        *,
        zone_ref: str | None = None,
        best_before: date | None = None,
        channel: Channel,
        user_id: str | None = None,
        kind: MovementKind = MovementKind.INVENTORY,
    ) -> Movement:
        """Set the counted quantity of a product in a zone."""
        _check_quantity(quantity)
        async with self._writing():
            product = await self.async_find_product(product_ref)
            if zone_ref:
                zone = await self.async_find_zone(zone_ref)
            else:
                zone = await self._movement_zone(product, StockOperation.PURCHASE, None)
            [entry] = await self.backend.async_apply_changes(
                [
                    StockChange(
                        product_id=product.id,
                        zone_id=zone.id,
                        kind=kind,
                        set_quantity=quantity,
                        best_before=best_before,
                        channel=channel,
                        user_id=user_id,
                    )
                ]
            )
        return Movement(entry, product, zone)

    async def async_undo_last(
        self,
        *,
        channel: Channel,
        only_channel: bool = False,
        user_id: str | None = None,
        within: timedelta | None = None,
    ) -> tuple[list[Movement], list[Movement]]:
        """Revert the newest operation that is not undone yet, as a whole.

        With `only_channel` the search is limited to that channel and user;
        otherwise any movement qualifies. The undo is one operation journaled
        with `channel` and `user_id`. Returns the original movements and the
        undo movements.
        """
        since = dt_util.utcnow() - within if within is not None else None
        async with self._writing():
            originals = await self.backend.async_last_undoable(
                channel=channel if only_channel else None,
                user_id=user_id if only_channel else None,
                since=since,
            )
            if not originals:
                raise InventoryError("nothing_to_undo")
            zones = {zone.id: zone for zone in await self.backend.async_list_zones()}
            if any(original.zone_id not in zones for original in originals):
                raise InventoryError("undo_zone_missing")
            products = {
                product.id: product
                for product in await self.backend.async_list_products(
                    include_archived=True
                )
            }
            undos = await self.backend.async_apply_changes(
                [
                    StockChange(
                        product_id=original.product_id,
                        zone_id=original.zone_id,
                        kind=MovementKind.UNDO,
                        delta=-original.delta,
                        channel=channel,
                        user_id=user_id,
                        undoes_id=original.id,
                    )
                    for original in reversed(originals)
                ]
            )

        def movement(entry: JournalEntry) -> Movement:
            return Movement(entry, products[entry.product_id], zones[entry.zone_id])

        return [movement(e) for e in originals], [movement(e) for e in undos]

    async def async_list_journal(
        self, *, limit: int = 50, product_id: int | None = None
    ) -> list[JournalEntry]:
        """Return the newest movements first."""
        return await self.backend.async_list_journal(limit=limit, product_id=product_id)

    # Whole-inventory transfer

    async def async_dump(self) -> dict[str, list[dict[str, Any]]]:
        """Return the whole inventory."""
        return await self.backend.async_dump()

    async def async_restore(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        """Replace the whole inventory."""
        async with self._writing():
            await self.backend.async_restore(tables)


def _check_essential(
    name: str, essential: bool, min_stock: float | None, target_stock: float | None
) -> None:
    for value in (min_stock, target_stock):
        if value is not None:
            _check_quantity(value)
    if essential and min_stock is None:
        raise InventoryError("min_stock_required", product=name)
    if min_stock is not None and target_stock is not None and target_stock < min_stock:
        raise InventoryError("target_below_min", product=name)
