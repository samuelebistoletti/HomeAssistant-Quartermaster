"""Shopping list: automatic, suggested and manual items, external copy, import.

- Automatic items follow the essentials to buy: one item per product, added
  when it goes below its minimum, its quantity kept current, removed when it
  is back above. If deleted by hand, it comes back at the next recalculation
  while the product is still below its minimum (author decision). If checked
  off, it stays away until the stock of the product changes or two days pass.
- Manual and suggested items are never removed automatically.
- Checking an item off can record the purchase of its product.
- The optional copy to another todo entity is one-way and tracked by uid
  through the documented todo actions; items added there by hand are never
  touched.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.quartermaster.core.inventory import (
    InventoryError,
    InventoryService,
    StockOperation,
    normalize,
)
from custom_components.quartermaster.core.suggestions import ToBuy
from custom_components.quartermaster.storage.base import StorageBackend
from custom_components.quartermaster.storage.models import (
    Channel,
    Product,
    ShoppingItem,
    ShoppingOrigin,
    ShoppingStatus,
)

_LOGGER = logging.getLogger(__name__)

TODO_DOMAIN = "todo"
# A checked item keeps its product off the list this long while the purchase
# is not in stock yet; completed items are deleted after COMPLETED_KEEP.
CHECKED_GRACE = timedelta(days=2)
COMPLETED_KEEP = timedelta(days=30)


class ImportTarget(StrEnum):
    """What the items of an imported todo list become."""

    PRODUCTS = "products"
    ESSENTIALS = "essentials"
    SHOPPING = "shopping"


class ImportAction(StrEnum):
    """What an import does with one todo item."""

    CREATE_PRODUCT = "create_product"
    SET_ESSENTIAL = "set_essential"
    ADD_TO_LIST = "add_to_list"
    SKIP = "skip"


@dataclass(slots=True)
class ImportStep:
    """One line of an import preview."""

    summary: str
    action: ImportAction
    product: str | None = None


class ShoppingService:
    """Operations on the shopping list."""

    def __init__(
        self, hass: HomeAssistant, backend: StorageBackend, inventory: InventoryService
    ) -> None:
        """Wrap the backend and the inventory."""
        self._hass = hass
        self._backend = backend
        self._inventory = inventory
        # One copy at a time, or an item could be added twice.
        self._external_lock = asyncio.Lock()

    async def async_items(self) -> list[ShoppingItem]:
        """Return the list in display order."""
        return await self._backend.async_list_shopping_items()

    async def _async_item(self, item_id: int) -> ShoppingItem:
        for item in await self._backend.async_list_shopping_items():
            if item.id == item_id:
                return item
        raise InventoryError("item_not_found", item=str(item_id))

    async def _async_match_product(self, text: str) -> Product | None:
        key = normalize(text)
        products = await self._inventory.async_list_products()
        for product in products:
            if normalize(product.name) == key:
                return product
        matches = [
            p for p in products if key in {normalize(alias) for alias in p.aliases}
        ]
        return matches[0] if len(matches) == 1 else None

    # Automatic items

    async def async_sync_auto(self, to_buy: list[ToBuy]) -> bool:
        """Bring the automatic items in line with the essentials to buy."""
        items = await self._backend.async_list_shopping_items()
        open_items = {
            item.product_id: item
            for item in items
            if item.status is ShoppingStatus.NEEDS_ACTION
            and item.product_id is not None
        }
        wanted = {entry.product.id: entry for entry in to_buy}
        changed = False
        now = dt_util.utcnow()
        expired = [
            item.id
            for item in items
            if item.status is ShoppingStatus.COMPLETED
            and item.completed_at is not None
            and now - item.completed_at > COMPLETED_KEEP
        ]
        if expired:
            await self._backend.async_delete_shopping_items(expired)
            changed = True
        checked = await self._async_just_checked(items, set(wanted) - set(open_items))
        updates: list[ShoppingItem] = []
        for entry in to_buy:
            item = open_items.get(entry.product.id)
            if item is None:
                if entry.product.id in checked:
                    continue
                await self._backend.async_add_shopping_item(
                    ShoppingItem(
                        id=0,
                        text=entry.product.name,
                        status=ShoppingStatus.NEEDS_ACTION,
                        origin=ShoppingOrigin.AUTO,
                        product_id=entry.product.id,
                        quantity=entry.quantity,
                    )
                )
                changed = True
            elif item.origin is ShoppingOrigin.AUTO and item.quantity != entry.quantity:
                item.quantity = entry.quantity
                updates.append(item)
        if updates:
            await self._backend.async_update_shopping_items(updates)
            changed = True
        stale = [
            item
            for item in open_items.values()
            if item.origin is ShoppingOrigin.AUTO and item.product_id not in wanted
        ]
        if stale:
            await self.async_delete([item.id for item in stale])
            changed = True
        return changed

    async def _async_just_checked(
        self, items: list[ShoppingItem], product_ids: set[int]
    ) -> set[int]:
        """Return the products checked off and not put in stock yet.

        A checked item means "bought": the product is not listed again until
        its stock changes (it then counts for what it is) or the grace time
        is over without the purchase ever being recorded.
        """
        now = dt_util.utcnow()
        checked_at: dict[int, datetime] = {}
        for item in items:
            if (
                item.status is ShoppingStatus.COMPLETED
                and item.product_id in product_ids
                and item.completed_at is not None
                and now - item.completed_at <= CHECKED_GRACE
            ):
                previous = checked_at.get(item.product_id)
                if previous is None or item.completed_at > previous:
                    checked_at[item.product_id] = item.completed_at
        result: set[int] = set()
        for product_id, when in checked_at.items():
            levels = await self._inventory.async_get_stock(product_id=product_id)
            if all(
                level.updated_at is None or level.updated_at <= when for level in levels
            ):
                result.add(product_id)
        return result

    # Adding, editing and removing

    async def async_add(
        self,
        *,
        text: str | None = None,
        product_ref: str | int | None = None,
        quantity: float | None = None,
        origin: ShoppingOrigin = ShoppingOrigin.MANUAL,
        note: str | None = None,
    ) -> ShoppingItem:
        """Add an item; a product already on the list is not added twice."""
        product: Product | None
        if product_ref is not None:
            product = await self._inventory.async_find_product(product_ref)
        else:
            text = " ".join((text or "").split())
            if not text:
                raise InventoryError("invalid_name")
            product = await self._async_match_product(text)
        if (
            product is not None
            and quantity is None
            and origin is ShoppingOrigin.SUGGESTION
            and product.essential
            and product.target_stock is not None
        ):
            # An accepted "below target" suggestion buys up to the target.
            levels = await self._inventory.async_get_stock(product_id=product.id)
            have = sum(level.quantity for level in levels)
            if have < product.target_stock:
                quantity = round(product.target_stock - have, 3)
        if product is not None:
            for item in await self._backend.async_list_shopping_items():
                if (
                    item.product_id == product.id
                    and item.status is ShoppingStatus.NEEDS_ACTION
                ):
                    if quantity is not None and item.quantity != quantity:
                        item.quantity = quantity
                        await self._backend.async_update_shopping_items([item])
                    return item
        return await self._backend.async_add_shopping_item(
            ShoppingItem(
                id=0,
                text=product.name if product and not text else str(text),
                status=ShoppingStatus.NEEDS_ACTION,
                origin=origin,
                product_id=product.id if product else None,
                quantity=quantity,
                note=note,
            )
        )

    async def async_update(
        self,
        item_id: int,
        *,
        text: str,
        completed: bool,
        note: str | None,
        record_purchase: bool,
    ) -> ShoppingItem:
        """Apply an edit from the todo entity; may record a purchase."""
        item = await self._async_item(item_id)
        text = " ".join(text.split()) or item.text
        if normalize(text) != normalize(item.text):
            product = await self._async_match_product(text)
            item.product_id = product.id if product else None
            if item.origin is ShoppingOrigin.AUTO:
                item.origin = ShoppingOrigin.MANUAL
        item.text = text
        item.note = note
        newly_completed = completed and item.status is ShoppingStatus.NEEDS_ACTION
        if completed:
            item.status = ShoppingStatus.COMPLETED
            item.completed_at = item.completed_at or dt_util.utcnow()
        else:
            item.status = ShoppingStatus.NEEDS_ACTION
            item.completed_at = None
        await self._backend.async_update_shopping_items([item])
        if newly_completed and record_purchase and item.product_id is not None:
            try:
                await self._inventory.async_adjust_stock(
                    item.product_id,
                    StockOperation.PURCHASE,
                    quantity=item.quantity or 1.0,
                    channel=Channel.DASHBOARD,
                )
            except InventoryError as err:
                # The item stays checked; the purchase needs a zone first.
                _LOGGER.info("Purchase of %s not recorded: %s", item.text, err)
        return item

    async def async_delete(self, item_ids: list[int]) -> None:
        """Delete items, and their copies in the external list."""
        items = {
            item.id: item for item in await self._backend.async_list_shopping_items()
        }
        external = [
            uid
            for item_id in item_ids
            if item_id in items and (uid := items[item_id].external_uid)
        ]
        await self._backend.async_delete_shopping_items(item_ids)
        if external and (entity_id := self.external_entity_id):
            await self._async_todo_call("remove_item", entity_id, {"item": external})

    async def async_move(self, item_id: int, previous_id: int | None) -> None:
        """Place an item right after another one, or first."""
        order = [item.id for item in await self._backend.async_list_shopping_items()]
        if item_id not in order:
            raise InventoryError("item_not_found", item=str(item_id))
        order.remove(item_id)
        if previous_id is not None and previous_id not in order:
            raise InventoryError("item_not_found", item=str(previous_id))
        index = 0 if previous_id is None else order.index(previous_id) + 1
        order.insert(index, item_id)
        await self._backend.async_reorder_shopping_items(order)

    # External copy

    external_entity_id: str | None = None

    async def _async_todo_call(
        self, action: str, entity_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            response = await self._hass.services.async_call(
                TODO_DOMAIN,
                action,
                data,
                target={ATTR_ENTITY_ID: entity_id},
                blocking=True,
                return_response=action == "get_items",
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Copy to %s failed (%s): %s", entity_id, action, err)
            return None
        return response

    async def _async_external_items(
        self, entity_id: str
    ) -> list[dict[str, Any]] | None:
        response = await self._async_todo_call(
            "get_items",
            entity_id,
            {"status": [ShoppingStatus.NEEDS_ACTION, ShoppingStatus.COMPLETED]},
        )
        if response is None:
            return None
        items: list[dict[str, Any]] = response.get(entity_id, {}).get("items", [])
        return items

    async def async_sync_external(self) -> None:
        """Copy open items to the external list and mirror completions."""
        entity_id = self.external_entity_id
        if not entity_id:
            return
        async with self._external_lock:
            await self._async_sync_external(entity_id)

    async def _async_sync_external(self, entity_id: str) -> None:
        external = await self._async_external_items(entity_id)
        if external is None:
            return
        status_by_uid = {item["uid"]: item.get("status") for item in external}
        known_uids = set(status_by_uid)
        updates: list[ShoppingItem] = []
        for item in await self._backend.async_list_shopping_items():
            if item.external_uid:
                if (
                    item.status is ShoppingStatus.COMPLETED
                    and status_by_uid.get(item.external_uid)
                    == ShoppingStatus.NEEDS_ACTION
                ):
                    await self._async_todo_call(
                        "update_item",
                        entity_id,
                        {"item": item.external_uid, "status": ShoppingStatus.COMPLETED},
                    )
                continue
            if item.status is not ShoppingStatus.NEEDS_ACTION:
                continue
            await self._async_todo_call("add_item", entity_id, {"item": item.text})
            after = await self._async_external_items(entity_id)
            if after is None:
                return
            new = [
                entry["uid"]
                for entry in after
                if entry["uid"] not in known_uids and entry.get("summary") == item.text
            ]
            if new:
                item.external_uid = new[0]
                known_uids.add(new[0])
                updates.append(item)
        if updates:
            await self._backend.async_update_shopping_items(updates)

    # Import from another todo entity

    async def async_import_from_todo(
        self,
        entity_id: str,
        target: ImportTarget,
        *,
        min_stock: float,
        apply: bool,
    ) -> list[ImportStep]:
        """Preview, and with `apply` carry out, an import from a todo list."""
        external = await self._async_external_items(entity_id)
        if external is None:
            raise InventoryError("todo_unreadable", entity=entity_id)
        steps: list[ImportStep] = []
        seen: set[str] = set()
        for entry in external:
            summary = " ".join(str(entry.get("summary") or "").split())
            if not summary or normalize(summary) in seen:
                continue
            seen.add(normalize(summary))
            product = await self._async_match_product(summary)
            step = self._import_step(summary, product, target)
            steps.append(step)
            if apply:
                await self._async_apply_step(step, product, target, min_stock)
        return steps

    @staticmethod
    def _import_step(
        summary: str, product: Product | None, target: ImportTarget
    ) -> ImportStep:
        name = product.name if product else None
        if target is ImportTarget.SHOPPING:
            return ImportStep(summary, ImportAction.ADD_TO_LIST, name)
        if product is None:
            return ImportStep(summary, ImportAction.CREATE_PRODUCT, summary)
        if target is ImportTarget.ESSENTIALS and not product.essential:
            return ImportStep(summary, ImportAction.SET_ESSENTIAL, name)
        return ImportStep(summary, ImportAction.SKIP, name)

    async def _async_apply_step(
        self,
        step: ImportStep,
        product: Product | None,
        target: ImportTarget,
        min_stock: float,
    ) -> None:
        if step.action is ImportAction.ADD_TO_LIST:
            await self.async_add(
                text=step.summary, product_ref=product.id if product else None
            )
        elif step.action is ImportAction.CREATE_PRODUCT:
            essential = target is ImportTarget.ESSENTIALS
            await self._inventory.async_add_product(
                step.summary,
                essential=essential,
                min_stock=min_stock if essential else None,
            )
        elif step.action is ImportAction.SET_ESSENTIAL and product is not None:
            await self._inventory.async_set_essential(
                product.id, essential=True, min_stock=product.min_stock or min_stock
            )
