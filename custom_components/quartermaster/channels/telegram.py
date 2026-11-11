"""Telegram channel through Home Assistant's Telegram bot integration.

Listens to the bot's event entity (documented on home-assistant.io): texts go
to the agent with one session per chat, photos go to the vision pipeline,
commands give quick answers, and inline buttons confirm or reject proposals,
pick the zone of a photo and undo. Only chats in Quartermaster's own list
are served; group chats only when the option allows them (spec §8). Replies
use the documented telegram_bot actions with the bot's config entry.

Pending until the Phase 0 Telegram check: the exact event attributes on the
author's bot and the download location (`telegram_bot.download_file`
without a directory writes to <config>/telegram_bot).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.events import is_fresh_event
from custom_components.quartermaster.core.inventory import InventoryError, normalize
from custom_components.quartermaster.core.phrases import (
    async_error_text,
    async_phrase,
    configured_language,
)
from custom_components.quartermaster.core.voice import (
    EVENT_ASK_RESULT,
    TELEGRAM_SESSION_PREFIX,
)
from custom_components.quartermaster.storage.models import (
    Channel,
    ShoppingOrigin,
    ShoppingStatus,
)
from custom_components.quartermaster.vision.analyze import async_photo_pipeline

if TYPE_CHECKING:
    from custom_components.quartermaster import QuartermasterRuntimeData

_LOGGER = logging.getLogger(__name__)

TELEGRAM = "telegram_bot"
MESSAGES_PER_MINUTE = 20
PHOTO_ZONE_TTL = timedelta(minutes=10)
# Callback commands carried by inline buttons (Telegram limits data to 64 bytes).
CB_CONFIRM = "/qm_ok"
CB_REJECT = "/qm_no"
CB_ZONE = "/qm_zone"
# Zone ids are slugs, so this can never be one.
RECEIPT = "@receipt"
CB_UNDO = "/qm_undo"
CB_ADD = "/qm_add"
CB_UNDO_CAMERA = "/qm_undo_camera"


@dataclass(slots=True)
class HeldPhoto:
    """A photo waiting for the user to pick its zone."""

    data: bytes
    chat_id: int
    expires: datetime


def parse_chat_ids(values: list[Any] | str) -> set[int]:
    """Return the chat ids of the option (numbers, groups negative)."""
    if isinstance(values, str):
        values = values.replace(";", ",").split(",")
    chats: set[int] = set()
    for value in values:
        try:
            chats.add(int(str(value).strip()))
        except ValueError:
            continue
    return chats


def _label(text: str) -> str:
    """Make a button label safe for the `Label:/command` keyboard format."""
    return text.replace(",", " ").replace(":", " ").strip()[:40] or "-"


def _number(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:g}"


class TelegramChannel:
    """Serves allowed Telegram chats."""

    def __init__(
        self,
        hass: HomeAssistant,
        runtime: QuartermasterRuntimeData,
        *,
        event_entity: str,
        bot_entry_id: str | None,
        chats: set[int],
        allow_groups: bool,
    ) -> None:
        """Store the settings; nothing listens until `async_start`."""
        self._hass = hass
        self._runtime = runtime
        self.event_entity = event_entity
        self.bot_entry_id = bot_entry_id
        self.chats = chats
        self.allow_groups = allow_groups
        self._unsubs: list[CALLBACK_TYPE] = []
        self._recent: dict[int, deque[float]] = {}
        self.held: dict[str, HeldPhoto] = {}
        # State (the event time) of the last bot event handled.
        self._last_event: str | None = None
        # Last chat that wrote, so replies to its requests go back there.

    @callback
    def async_start(self) -> None:
        """Listen to the bot's events and to late agent replies."""
        self._unsubs.append(
            async_track_state_change_event(
                self._hass, self.event_entity, self._async_state_changed
            )
        )
        self._unsubs.append(
            self._hass.bus.async_listen(EVENT_ASK_RESULT, self._async_late_reply)
        )

    @callback
    def async_stop(self) -> None:
        """Stop listening."""
        while self._unsubs:
            self._unsubs.pop()()

    # Incoming

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        old = event.data["old_state"]
        if (old is not None and old.state == new.state) or (
            new.state == self._last_event
        ):
            return
        if (old is None or old.state == STATE_UNAVAILABLE) and not is_fresh_event(
            new.state
        ):
            # The entity came back with its restored state (restart, reload
            # of the bot): that message was handled before.
            return
        self._last_event = new.state
        data = dict(new.attributes)
        entry = self._runtime.coordinator.config_entry
        entry.async_create_background_task(
            self._hass, self.async_handle(data), f"{DOMAIN} telegram"
        )

    def allowed(self, chat_id: int) -> bool:
        """Return whether a chat may use Quartermaster."""
        if chat_id not in self.chats:
            return False
        return chat_id > 0 or self.allow_groups

    def _within_rate(self, chat_id: int) -> bool:
        now = time.monotonic()
        recent = self._recent.setdefault(chat_id, deque())
        while recent and now - recent[0] > 60:
            recent.popleft()
        if len(recent) >= MESSAGES_PER_MINUTE:
            return False
        recent.append(now)
        return True

    async def async_handle(self, data: dict[str, Any]) -> None:
        """Dispatch one bot event."""
        try:
            chat_id = int(data.get("chat_id"))  # type: ignore[arg-type]
        except TypeError, ValueError:
            return
        if not self.allowed(chat_id):
            _LOGGER.debug("Ignoring Telegram chat %s (not allowed)", chat_id)
            return
        if not self._within_rate(chat_id):
            return
        bot = data.get("bot")
        if (
            isinstance(bot, dict)
            and bot.get("config_entry_id")
            and not self.bot_entry_id
        ):
            self.bot_entry_id = str(bot["config_entry_id"])
        kind = data.get("event_type")
        try:
            if kind == "telegram_text":
                await self._async_text(chat_id, str(data.get("text") or ""))
            elif kind == "telegram_command":
                await self._async_command(
                    chat_id, str(data.get("command") or ""), str(data.get("args") or "")
                )
            elif kind == "telegram_attachment":
                await self._async_attachment(chat_id, data)
            elif kind == "telegram_callback":
                await self._async_callback(chat_id, data)
        except InventoryError as err:
            error = await async_error_text(self._hass, None, err)
            await self.async_send(
                chat_id, await self._phrase("telegram_error", error=error)
            )
        except ValueError:
            # A button or command with an argument that is not a number.
            _LOGGER.debug("Ignoring malformed Telegram %s", kind)

    async def _phrase(self, key: str, **placeholders: str) -> str:
        return await async_phrase(self._hass, None, key, **placeholders)

    async def _async_text(self, chat_id: int, text: str) -> None:
        text = text.strip()
        if not text:
            return
        reply = await self._runtime.voice.async_ask(
            text,
            channel=Channel.TELEGRAM,
            key=f"{TELEGRAM_SESSION_PREFIX}{chat_id}",
            language=None,
            context=Context(),
        )
        await self.async_send(chat_id, reply.speech)

    @callback
    def _async_late_reply(self, event: Event[dict[str, Any]]) -> None:
        data = event.data
        if data.get("channel") != Channel.TELEGRAM or not str(
            data.get("session_key", "")
        ).startswith(TELEGRAM_SESSION_PREFIX):
            return
        chat_id = int(str(data["session_key"]).removeprefix(TELEGRAM_SESSION_PREFIX))
        entry = self._runtime.coordinator.config_entry
        entry.async_create_background_task(
            self._hass,
            self.async_send(chat_id, str(data.get("speech") or "")),
            f"{DOMAIN} telegram reply",
        )

    async def _async_command(self, chat_id: int, command: str, args: str) -> None:
        name = command.lstrip("/").split("@")[0].lower()
        runtime = self._runtime
        if name in ("list", "lista"):
            await runtime.coordinator.async_refresh()
            items = [
                f"• {item.text}"
                + (f" ({_number(item.quantity)})" if item.quantity else "")
                for item in runtime.coordinator.data.shopping_items
                if item.status is ShoppingStatus.NEEDS_ACTION
            ]
            text = (
                await self._phrase("telegram_list")
                if items
                else await self._phrase("telegram_list_empty")
            )
            await self.async_send(chat_id, "\n".join([text, *items]))
        elif name in ("shopping", "spesa"):
            await runtime.coordinator.async_start_shopping(notify=False)
            await self.async_send_suggestions(chat_id)
        elif name in ("undo", "annulla"):
            await self._async_undo(chat_id, None)
        else:
            await self.async_send(chat_id, await self._phrase("telegram_help"))

    # Photos

    async def _async_attachment(self, chat_id: int, data: dict[str, Any]) -> None:
        mime = str(data.get("file_mime_type") or "")
        if mime and not mime.startswith("image/"):
            return
        file_id = data.get("file_id")
        if not file_id:
            return
        photo = await self._async_download(str(file_id))
        caption = str(data.get("text") or "").strip()
        if caption and await self._is_receipt(caption):
            await self._async_receipt(chat_id, photo)
            return
        zone = None
        if caption:
            try:
                zone = await self._runtime.inventory.async_find_zone(caption)
            except InventoryError:
                zone = None
        if zone is None:
            await self._async_ask_zone(chat_id, photo)
            return
        await self._async_analyze(chat_id, photo, zone.id)

    async def _async_download(self, file_id: str) -> bytes:
        data: dict[str, Any] = {"file_id": file_id}
        if self.bot_entry_id:
            data["config_entry_id"] = self.bot_entry_id
        try:
            response = await self._hass.services.async_call(
                TELEGRAM, "download_file", data, blocking=True, return_response=True
            )
        except HomeAssistantError as err:
            raise InventoryError("telegram_download_failed", error=str(err)) from err
        path_text = (response or {}).get("file_path")
        if not path_text:
            raise InventoryError("telegram_download_failed", error="no file_path")
        path = Path(str(path_text))
        try:
            return await self._hass.async_add_executor_job(_read_and_delete, path)
        except OSError as err:
            raise InventoryError("telegram_download_failed", error=str(err)) from err

    async def _is_receipt(self, caption: str) -> bool:
        words = {
            normalize(word)
            for language in {configured_language(self._hass), "en"}
            for word in (
                await async_phrase(self._hass, language, "receipt_words")
            ).split(",")
        }
        return normalize(caption) in words

    async def _async_receipt(self, chat_id: int, photo: bytes) -> None:
        result = await self._runtime.purchases.async_from_receipt(
            photo, channel=Channel.TELEGRAM, user_id=None
        )
        await self.async_send_purchases(chat_id, result)

    async def async_send_purchases(self, chat_id: int, result: dict[str, Any]) -> None:
        """Describe the purchases read from a receipt, with buttons."""
        if result["proposal_id"] is None:
            await self.async_send(chat_id, await self._phrase("telegram_nothing"))
            return
        lines = [await self._phrase("telegram_purchases")]
        lines += [
            f"• {row['product'] or row['seen_as']}: {_number(row['proposed'])}"
            for row in result["items"]
        ]
        keyboard = [
            [
                (
                    await self._phrase("button_confirm"),
                    f"{CB_CONFIRM} {result['proposal_id']}",
                ),
                (
                    await self._phrase("button_reject"),
                    f"{CB_REJECT} {result['proposal_id']}",
                ),
            ]
        ]
        await self.async_send(chat_id, "\n".join(lines), keyboard)

    async def _async_ask_zone(self, chat_id: int, photo: bytes) -> None:
        now = dt_util.utcnow()
        self.held = {k: v for k, v in self.held.items() if v.expires > now}
        token = ulid_now()[-10:]
        self.held[token] = HeldPhoto(photo, chat_id, now + PHOTO_ZONE_TTL)
        zones = list(self._runtime.coordinator.data.zones.values())
        keyboard = [
            [
                (_label(zone.name), f"{CB_ZONE} {token} {zone.id}")
                for zone in zones[i : i + 2]
            ]
            for i in range(0, len(zones), 2)
        ]
        keyboard.append(
            [(await self._phrase("button_receipt"), f"{CB_ZONE} {token} {RECEIPT}")]
        )
        await self.async_send(
            chat_id, await self._phrase("telegram_choose_zone"), keyboard
        )

    async def _async_analyze(self, chat_id: int, photo: bytes, zone_id: str) -> None:
        result = await async_photo_pipeline(
            self._runtime, photo, zone_id, channel=Channel.TELEGRAM, user_id=None
        )
        await self.async_send_result(chat_id, result)

    async def async_send_result(self, chat_id: int, result: dict[str, Any]) -> None:
        """Describe an analysis: applied changes, proposal with buttons."""
        lines: list[str] = []
        keyboard: list[list[tuple[str, str]]] = []
        if result["applied"]:
            lines.append(await self._phrase("telegram_applied"))
            lines += [
                f"• {row['product']}: {_number(row['current'])} → {_number(row['proposed'])}"
                for row in result["applied"]
            ]
            keyboard.append([(await self._phrase("button_undo"), CB_UNDO)])
        if result["proposal_id"] is not None:
            lines.append(await self._phrase("telegram_proposal"))
            lines += [
                f"• {row['product'] or row['seen_as']}: "
                f"{_number(row['current'])} → {_number(row['proposed'])}"
                for row in result["proposed"]
            ]
            keyboard.append(
                [
                    (
                        await self._phrase("button_confirm"),
                        f"{CB_CONFIRM} {result['proposal_id']}",
                    ),
                    (
                        await self._phrase("button_reject"),
                        f"{CB_REJECT} {result['proposal_id']}",
                    ),
                ]
            )
        if not lines:
            lines.append(await self._phrase("telegram_nothing"))
        await self.async_send(chat_id, "\n".join(lines), keyboard or None)

    # Buttons

    async def _async_callback(self, chat_id: int, data: dict[str, Any]) -> None:
        parts = str(data.get("data") or "").split()
        query_id = data.get("id")
        if not parts:
            return
        command, args = parts[0], parts[1:]
        runtime = self._runtime
        answer = ""
        if command == CB_CONFIRM and args:
            await runtime.proposals.async_confirm(
                int(args[0]), channel=Channel.TELEGRAM, user_id=None
            )
            answer = await self._phrase("telegram_confirmed")
        elif command == CB_REJECT and args:
            await runtime.proposals.async_reject(int(args[0]))
            answer = await self._phrase("telegram_rejected")
        elif command == CB_ZONE and len(args) == 2:
            held = self.held.pop(args[0], None)
            if held is None or held.chat_id != chat_id:
                answer = await self._phrase("telegram_expired")
            else:
                await self._async_answer(query_id, "")
                if args[1] == RECEIPT:
                    await self._async_receipt(chat_id, held.data)
                else:
                    await self._async_analyze(chat_id, held.data, args[1])
                return
        elif command == CB_UNDO:
            await self._async_undo(chat_id, query_id)
            return
        elif command == CB_UNDO_CAMERA:
            await self._async_undo(
                chat_id, query_id, Channel.CAMERA, args[0] if args else None
            )
            return
        elif command == CB_ADD and args:
            await runtime.shopping.async_add(
                product_ref=int(args[0]),
                origin=ShoppingOrigin.SUGGESTION,
            )
            answer = await self._phrase("telegram_added")
        await runtime.coordinator.async_refresh()
        await self._async_answer(query_id, answer)

    async def _async_undo(
        self,
        chat_id: int,
        query_id: Any,
        channel: Channel = Channel.TELEGRAM,
        operation_id: str | None = None,
    ) -> None:
        """Undo the operation named by the button, or the channel's last one."""
        try:
            if operation_id is not None:
                await self._runtime.inventory.async_undo_last(
                    channel=channel, operation_id=operation_id
                )
            else:
                await self._runtime.inventory.async_undo_last(
                    channel=channel,
                    within=timedelta(minutes=10),
                    only_channel=True,
                )
            text = await self._phrase("telegram_undone")
        except InventoryError:
            text = await self._phrase("telegram_nothing_to_undo")
        await self._runtime.coordinator.async_refresh()
        if query_id is not None:
            await self._async_answer(query_id, text)
        else:
            await self.async_send(chat_id, text)

    async def _async_answer(self, query_id: Any, message: str) -> None:
        if query_id is None:
            return
        data: dict[str, Any] = {"callback_query_id": query_id, "message": message}
        if self.bot_entry_id:
            data["config_entry_id"] = self.bot_entry_id
        try:
            await self._hass.services.async_call(
                TELEGRAM, "answer_callback_query", data, blocking=True
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Telegram answer failed: %s", err)

    # Outgoing

    async def async_send_suggestions(self, chat_id: int) -> None:
        """Send the list and the suggestions with buttons to add them."""
        data = self._runtime.coordinator.data
        await self._async_command(chat_id, "/list", "")
        if not data.analysis.suggestions:
            return
        keyboard = [
            [(_label(f"+ {s.product.name}"), f"{CB_ADD} {s.product.id}")]
            for s in data.analysis.suggestions[:8]
        ]
        await self.async_send(
            chat_id, await self._phrase("telegram_suggestions"), keyboard
        )

    async def async_send(
        self,
        chat_id: int,
        message: str,
        keyboard: list[list[tuple[str, str]]] | None = None,
    ) -> None:
        """Send a plain-text message, optionally with inline buttons."""
        if not message:
            return
        data: dict[str, Any] = {
            "message": message,
            "chat_id": [chat_id],
            "parse_mode": "plain_text",
        }
        if self.bot_entry_id:
            data["config_entry_id"] = self.bot_entry_id
        if keyboard:
            data["inline_keyboard"] = [
                ", ".join(f"{label}:{command}" for label, command in row)
                for row in keyboard
            ]
        try:
            await self._hass.services.async_call(
                TELEGRAM, "send_message", data, blocking=True
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Telegram message to %s failed: %s", chat_id, err)


def _read_and_delete(path: Path) -> bytes:
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)
