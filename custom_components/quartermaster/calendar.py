"""Calendar of best-before dates."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.coordinator import QuartermasterCoordinator
from custom_components.quartermaster.entity import QuartermasterEntity
from custom_components.quartermaster.todo import quantity_text

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QuartermasterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the calendar."""
    async_add_entities([ExpiryCalendar(entry.runtime_data.coordinator)])


class ExpiryCalendar(QuartermasterEntity, CalendarEntity):
    """One all-day event per product and zone on its best-before date."""

    def __init__(self, coordinator: QuartermasterCoordinator) -> None:
        """Set up the calendar."""
        super().__init__(coordinator, "expiry")

    def _events(self) -> list[CalendarEvent]:
        data = self.coordinator.data
        events = []
        for level in data.stock:
            product = data.products.get(level.product_id)
            if level.best_before is None or level.quantity <= 0 or product is None:
                continue
            zone = data.zones[level.zone_id]
            events.append(
                CalendarEvent(
                    start=level.best_before,
                    end=level.best_before + timedelta(days=1),
                    summary=product.name,
                    description=quantity_text(level.quantity, product.unit),
                    location=zone.name,
                    uid=f"{product.id}_{zone.id}",
                )
            )
        return sorted(events, key=lambda event: (event.start, event.summary))

    @property
    def event(self) -> CalendarEvent | None:
        """Return today's or the next expiry."""
        today = dt_util.now().date()
        for event in self._events():
            if isinstance(event.end, date) and event.end > today:
                return event
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the expiries in the range."""
        start, end = start_date.date(), end_date.date()
        return [
            event
            for event in self._events()
            if isinstance(event.end, date)
            and isinstance(event.start, date)
            and event.end > start
            and event.start <= end
        ]
