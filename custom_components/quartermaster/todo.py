"""Shopping list todo entity."""

from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.const import (
    CONF_RECORD_PURCHASE_ON_CHECK,
    DEFAULT_RECORD_PURCHASE_ON_CHECK,
)
from custom_components.quartermaster.coordinator import QuartermasterCoordinator
from custom_components.quartermaster.entity import QuartermasterEntity
from custom_components.quartermaster.storage.models import ShoppingItem

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QuartermasterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the shopping list."""
    async_add_entities([QuartermasterShoppingList(entry.runtime_data.coordinator)])


def quantity_text(quantity: float | None, unit: str | None) -> str | None:
    """Return the quantity as shown under an item, such as "3 bottle"."""
    if quantity is None:
        return None
    return f"{quantity:g} {unit or ''}".strip()


class QuartermasterShoppingList(QuartermasterEntity, TodoListEntity):
    """The shopping list: automatic, suggested and manual items."""

    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: QuartermasterCoordinator) -> None:
        """Set up the list."""
        super().__init__(coordinator, "shopping_list")

    def _description(self, item: ShoppingItem) -> str | None:
        if item.note:
            return item.note
        product = self.coordinator.data.products.get(item.product_id or 0)
        return quantity_text(item.quantity, product.unit if product else None)

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the items in display order."""
        return [
            TodoItem(
                uid=str(item.id),
                summary=item.text,
                status=TodoItemStatus(item.status.value),
                description=self._description(item),
                completed=item.completed_at,
            )
            for item in self.coordinator.data.shopping_items
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add a manual item."""
        await self.coordinator.shopping.async_add(
            text=item.summary or "", note=item.description
        )
        await self.coordinator.async_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Rename, check or uncheck an item; checking may record the purchase."""
        assert item.uid is not None
        current = next(
            entry
            for entry in self.coordinator.data.shopping_items
            if str(entry.id) == item.uid
        )
        description = item.description
        if description == self._description(current) and not current.note:
            description = None  # the generated quantity text, not a note
        record = self.coordinator.data.shopping_mode or bool(
            self.coordinator.config_entry.options.get(
                CONF_RECORD_PURCHASE_ON_CHECK, DEFAULT_RECORD_PURCHASE_ON_CHECK
            )
        )
        await self.coordinator.shopping.async_update(
            current.id,
            text=item.summary or current.text,
            completed=item.status == TodoItemStatus.COMPLETED,
            note=description,
            record_purchase=record,
        )
        await self.coordinator.async_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete items."""
        await self.coordinator.shopping.async_delete([int(uid) for uid in uids])
        await self.coordinator.async_refresh()

    async def async_move_todo_item(
        self, uid: str, previous_uid: str | None = None
    ) -> None:
        """Reorder an item."""
        await self.coordinator.shopping.async_move(
            int(uid), int(previous_uid) if previous_uid is not None else None
        )
        await self.coordinator.async_refresh()
