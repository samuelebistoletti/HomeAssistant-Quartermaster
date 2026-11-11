"""Notification routing (spec §6.9): notify entities per message type.

For each type the options list notify entities in order; the message goes to
the first one that is available (fallback). During quiet hours messages wait
and go out when quiet hours end. Buttons only exist on Telegram (author
decision): with `telegram_notify` on, messages also go to the allowed
private Telegram chats, with buttons where they help. Other notify entities
get plain text through the documented `notify.send_message` action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from custom_components.quartermaster.channels.telegram import (
    async_proposal_buttons,
    async_proposal_lines,
)
from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.phrases import async_phrase
from custom_components.quartermaster.core.purchases import EVENT_PURCHASES_PROPOSED
from custom_components.quartermaster.core.suggestions import Suggestion
from custom_components.quartermaster.core.voice import EVENT_ASK_RESULT
from custom_components.quartermaster.core.zones import (
    EVENT_LEDGER_CHECK,
    EVENT_PHOTO_REQUEST,
)
from custom_components.quartermaster.storage.models import Channel, MovementKind
from custom_components.quartermaster.vision.show import (
    EVENT_SHOW_RECOGNIZED,
    EVENT_SHOW_REQUEST,
)

if TYPE_CHECKING:
    from custom_components.quartermaster import QuartermasterRuntimeData

_LOGGER = logging.getLogger(__name__)

CONF_QUIET_HOURS = "quiet_hours"
CONF_TELEGRAM_NOTIFY = "telegram_notify"
EXPIRING_HOUR = 9
WEEKLY_WEEKDAY = 0  # Monday


class MessageType(StrEnum):
    """Kinds of notifications, each with its own notify entities."""

    PHOTO_REQUEST = "photo_request"
    CHECK_RESULT = "check_result"
    EXPIRING = "expiring"
    CAMERA = "camera"
    ESSENTIAL_FINISHED = "essential_finished"
    SHOPPING = "shopping"
    WEEKLY = "weekly"
    VOICE_REPLY = "voice_reply"
    PURCHASES = "purchases"


def option_key(kind: MessageType) -> str:
    """Return the option holding the notify entities of a type."""
    return f"notify_{kind.value}"


def quiet_window(spec: str | None) -> tuple[time, time] | None:
    """Return the start and end of an "HH:MM-HH:MM" window, or None."""
    if not spec:
        return None
    try:
        start_text, end_text = spec.split("-")
    except ValueError:
        return None
    start = dt_util.parse_time(start_text.strip())
    end = dt_util.parse_time(end_text.strip())
    if start is None or end is None:
        return None
    return start, end


def in_quiet_hours(spec: str | None, now: datetime) -> bool:
    """Return whether a local time falls in an "HH:MM-HH:MM" window."""
    if (window := quiet_window(spec)) is None:
        return False
    start, end = window
    current = now.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


@dataclass(slots=True)
class Message:
    """One notification, with optional Telegram buttons."""

    kind: MessageType
    text: str
    title: str | None = None
    buttons: list[list[tuple[str, str]]] | None = None
    # Notify entities that get the message too (a zone's own announcements).
    also: list[str] = field(default_factory=list)
    # Telegram chats that get it even without telegram_notify (the chat a
    # request came from).
    chats: list[int] = field(default_factory=list)
    # An answer to something the user just asked: not held in quiet hours.
    direct: bool = False


class NotificationRouter:
    """Turns integration events into notifications."""

    def __init__(self, hass: HomeAssistant, runtime: QuartermasterRuntimeData) -> None:
        """Keep references; nothing listens until `async_start`."""
        self._hass = hass
        self._runtime = runtime
        self._unsubs: list[CALLBACK_TYPE] = []
        self.queue: list[Message] = []
        self._known_to_buy: set[int] | None = None

    @property
    def _options(self) -> dict[str, Any]:
        return dict(self._runtime.coordinator.config_entry.options)

    @callback
    def async_start(self) -> None:
        """Listen to the integration's events and timers."""
        bus = self._hass.bus
        self._unsubs += [
            bus.async_listen(EVENT_PHOTO_REQUEST, self._on_photo_request),
            bus.async_listen(EVENT_SHOW_RECOGNIZED, self._on_show),
            bus.async_listen(EVENT_SHOW_REQUEST, self._on_show_request),
            bus.async_listen(EVENT_LEDGER_CHECK, self._on_ledger_check),
            bus.async_listen(EVENT_ASK_RESULT, self._on_ask_result),
            bus.async_listen(f"{DOMAIN}_zone_checked", self._on_zone_checked),
            bus.async_listen(EVENT_PURCHASES_PROPOSED, self._on_purchases),
            async_track_time_change(self._hass, self._on_minute, second=30),
            self._runtime.coordinator.async_add_listener(self._on_refresh),
        ]

    @callback
    def async_stop(self) -> None:
        """Stop listening."""
        while self._unsubs:
            self._unsubs.pop()()

    def _task(self, job: Any, name: str) -> None:
        entry = self._runtime.coordinator.config_entry
        entry.async_create_background_task(self._hass, job, f"{DOMAIN} notify {name}")

    async def _phrase(self, key: str, **placeholders: Any) -> str:
        return await async_phrase(
            self._hass, None, key, **{k: str(v) for k, v in placeholders.items()}
        )

    # Sending

    def can_deliver(self, kind: MessageType) -> bool:
        """Whether a message of this kind has somewhere to go."""
        if self._options.get(option_key(kind)):
            return True
        telegram = self._runtime.telegram
        return bool(
            telegram is not None
            and self._options.get(CONF_TELEGRAM_NOTIFY)
            and any(c > 0 for c in telegram.chats)
        )

    async def async_send(self, message: Message) -> None:
        """Send now, or queue it during quiet hours."""
        if not message.direct and in_quiet_hours(
            self._options.get(CONF_QUIET_HOURS), dt_util.now()
        ):
            self.queue.append(message)
            return
        await self._async_deliver(message)

    async def _async_deliver(self, message: Message) -> None:
        for entity_id in self._options.get(option_key(message.kind), []) or []:
            state = self._hass.states.get(entity_id)
            if state is None or state.state == STATE_UNAVAILABLE:
                continue
            data: dict[str, Any] = {"message": message.text}
            if message.title:
                data["title"] = message.title
            try:
                await self._hass.services.async_call(
                    "notify",
                    "send_message",
                    data,
                    target={ATTR_ENTITY_ID: entity_id},
                    blocking=True,
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Notification to %s failed: %s", entity_id, err)
                continue
            break
        for entity_id in message.also:
            state = self._hass.states.get(entity_id)
            if state is None or state.state == STATE_UNAVAILABLE:
                continue
            try:
                await self._hass.services.async_call(
                    "notify",
                    "send_message",
                    {"message": message.text},
                    target={ATTR_ENTITY_ID: entity_id},
                    blocking=True,
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Notification to %s failed: %s", entity_id, err)
        telegram = self._runtime.telegram
        if telegram is None:
            return
        chats = set(message.chats)
        if self._options.get(CONF_TELEGRAM_NOTIFY):
            chats |= {c for c in telegram.chats if c > 0}
        for chat_id in sorted(chats):
            await telegram.async_send(chat_id, message.text, message.buttons)

    async def async_flush(self) -> None:
        """Send the messages held during quiet hours."""
        held, self.queue = self.queue, []
        for message in held:
            await self._async_deliver(message)

    # Event handlers

    @callback
    def _on_photo_request(self, event: Event[dict[str, Any]]) -> None:
        self._task(self._async_photo_request(event.data), "photo")

    async def _async_photo_request(self, data: dict[str, Any]) -> None:
        reason = await self._phrase(f"reason_{data['reason']}")
        key = (
            "notify_photo_reminder" if data.get("reminder") else "notify_photo_request"
        )
        text = await self._phrase(key, zone=data["zone"], reason=reason)
        if products := data.get("products"):
            text += " " + await self._phrase(
                "notify_photo_frame", products=", ".join(products)
            )
        chats = [int(data["chat_id"])] if data.get("chat_id") is not None else []
        await self.async_send(
            Message(MessageType.PHOTO_REQUEST, text, chats=chats, direct=bool(chats))
        )

    @callback
    def _on_ledger_check(self, event: Event[dict[str, Any]]) -> None:
        self._task(self._async_ledger_check(event.data), "ledger")

    async def _async_ledger_check(self, data: dict[str, Any]) -> None:
        """Send the list of a zone kept by word, with a row of buttons each."""
        zone_id, zone = str(data["zone_id"]), str(data["zone"])
        chats = [int(data["chat_id"])] if data.get("chat_id") is not None else []
        items = list(data.get("items") or [])
        if not items:
            text = await self._phrase("notify_ledger_empty", zone=zone)
            await self.async_send(
                Message(
                    MessageType.PHOTO_REQUEST, text, chats=chats, direct=bool(chats)
                )
            )
            return
        lines = [await self._phrase("notify_ledger_check", zone=zone)]
        buttons: list[list[tuple[str, str]]] = []
        for item in items:
            line = f"• {item['product']}: {float(item['quantity']):g}"
            if item.get("unit"):
                line += f" {item['unit']}"
            if since := dt_util.parse_date(str(item.get("since") or "")):
                line += " " + await self._phrase(
                    "notify_ledger_since",
                    date=f"{since.day}/{since.month}/{since.year}",
                )
            lines.append(line)
            less = f"/qm_lg {zone_id} {item['product_id']} m"
            # Telegram refuses callback data over 64 bytes.
            if len(less.encode()) <= 64:
                buttons.append(
                    [
                        (
                            await self._phrase(
                                "button_ledger_less", product=item["product"]
                            ),
                            less,
                        ),
                        (
                            await self._phrase(
                                "button_ledger_finished", product=item["product"]
                            ),
                            f"/qm_lg {zone_id} {item['product_id']} f",
                        ),
                    ]
                )
        if more := int(data.get("more") or 0):
            lines.append(await self._phrase("notify_ledger_more", count=more))
        lines.append(await self._phrase("notify_ledger_hint"))
        done = f"/qm_lg_ok {zone_id}"
        if len(done.encode()) <= 64:
            buttons.append([(await self._phrase("button_ledger_ok"), done)])
        await self.async_send(
            Message(
                MessageType.PHOTO_REQUEST,
                "\n".join(lines),
                buttons=buttons,
                chats=chats,
                direct=bool(chats),
            )
        )

    @callback
    def _on_show_request(self, event: Event[dict[str, Any]]) -> None:
        self._task(self._async_show_request(event.data), "camera request")

    async def _async_show_request(self, data: dict[str, Any]) -> None:
        """Ask the user to confirm what a camera saw; nothing is written yet."""
        quantity = float(data.get("quantity") or 1)
        current = float(data.get("current") or 0)
        values = {
            "product": data.get("product"),
            "quantity": f"{quantity:g}",
            "zone": data.get("zone"),
        }
        proposal_id = data.get("proposal_id")
        if proposal_id is None:
            text = await self._phrase("notify_show_no_receipt", **values)
            await self.async_send(Message(MessageType.CAMERA, text))
            return
        no = (await self._phrase("button_no"), f"/qm_no {proposal_id}")
        if data.get("action") != "taken":
            text = await self._phrase("notify_show_ask_returned", **values)
            yes = (
                await self._phrase("button_yes", quantity=values["quantity"]),
                f"/qm_ok {proposal_id}",
            )
            await self.async_send(
                Message(MessageType.CAMERA, text, buttons=[[yes, no]])
            )
            return
        text = await self._phrase(
            "notify_show_ask_taken",
            **values,
            current=f"{current:g}",
            left=f"{max(current - quantity, 0):g}",
        )
        # The quantity seen, one more, and everything: the usual corrections.
        row = [
            (
                await self._phrase("button_yes", quantity=values["quantity"]),
                f"/qm_ok {proposal_id}",
            )
        ]
        if quantity + 1 < current:
            row.append((f"{quantity + 1:g}", f"/qm_qty {proposal_id} {quantity + 1:g}"))
        if current > quantity:
            row.append(
                (
                    await self._phrase("button_all", quantity=f"{current:g}"),
                    f"/qm_qty {proposal_id} {current:g}",
                )
            )
        await self.async_send(Message(MessageType.CAMERA, text, buttons=[[*row, no]]))

    @callback
    def _on_show(self, event: Event[dict[str, Any]]) -> None:
        self._task(self._async_show(event.data), "camera")

    async def _async_show(self, data: dict[str, Any]) -> None:
        key = (
            "notify_show_taken"
            if data.get("action") == "taken"
            else "notify_show_returned"
        )
        text = await self._phrase(
            key,
            product=data.get("product"),
            quantity=f"{data.get('quantity', 1):g}",
            zone=data.get("zone"),
        )
        if data.get("to_list"):
            text += " " + await self._phrase("notify_show_to_list")
        # The button undoes this movement, not whatever the camera did last.
        undo = "/qm_undo_camera"
        if data.get("operation_id"):
            undo += f" {data['operation_id']}"
        await self.async_send(
            Message(
                MessageType.CAMERA,
                text,
                buttons=[[(await self._phrase("button_undo"), undo)]],
                also=list(data.get("announce") or []),
            )
        )

    @callback
    def _on_ask_result(self, event: Event[dict[str, Any]]) -> None:
        if event.data.get("channel") == Channel.TELEGRAM:
            return  # the Telegram channel replies in the chat itself
        self._task(
            self.async_send(
                Message(
                    MessageType.VOICE_REPLY,
                    str(event.data.get("speech") or ""),
                    direct=True,
                )
            ),
            "voice",
        )

    @callback
    def _on_zone_checked(self, event: Event[dict[str, Any]]) -> None:
        data = event.data
        if data.get("status") == "failed" and data.get("reason") != "already_running":
            self._task(self._async_check_failed(data), "check")
        if data.get("status") != "updated" or data.get("unchanged"):
            return
        self._task(self._async_zone_checked(data), "check")

    def _zone_name(self, zone_id: str) -> str:
        zones = self._runtime.coordinator.data.zones
        return zones[zone_id].name if zone_id in zones else zone_id

    async def _async_check_failed(self, data: dict[str, Any]) -> None:
        reason = await self._phrase(f"reason_{data.get('reason') or 'analysis_failed'}")
        text = await self._phrase(
            "notify_check_failed", zone=self._zone_name(data["zone_id"]), reason=reason
        )
        await self.async_send(Message(MessageType.CHECK_RESULT, text))

    @callback
    def _on_purchases(self, event: Event[dict[str, Any]]) -> None:
        # Receipts sent from a chat or the dashboard are answered there.
        if event.data.get("source") != "email":
            return
        self._task(self._async_purchases(event.data), "purchases")

    async def _async_purchases(self, data: dict[str, Any]) -> None:
        proposal_id = data["proposal_id"]
        text = await self._phrase("notify_purchases", count=data.get("count", 0))
        buttons = [
            [
                (await self._phrase("button_confirm"), f"/qm_ok {proposal_id}"),
                (await self._phrase("button_reject"), f"/qm_no {proposal_id}"),
            ]
        ]
        await self.async_send(Message(MessageType.PURCHASES, text, buttons=buttons))

    async def _async_zone_checked(self, data: dict[str, Any]) -> None:
        text = await self._phrase(
            "notify_check_result",
            zone=self._zone_name(data["zone_id"]),
            applied=data.get("applied") or 0,
            proposed="1" if data.get("proposal_id") else "0",
        )
        if more := data.get("more_than_stock"):
            text += " " + await self._phrase(
                "notify_check_more", products=", ".join(more)
            )
        buttons = None
        if proposal_id := data.get("proposal_id"):
            rows: list[dict[str, Any]] = next(
                (
                    p.payload["items"]
                    for p in await self._runtime.proposals.async_pending()
                    if p.id == proposal_id
                ),
                [],
            )
            if rows:
                text += "\n" + "\n".join(await async_proposal_lines(self._hass, rows))
            buttons = await async_proposal_buttons(self._hass, proposal_id, rows)
        await self.async_send(Message(MessageType.CHECK_RESULT, text, buttons=buttons))

    @callback
    def _on_refresh(self) -> None:
        """Announce essentials that just went below their minimum."""
        to_buy = {
            entry.product.id: entry
            for entry in self._runtime.coordinator.data.analysis.to_buy
        }
        if self._known_to_buy is not None:
            for product_id in to_buy.keys() - self._known_to_buy:
                self._task(
                    self._async_finished(to_buy[product_id].product.name), "essential"
                )
        self._known_to_buy = set(to_buy)

    async def _async_finished(self, product: str) -> None:
        await self.async_send(
            Message(
                MessageType.ESSENTIAL_FINISHED,
                await self._phrase("notify_essential", product=product),
            )
        )

    # Scheduled messages

    @callback
    def _on_minute(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        if self.queue and not in_quiet_hours(
            self._options.get(CONF_QUIET_HOURS), local
        ):
            self._task(self.async_flush(), "flush")
        if local.minute == 0 and local.hour == EXPIRING_HOUR:
            self._task(self.async_send_expiring(), "expiring")
            if local.weekday() == WEEKLY_WEEKDAY:
                self._task(self.async_send_weekly(), "weekly")

    async def async_send_expiring(self) -> None:
        """Send the products expiring soon, if any."""
        expiring = self._runtime.coordinator.data.analysis.expiring
        if not expiring:
            return
        lines = [
            await self._phrase("notify_expiring"),
            *[
                f"• {entry.product.name} ({entry.zone.name}): "
                + await self._date(entry.best_before)
                for entry in expiring
            ],
        ]
        await self.async_send(Message(MessageType.EXPIRING, "\n".join(lines)))

    async def async_send_shopping(self) -> None:
        """Send the shopping list, then suggestions with their reasons."""
        data = self._runtime.coordinator.data
        items = [
            f"• {item.text}" + (f" ({item.quantity:g})" if item.quantity else "")
            for item in data.shopping_items
            if item.status.value == "needs_action"
        ]
        head = await self._phrase("telegram_list" if items else "telegram_list_empty")
        await self.async_send(Message(MessageType.SHOPPING, "\n".join([head, *items])))
        if not data.analysis.suggestions:
            return
        lines = [await self._phrase("telegram_suggestions")]
        buttons = []
        for suggestion in data.analysis.suggestions[:8]:
            lines.append(
                f"• {suggestion.product.name}: {await self.reason(suggestion)}"
            )
            buttons.append(
                [
                    (
                        f"+ {suggestion.product.name}"[:40],
                        f"/qm_add {suggestion.product.id}",
                    )
                ]
            )
        await self.async_send(
            Message(MessageType.SHOPPING, "\n".join(lines), buttons=buttons)
        )

    async def async_send_weekly(self) -> None:
        """Send a short weekly summary."""
        since = dt_util.utcnow() - timedelta(days=7)
        journal = await self._runtime.inventory.backend.async_list_journal(
            limit=5000, since=since
        )
        consumed = sum(1 for e in journal if e.kind is MovementKind.CONSUME)
        bought = sum(1 for e in journal if e.kind is MovementKind.PURCHASE)
        data = self._runtime.coordinator.data
        text = await self._phrase(
            "notify_weekly",
            consumed=consumed,
            bought=bought,
            to_buy=len(data.analysis.to_buy),
            suggestions=len(data.analysis.suggestions),
        )
        await self.async_send(Message(MessageType.WEEKLY, text))

    async def reason(self, suggestion: Suggestion) -> str:
        """Return the readable reason of a suggestion."""
        details = dict(suggestion.details)
        if "date" in details:
            details["date"] = await self._date(date.fromisoformat(str(details["date"])))
        for key in ("have", "target"):
            if isinstance(details.get(key), float):
                details[key] = f"{details[key]:g}"
        return await self._phrase(f"reason_{suggestion.reason.value}", **details)

    async def _date(self, value: date) -> str:
        return await self._phrase("date_format", day=value.day, month=value.month)
