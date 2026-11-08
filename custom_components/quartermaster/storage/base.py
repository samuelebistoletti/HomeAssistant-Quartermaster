"""Storage backend interface.

The inventory service talks to storage only through this interface, so other
backends (Grocy, EverShelf) can be added without touching the agent or the
entities. Every method is called from the event loop; implementations keep
blocking work in the executor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from .models import Channel, JournalEntry, Product, StockChange, StockLevel, Zone


class StorageBackend(ABC):
    """Persistent store for zones, products, stock and the journal."""

    @abstractmethod
    async def async_open(self) -> None:
        """Open the store and bring its schema up to date."""

    @abstractmethod
    async def async_close(self) -> None:
        """Release the store."""

    @abstractmethod
    async def async_list_zones(self) -> list[Zone]:
        """Return every zone."""

    @abstractmethod
    async def async_save_zone(self, zone: Zone) -> None:
        """Insert or replace a zone."""

    @abstractmethod
    async def async_delete_zone(self, zone_id: str) -> None:
        """Delete a zone with its stock rows."""

    @abstractmethod
    async def async_list_products(self, *, include_archived: bool) -> list[Product]:
        """Return the products."""

    @abstractmethod
    async def async_add_product(self, product: Product) -> Product:
        """Insert a product and return it with its new id."""

    @abstractmethod
    async def async_update_product(self, product: Product) -> None:
        """Replace a stored product."""

    @abstractmethod
    async def async_list_stock(
        self, *, product_id: int | None = None, zone_id: str | None = None
    ) -> list[StockLevel]:
        """Return stock rows, optionally filtered."""

    @abstractmethod
    async def async_apply_changes(
        self, changes: list[StockChange]
    ) -> list[JournalEntry]:
        """Apply stock changes and journal them in one transaction."""

    @abstractmethod
    async def async_list_journal(
        self, *, limit: int, product_id: int | None = None
    ) -> list[JournalEntry]:
        """Return the newest journal entries first."""

    @abstractmethod
    async def async_last_undoable(
        self,
        *,
        channel: Channel | None = None,
        user_id: str | None = None,
        since: datetime | None = None,
    ) -> JournalEntry | None:
        """Return the newest movement that is not an undo and is not undone."""

    @abstractmethod
    async def async_dump(self) -> dict[str, list[dict[str, Any]]]:
        """Return the whole inventory as JSON-serializable tables."""

    @abstractmethod
    async def async_restore(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        """Replace the whole inventory with tables from `async_dump`."""
