"""Coordinator: recomputes the shopping list, suggestions and expiring stock.

It refreshes after every inventory write (debounced) and every hour, so
time-based reasons (stale zones, expiry dates) stay current; the work is a
few local SQLite reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from custom_components.quartermaster.const import (
    CONF_EXPIRING_DAYS,
    CONF_SHOPPING_MODE_MINUTES,
    CONF_STALE_ZONE_DAYS,
    DEFAULT_EXPIRING_DAYS,
    DEFAULT_SHOPPING_MODE_MINUTES,
    DEFAULT_STALE_ZONE_DAYS,
    DOMAIN,
)
from custom_components.quartermaster.core.confirmations import ProposalService
from custom_components.quartermaster.core.inventory import InventoryService
from custom_components.quartermaster.core.shopping import ShoppingService
from custom_components.quartermaster.core.suggestions import (
    FINISHED_LOOKBACK,
    Analysis,
    analyze,
    zone_last_updates,
)
from custom_components.quartermaster.storage.models import (
    PhotoRequest,
    Product,
    Proposal,
    ShoppingItem,
    ShoppingStatus,
    StockLevel,
    Zone,
)

if TYPE_CHECKING:
    from custom_components.quartermaster import QuartermasterConfigEntry
    from custom_components.quartermaster.core.zones import ZoneManager
    from custom_components.quartermaster.vision.analyze import VisionService

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=1)
# Keys of the entities created once per zone (unique id: entry_key_zone).
ZONE_ENTITY_KEYS = ("last_check", "zone_error", "check_zone", "show_mode", "last_show")
REFRESH_COOLDOWN = 1.0
JOURNAL_LIMIT = 5000


@dataclass(slots=True)
class QuartermasterData:
    """Snapshot shared by the entities."""

    analysis: Analysis
    products: dict[int, Product]
    zones: dict[str, Zone]
    stock: list[StockLevel]
    shopping_items: list[ShoppingItem]
    shopping_until: datetime | None = None
    zone_updates: dict[str, datetime] = field(default_factory=dict)
    proposals: list[Proposal] = field(default_factory=list)
    analyses_today: int = 0
    zone_checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    zone_failures: dict[str, int] = field(default_factory=dict)
    photo_requests: list[PhotoRequest] = field(default_factory=list)

    @property
    def shopping_mode(self) -> bool:
        """Whether shopping mode is on."""
        return (
            self.shopping_until is not None and dt_util.utcnow() < self.shopping_until
        )


class QuartermasterCoordinator(DataUpdateCoordinator[QuartermasterData]):
    """Keeps the derived shopping data current."""

    config_entry: QuartermasterConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: QuartermasterConfigEntry,
        inventory: InventoryService,
        shopping: ShoppingService,
    ) -> None:
        """Set up the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            # Short cooldown so the list follows a burst of writes quickly.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.inventory = inventory
        self.shopping = shopping
        self.shopping_until: datetime | None = None
        self.proposals: ProposalService | None = None
        self.vision: VisionService | None = None
        self.zones: ZoneManager | None = None
        self._zone_names: dict[str, str] | None = None

    @callback
    def _async_remove_zone_entities(self, zone_id: str) -> None:
        """Remove the per-zone entities of a deleted zone from the registry."""
        registry = er.async_get(self.hass)
        suffixes = tuple(f"_{key}_{zone_id}" for key in ZONE_ENTITY_KEYS)
        for entry in er.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            if entry.unique_id.endswith(suffixes):
                registry.async_remove(entry.entity_id)

    @callback
    def async_inventory_changed(self) -> None:
        """Schedule a debounced refresh after an inventory write."""
        self.hass.async_create_task(self.async_request_refresh())

    def option(self, key: str, default: int) -> int:
        """Return an integer option."""
        return int(self.config_entry.options.get(key, default))

    async def async_start_shopping(self) -> None:
        """Turn shopping mode on for the configured time and recompute."""
        minutes = self.option(CONF_SHOPPING_MODE_MINUTES, DEFAULT_SHOPPING_MODE_MINUTES)
        self.shopping_until = dt_util.utcnow() + timedelta(minutes=minutes)
        await self.async_refresh()

    async def _async_update_data(self) -> QuartermasterData:
        """Recompute everything from the store and sync the list."""
        now = dt_util.utcnow()
        backend = self.inventory.backend
        products = await backend.async_list_products(include_archived=False)
        zones = await backend.async_list_zones()
        stock = await backend.async_list_stock()
        journal = await backend.async_list_journal(
            limit=JOURNAL_LIMIT, since=now - FINISHED_LOOKBACK
        )
        updates = zone_last_updates(stock)
        zone_checks: dict[str, dict[str, Any]] = {}
        zone_failures: dict[str, int] = {}
        for zone in zones:
            snapshot = await backend.async_get_snapshot(zone.id)
            if snapshot is not None and (
                zone.id not in updates or snapshot.ts > updates[zone.id]
            ):
                updates[zone.id] = snapshot.ts
            if self.zones is not None:
                if (last := await self.zones.async_last_check(zone.id)) is not None:
                    zone_checks[zone.id] = last
                zone_failures[zone.id] = await self.zones.async_failures(zone.id)
        names = {zone.id: zone.name for zone in zones}
        if self._zone_names is not None:
            for zone_id in self._zone_names.keys() - names.keys():
                self._async_remove_zone_entities(zone_id)
        self._zone_names = names

        def compute(items: list[ShoppingItem]) -> Analysis:
            return analyze(
                products=products,
                zones=zones,
                stock=stock,
                journal=journal,
                listed_product_ids={
                    item.product_id
                    for item in items
                    if item.product_id is not None
                    and item.status is ShoppingStatus.NEEDS_ACTION
                },
                zone_updates=updates,
                now=now,
                stale_days=self.option(CONF_STALE_ZONE_DAYS, DEFAULT_STALE_ZONE_DAYS),
                expiring_days=self.option(CONF_EXPIRING_DAYS, DEFAULT_EXPIRING_DAYS),
            )

        analysis = compute(await self.shopping.async_items())
        if await self.shopping.async_sync_auto(analysis.to_buy):
            analysis = compute(await self.shopping.async_items())
        await self.shopping.async_sync_external()
        if self.shopping_until is not None and now >= self.shopping_until:
            self.shopping_until = None
        return QuartermasterData(
            analysis=analysis,
            products={product.id: product for product in products},
            zones={zone.id: zone for zone in zones},
            stock=stock,
            shopping_items=await self.shopping.async_items(),
            shopping_until=self.shopping_until,
            zone_updates=updates,
            proposals=await self.proposals.async_pending() if self.proposals else [],
            analyses_today=await self.vision.async_analyses_today()
            if self.vision
            else 0,
            zone_checks=zone_checks,
            zone_failures=zone_failures,
            photo_requests=await self.zones.async_open_requests() if self.zones else [],
        )
