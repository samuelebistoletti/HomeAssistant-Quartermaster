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
from custom_components.quartermaster.core.confirmations import MISSING, PURCHASE
from custom_components.quartermaster.core.events import is_fresh_event
from custom_components.quartermaster.core.inventory import (
    InventoryError,
    StockOperation,
    normalize,
)
from custom_components.quartermaster.core.phrases import (
    async_error_text,
    async_phrase,
    configured_language,
)
from custom_components.quartermaster.core.voice import (
    EVENT_ASK_RESULT,
    TELEGRAM_SESSION_PREFIX,
)
from custom_components.quartermaster.core.zones import CheckStatus
from custom_components.quartermaster.storage.models import (
    Channel,
    Proposal,
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
CB_QUANTITY = "/qm_qty"
# A row not seen in a picture, confirmed on its own: "/qm_gone <proposal> <key>".
CB_GONE = "/qm_gone"
# An unknown item created as a new product: "/qm_new <proposal> <key>"; a
# receipt asks for its zone first: "/qm_newz <proposal> <key> <zone>".
CB_NEW = "/qm_new"
CB_NEW_ZONE = "/qm_newz"
# Guided check of a zone kept by word: one product less or finished, done.
CB_LEDGER = "/qm_lg"
CB_LEDGER_OK = "/qm_lg_ok"
CB_ZONE = "/qm_zone"
# A zone picked after /check without a zone: "/qm_chk <zone>".
CB_CHECK = "/qm_chk"
# Zone ids are slugs, so this can never be one.
RECEIPT = "@receipt"
CB_UNDO = "/qm_undo"
CB_ADD = "/qm_add"
CB_UNDO_CAMERA = "/qm_undo_camera"


async def async_proposal_lines(
    hass: HomeAssistant, rows: list[dict[str, Any]]
) -> list[str]:
    """One line per proposed row; rows not seen and unknown ones say so.

    Purchase rows have no current quantity: only the quantity bought.
    """
    not_seen = await async_phrase(hass, None, "telegram_not_seen")
    unknown = await async_phrase(hass, None, "telegram_unknown")
    lines = []
    for row in rows:
        change = (
            _number(row["proposed"])
            if row["current"] is None
            else f"{_number(row['current'])} → {_number(row['proposed'])}"
        )
        note = (
            f" ({not_seen})"
            if row.get(MISSING)
            else f" ({unknown})"
            if row["product_id"] is None
            else ""
        )
        lines.append(f"• {row['product'] or row['seen_as']}: {change}{note}")
    return lines


async def async_proposal_buttons(
    hass: HomeAssistant, proposal_id: int, rows: list[dict[str, Any]]
) -> list[list[tuple[str, str]]]:
    """Confirm and reject a proposal; own buttons for rows not seen or unknown.

    Confirm applies what was seen only: a product not seen may be hidden, so
    it is set to zero only when its own button is tapped; an unknown item is
    created as a new product only by its own button, and otherwise waits.
    """
    keyboard: list[list[tuple[str, str]]] = []
    for row in rows:
        if row.get(MISSING):
            label = await async_phrase(
                hass, None, "button_ledger_finished", product=row["product"]
            )
            command = CB_GONE
        elif row["product_id"] is None:
            label = await async_phrase(
                hass, None, "button_new_product", product=row["seen_as"]
            )
            command = CB_NEW
        else:
            continue
        keyboard.append([(_label(label), f"{command} {proposal_id} {row['key']}")])
    last: list[tuple[str, str]] = []
    if any(not row.get(MISSING) and row["product_id"] is not None for row in rows):
        last.append(
            (
                await async_phrase(hass, None, "button_confirm"),
                f"{CB_CONFIRM} {proposal_id}",
            )
        )
    last.append(
        (await async_phrase(hass, None, "button_reject"), f"{CB_REJECT} {proposal_id}")
    )
    keyboard.append(last)
    return keyboard


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
        elif name in ("check", "controlla") and not args.strip():
            # From the bot's command menu, which sends no zone: pick one.
            zones = list(runtime.coordinator.data.zones.values())
            keyboard = [
                [
                    (_label(zone.name), f"{CB_CHECK} {zone.id}")
                    for zone in zones[i : i + 2]
                ]
                for i in range(0, len(zones), 2)
            ]
            await self.async_send(
                chat_id, await self._phrase("telegram_choose_check"), keyboard
            )
        elif name in ("check", "controlla"):
            await self._async_check(chat_id, args.strip())
        else:
            await self.async_send(chat_id, await self._phrase("telegram_help"))

    async def _async_check(self, chat_id: int, zone_ref: str) -> None:
        """Check a zone for this chat and say how it went."""
        # A zone kept by word answers with its list; a camera is read; a zone
        # with photos asks this chat for one.
        outcome = await self._runtime.zones.async_check_zone(
            zone_ref, channel=Channel.TELEGRAM, chat_id=chat_id
        )
        zones = self._runtime.coordinator.data.zones
        zone = zones[outcome.zone_id].name if outcome.zone_id in zones else zone_ref
        if outcome.status is CheckStatus.UPDATED:
            text = await self._phrase(
                "notify_check_result",
                zone=zone,
                applied=str(outcome.result.get("applied") or 0),
                proposed="1" if outcome.result.get("proposal_id") else "0",
            )
            await self.async_send(chat_id, text)
        elif outcome.status is CheckStatus.FAILED:
            reason = await self._phrase(f"reason_{outcome.reason or 'analysis_failed'}")
            text = await self._phrase("notify_check_failed", zone=zone, reason=reason)
            await self.async_send(chat_id, text)

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
        lines += await async_proposal_lines(self._hass, result["items"])
        if any(row["product_id"] is None for row in result["items"]):
            lines.append(await self._phrase("telegram_unknown_hint"))
        keyboard = await async_proposal_buttons(
            self._hass, result["proposal_id"], result["items"]
        )
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
            lines += await async_proposal_lines(self._hass, result["proposed"])
            if any(row.get(MISSING) for row in result["proposed"]):
                lines.append(await self._phrase("telegram_not_seen_hint"))
            if any(row["product_id"] is None for row in result["proposed"]):
                lines.append(await self._phrase("telegram_unknown_hint"))
            keyboard += await async_proposal_buttons(
                self._hass, result["proposal_id"], result["proposed"]
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
            outcome = await runtime.proposals.async_confirm(
                int(args[0]), channel=Channel.TELEGRAM, user_id=None
            )
            answer = await self._phrase(
                "telegram_confirmed_unknown_waiting"
                if outcome.waiting
                else "telegram_confirmed"
            )
        elif command == CB_NEW and len(args) == 2:
            proposal = await runtime.proposals.async_get(int(args[0]))
            if proposal.payload.get("kind") == PURCHASE:
                # A receipt has no zone: ask where the new product goes.
                await self._async_answer(query_id, "")
                await self._async_ask_new_zone(chat_id, proposal, int(args[1]))
                return
            answer = await self._async_new_product(int(args[0]), int(args[1]))
        elif command == CB_NEW_ZONE and len(args) == 3:
            answer = await self._async_new_product(
                int(args[0]), int(args[1]), zone=args[2]
            )
        elif command == CB_GONE and len(args) == 2:
            # A product the picture did not show, finished after all.
            await runtime.proposals.async_confirm(
                int(args[0]),
                keys=[int(args[1])],
                channel=Channel.TELEGRAM,
                user_id=None,
            )
            answer = await self._phrase("telegram_confirmed")
        elif command == CB_QUANTITY and len(args) == 2:
            # A camera sighting confirmed with another quantity taken.
            await runtime.proposals.async_confirm(
                int(args[0]),
                corrections={0: {"moved": float(args[1])}},
                channel=Channel.TELEGRAM,
                user_id=None,
            )
            answer = await self._phrase("telegram_confirmed")
        elif command == CB_LEDGER and len(args) == 3:
            movement = await runtime.inventory.async_adjust_stock(
                int(args[1]),
                StockOperation.FINISH if args[2] == "f" else StockOperation.CONSUME,
                zone_ref=args[0],
                channel=Channel.TELEGRAM,
            )
            answer = await self._phrase(
                "telegram_ledger_left",
                product=movement.product.name,
                quantity=_number(movement.entry.quantity_after),
            )
        elif command == CB_LEDGER_OK and args:
            zone = await runtime.inventory.async_find_zone(args[0])
            answer = await self._phrase("telegram_ledger_done", zone=zone.name)
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
        elif command == CB_CHECK and args:
            await self._async_answer(query_id, "")
            await self._async_check(chat_id, args[0])
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

    async def _async_ask_new_zone(
        self, chat_id: int, proposal: Proposal, key: int
    ) -> None:
        """Ask in which zone an unknown item of a receipt goes."""
        row = next(row for row in proposal.payload["items"] if row["key"] == key)
        zones = list(self._runtime.coordinator.data.zones.values())
        keyboard = [
            [
                (_label(zone.name), f"{CB_NEW_ZONE} {proposal.id} {key} {zone.id}")
                for zone in zones[i : i + 2]
            ]
            for i in range(0, len(zones), 2)
        ]
        await self.async_send(
            chat_id,
            await self._phrase("telegram_new_product_zone", product=row["seen_as"]),
            keyboard,
        )

    async def _async_new_product(
        self, proposal_id: int, key: int, *, zone: str | None = None
    ) -> str:
        """Create an unknown item as a new product, named as it was read."""
        correction: dict[str, Any] = {"new_product": True}
        if zone is not None:
            correction["zone"] = zone
        outcome = await self._runtime.proposals.async_confirm(
            proposal_id,
            keys=[key],
            corrections={key: correction},
            channel=Channel.TELEGRAM,
            user_id=None,
        )
        return await self._phrase(
            "telegram_new_product", product=outcome.applied[0]["product"]
        )

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
