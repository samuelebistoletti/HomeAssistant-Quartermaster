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


def shelf_life_date(product: Product) -> date | None:
    """Return the best-before date of a purchase today, from the shelf life."""
    if not product.shelf_life_days:
        return None
    return dt_util.now().date() + timedelta(days=product.shelf_life_days)


def normalize(text: str) -> str:
    """Return text for case-insensitive, whitespace-insensitive matching."""
    return " ".join(text.split()).casefold()


_STEM_ENDINGS = "aeiouàèéìòóù"


def stem(text: str) -> str:
    """Return a rough stem so singular and plural forms match.

    "formaggio" and "formaggi" both give "formagg"; "cheeses" and "cheese"
    give "chees". Only used to find candidates, never to write.
    """
    words = []
    for word in normalize(text).split():
        if len(word) > 3 and word.endswith("es"):
            word = word[:-2]
        elif len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        trimmed = word.rstrip(_STEM_ENDINGS)
        words.append(trimmed if len(trimmed) >= 3 else word)
    return " ".join(words)


@dataclass(slots=True)
class Candidate:
    """A product that may be the one meant, with where it is kept."""

    product: Product
    levels: list[StockLevel]

    @property
    def total(self) -> float:
        """Quantity in all zones."""
        return round(sum(level.quantity for level in self.levels), 3)


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

    async def async_update_zone(
        self,
        zone_ref: str,
        *,
        name: str | None = None,
        aliases: list[str] | None = None,
        sources: list[dict[str, Any]] | None = None,
        check_schedule: str | None = None,
        reminder_days: int | None = None,
        confidence_threshold: float | None = None,
    ) -> Zone:
        """Change a zone; `check_schedule=""` or 0 values clear the field."""
        async with self._writing():
            zone = await self.async_find_zone(zone_ref)
            if name is not None:
                name = " ".join(name.split())
                if not name:
                    raise InventoryError("invalid_name")
                others = await self.backend.async_list_zones()
                if any(
                    z.id != zone.id and normalize(z.name) == normalize(name)
                    for z in others
                ):
                    raise InventoryError("zone_exists", zone=name)
                zone.name = name
            if aliases is not None:
                zone.aliases = [" ".join(a.split()) for a in aliases if a.strip()]
            if sources is not None:
                zone.sources = sources
            if check_schedule is not None:
                zone.check_schedule = check_schedule or None
            if reminder_days is not None:
                zone.reminder_days = reminder_days or None
            if confidence_threshold is not None:
                zone.confidence_threshold = confidence_threshold or None
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
            if isinstance(ref, int):
                raise InventoryError("product_not_found", product=str(ref))
            # Digits that are no id may be a name ("1664").
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
            if archived := await self._archived_named(name):
                # Adding an archived product brings it back with what was given.
                archived.archived = False
                for attribute, value in (
                    ("category", category),
                    ("default_zone_id", zone_id),
                    ("unit", unit),
                    ("visual_hints", visual_hints),
                    ("barcode", barcode),
                    ("shelf_life_days", shelf_life_days),
                ):
                    if value is not None:
                        setattr(archived, attribute, value)
                if essential:
                    archived.essential = True
                    archived.min_stock, archived.target_stock = min_stock, target_stock
                await self.backend.async_update_product(archived)
                return archived
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

    async def _archived_named(self, ref: str | int) -> Product | None:
        """Return the archived product with this id or name, if any."""
        key = normalize(str(ref))
        for product in await self.backend.async_list_products(include_archived=True):
            if product.archived and (
                str(product.id) == str(ref) or normalize(product.name) == key
            ):
                return product
        return None

    async def async_update_product(
        self, product_ref: str | int, changes: dict[str, Any]
    ) -> Product:
        """Change a product's details; a key set to None clears that detail.

        Keys: name, aliases (the whole list), category, unit, default_zone,
        shelf_life_days, visual_hints, essential, min_stock, target_stock.
        Names and aliases stay unique among active products.
        """
        async with self._writing():
            product = await self.async_find_product(product_ref)
            others = [
                p
                for p in await self.backend.async_list_products(include_archived=False)
                if p.id != product.id
            ]
            taken = {normalize(n) for p in others for n in (p.name, *p.aliases)}
            if "name" in changes:
                name = " ".join(str(changes["name"] or "").split())
                if not name:
                    raise InventoryError("invalid_name")
                if normalize(name) in {normalize(p.name) for p in others}:
                    raise InventoryError("product_exists", product=name)
                if normalize(name) in taken:
                    raise InventoryError("alias_taken", alias=name)
                product.name = name
            if "aliases" in changes:
                aliases: list[str] = []
                for alias in changes["aliases"] or []:
                    alias = " ".join(str(alias).split())
                    key = normalize(alias)
                    if not alias or key == normalize(product.name):
                        continue
                    if key in taken:
                        raise InventoryError("alias_taken", alias=alias)
                    if key not in {normalize(a) for a in aliases}:
                        aliases.append(alias)
                product.aliases = aliases
            if "default_zone" in changes:
                product.default_zone_id = (
                    (await self.async_find_zone(changes["default_zone"])).id
                    if changes["default_zone"]
                    else None
                )
            for key in ("category", "unit", "visual_hints"):
                if key in changes:
                    setattr(
                        product, key, " ".join(str(changes[key] or "").split()) or None
                    )
            if "shelf_life_days" in changes:
                product.shelf_life_days = changes["shelf_life_days"] or None
            for key in ("essential", "min_stock", "target_stock"):
                if key in changes:
                    setattr(product, key, changes[key])
            product.essential = bool(product.essential)
            _check_essential(
                product.name, product.essential, product.min_stock, product.target_stock
            )
            await self.backend.async_update_product(product)
        return product

    async def async_archive_product(
        self,
        product_ref: str | int,
        *,
        archived: bool = True,
        channel: Channel,
        user_id: str | None = None,
    ) -> tuple[Product, list[Movement]]:
        """Archive a product, setting its stock to zero, or bring it back.

        The stock set to zero is one operation: undoing it restores the
        stock and the product. A product brought back starts from zero.
        """
        async with self._writing():
            if not archived:
                product = await self._archived_named(product_ref)
                if product is None:
                    raise InventoryError("product_not_found", product=str(product_ref))
                await self._check_free_name(product)
                product.archived = False
                await self.backend.async_update_product(product)
                return product, []
            product = await self.async_find_product(product_ref)
            zones = {zone.id: zone for zone in await self.backend.async_list_zones()}
            levels = [
                level
                for level in await self.backend.async_list_stock(product_id=product.id)
                if level.quantity > 0
            ]
            entries = (
                await self.backend.async_apply_changes(
                    [
                        StockChange(
                            product_id=product.id,
                            zone_id=level.zone_id,
                            kind=MovementKind.ARCHIVE,
                            set_quantity=0.0,
                            channel=channel,
                            user_id=user_id,
                        )
                        for level in levels
                    ]
                )
                if levels
                else []
            )
            product.archived = True
            await self.backend.async_update_product(product)
        return product, [Movement(e, product, zones[e.zone_id]) for e in entries]

    async def _check_free_name(self, product: Product) -> None:
        """Refuse to bring back a product whose name is now someone else's."""
        key = normalize(product.name)
        for other in await self.backend.async_list_products(include_archived=False):
            if key in {normalize(n) for n in (other.name, *other.aliases)}:
                raise InventoryError("product_exists", product=product.name)

    async def async_move_stock(
        self,
        product_ref: str | int,
        *,
        to_zone: str,
        from_zone: str | None = None,
        quantity: float | None = None,
        channel: Channel,
        user_id: str | None = None,
    ) -> list[Movement]:
        """Move stock to another zone as one operation (all of it by default).

        The best-before date goes with the goods: the earlier of the two
        when the zone already holds some.
        """
        if quantity is not None:
            _check_quantity(quantity)
        async with self._writing():
            product = await self.async_find_product(product_ref)
            source = await self._movement_zone(
                product, StockOperation.CONSUME, from_zone
            )
            target = await self.async_find_zone(to_zone)
            if target.id == source.id:
                raise InventoryError("same_zone", zone=target.name)
            levels = {
                level.zone_id: level
                for level in await self.backend.async_list_stock(product_id=product.id)
            }
            held = levels.get(source.id)
            available = held.quantity if held else 0.0
            if available <= 0:
                raise InventoryError("not_in_stock", product=product.name)
            moved = available if quantity is None else quantity
            if moved > available:
                raise InventoryError(
                    "not_enough_stock",
                    product=product.name,
                    quantity=f"{available:g}",
                    zone=source.name,
                )
            there = levels.get(target.id)
            dates = [
                level.best_before
                for level in (held, there)
                if level is not None and level.quantity > 0 and level.best_before
            ]
            entries = await self.backend.async_apply_changes(
                [
                    StockChange(
                        product_id=product.id,
                        zone_id=source.id,
                        kind=MovementKind.MOVE,
                        delta=-moved,
                        channel=channel,
                        user_id=user_id,
                    ),
                    StockChange(
                        product_id=product.id,
                        zone_id=target.id,
                        kind=MovementKind.MOVE,
                        delta=moved,
                        best_before=min(dates) if dates else None,
                        channel=channel,
                        user_id=user_id,
                    ),
                ]
            )
        zones = {source.id: source, target.id: target}
        return [Movement(e, product, zones[e.zone_id]) for e in entries]

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

    async def async_search(
        self, query: str, *, zone_id: str | None = None, in_stock: bool = False
    ) -> list[Candidate]:
        """Find products by name or alias, then by stem, then by category.

        With `zone_id` only products with stock in that zone qualify; with
        `in_stock` only products with a positive quantity. The best matches
        come first, ordered by quantity in the zone.
        """
        products = await self.backend.async_list_products(include_archived=False)
        stock = await self.backend.async_list_stock()
        key, key_stem = normalize(query), stem(query)

        def names(product: Product) -> list[str]:
            return [product.name, *product.aliases]

        tiers: tuple[Callable[[Product], bool], ...] = (
            lambda p: any(normalize(n) == key for n in names(p)),
            lambda p: any(stem(n) == key_stem for n in names(p)),
            lambda p: p.category is not None and stem(p.category) == key_stem,
        )
        for matches in tiers:
            candidates = []
            for product in products:
                if not matches(product):
                    continue
                levels = [
                    level
                    for level in stock
                    if level.product_id == product.id
                    and (zone_id is None or level.zone_id == zone_id)
                    and (not in_stock or level.quantity > 0)
                ]
                if (zone_id is not None or in_stock) and not levels:
                    continue
                candidates.append(Candidate(product, levels))
            if candidates:
                return sorted(candidates, key=lambda c: (-c.total, c.product.name))
        return []

    async def async_mark_opened(
        self, product_ref: str | int, *, zone_ref: str | None = None
    ) -> Zone:
        """Record that a product was opened in a zone (not a movement)."""
        async with self._writing():
            product = await self.async_find_product(product_ref)
            if zone_ref:
                zone = await self.async_find_zone(zone_ref)
            else:
                zone = await self._movement_zone(product, StockOperation.CONSUME, None)
            await self.backend.async_set_opened(product.id, zone.id, dt_util.utcnow())
        return zone

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
                    best_before=best_before or shelf_life_date(product),
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
        operation_id: str | None = None,
    ) -> tuple[list[Movement], list[Movement]]:
        """Revert the newest operation that is not undone yet, as a whole.

        With `operation_id` that operation is reverted instead. With
        `only_channel` the search is limited to that channel and user;
        otherwise any movement qualifies. The undo is one operation journaled
        with `channel` and `user_id`. Returns the original movements and the
        undo movements.
        """
        since = dt_util.utcnow() - within if within is not None else None
        async with self._writing():
            if operation_id is not None:
                originals = await self.backend.async_operation_entries(operation_id)
            else:
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
            # Undoing an archive brings the product back with its stock.
            restored = {
                original.product_id
                for original in originals
                if original.kind is MovementKind.ARCHIVE
                and products[original.product_id].archived
            }
            for product_id in restored:
                await self._check_free_name(products[product_id])
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
            for product_id in restored:
                products[product_id].archived = False
                await self.backend.async_update_product(products[product_id])

        def movement(entry: JournalEntry) -> Movement:
            return Movement(entry, products[entry.product_id], zones[entry.zone_id])

        return [movement(e) for e in originals], [movement(e) for e in undos]

    async def async_apply_counts(
        self,
        counts: list[tuple[int, str, float, float | None]],
        *,
        channel: Channel,
        user_id: str | None = None,
    ) -> list[Movement]:
        """Set counted quantities (product id, zone id, quantity, confidence).

        All counts form one operation, undone together.
        """
        for _, _, quantity, _ in counts:
            _check_quantity(quantity)
        async with self._writing():
            products = {
                p.id: p
                for p in await self.backend.async_list_products(include_archived=True)
            }
            zones = {zone.id: zone for zone in await self.backend.async_list_zones()}
            entries = await self.backend.async_apply_changes(
                [
                    StockChange(
                        product_id=product_id,
                        zone_id=zone_id,
                        kind=MovementKind.INVENTORY,
                        set_quantity=quantity,
                        channel=channel,
                        user_id=user_id,
                        confidence=confidence,
                    )
                    for product_id, zone_id, quantity, confidence in counts
                ]
            )
        return [
            Movement(entry, products[entry.product_id], zones[entry.zone_id])
            for entry in entries
        ]

    async def async_record_purchases(
        self,
        purchases: list[tuple[int, float]],
        *,
        channel: Channel,
        user_id: str | None = None,
    ) -> list[Movement]:
        """Record purchases (product id, quantity) as one operation.

        Each goes to the product's default zone (or its only zone); the
        best-before date follows the product's shelf life when known.
        """
        for _, quantity in purchases:
            _check_quantity(quantity)
        async with self._writing():
            changes: list[StockChange] = []
            places: dict[int, tuple[Product, Zone]] = {}
            for product_id, quantity in purchases:
                product = await self.async_find_product(product_id)
                zone = await self._movement_zone(product, StockOperation.PURCHASE, None)
                places[product.id] = (product, zone)
                changes.append(
                    StockChange(
                        product_id=product.id,
                        zone_id=zone.id,
                        kind=MovementKind.PURCHASE,
                        delta=quantity,
                        best_before=shelf_life_date(product),
                        channel=channel,
                        user_id=user_id,
                    )
                )
            entries = await self.backend.async_apply_changes(changes)
        return [Movement(entry, *places[entry.product_id]) for entry in entries]

    async def async_add_alias(self, product_id: int, alias: str) -> bool:
        """Learn another name for a product; False when it is already known."""
        alias = " ".join(alias.split())
        async with self._writing():
            product = await self.async_find_product(product_id)
            products = await self.backend.async_list_products(include_archived=False)
            taken = {
                normalize(name)
                for other in products
                for name in (other.name, *other.aliases)
            }
            if not alias or normalize(alias) in taken:
                return False
            product.aliases.append(alias)
            await self.backend.async_update_product(product)
        return True

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
