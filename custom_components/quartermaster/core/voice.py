"""quartermaster.ask: conversation sessions per channel, for Alexa and others.

Each session (channel + key) keeps the conversation_id of the last exchange
and any pending clarification (the candidates returned by adjust_stock) for a
configurable time, so an answer to a question continues the same thread even
when it arrives as a new request. Replies not ready within 5 seconds are
acknowledged at once and completed in the background.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import re
import time
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.quartermaster.const import ASK_PER_MINUTE, DOMAIN
from custom_components.quartermaster.core.phrases import async_phrase
from custom_components.quartermaster.storage.models import Channel

_LOGGER = logging.getLogger(__name__)

REPLY_TIMEOUT = 5.0
EVENT_ASK_RESULT = f"{DOMAIN}_ask_result"
_QUESTION = re.compile(r"[^.!?]*\?")


# Session key of a Telegram chat: the prefix and the chat id.
TELEGRAM_SESSION_PREFIX = "chat:"

@dataclass(slots=True)
class PendingClarification:
    """A write held back until the user picks one of the candidates."""

    action: str | None
    quantity: float | None
    zone_id: str | None
    candidates: list[str]


@dataclass(slots=True)
class Session:
    """State of one conversation thread."""

    channel: Channel
    key: str
    expires: datetime
    conversation_id: str | None = None
    pending: PendingClarification | None = None


@dataclass(slots=True)
class AskReply:
    """What quartermaster.ask returns."""

    speech: str
    continue_conversation: bool
    reprompt: str
    conversation_id: str | None
    in_progress: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return the action response."""
        return {
            "speech": self.speech,
            "continue_conversation": self.continue_conversation,
            "reprompt": self.reprompt,
            "conversation_id": self.conversation_id,
            "in_progress": self.in_progress,
        }


def _dig(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def reprompt_for(speech: str) -> str:
    """Return the last question in the reply, to repeat it, or the reply."""
    questions = _QUESTION.findall(speech)
    return questions[-1].strip() if questions else speech.strip()


@dataclass
class VoiceSessions:
    """Sessions by channel and key, and the channel of each running request."""

    hass: HomeAssistant
    agent_id: str | None
    lifetime: timedelta
    sessions: dict[tuple[Channel, str], Session] = field(default_factory=dict)
    # Context id of a running conversation.process call -> its session, so the
    # LLM tools know the channel and can store a pending clarification.
    running: dict[str, Session] = field(default_factory=dict)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    _recent: dict[Channel, deque[float]] = field(default_factory=dict)

    def _within_rate(self, channel: Channel) -> bool:
        """Allow at most ASK_PER_MINUTE requests per channel and minute."""
        now = time.monotonic()
        recent = self._recent.setdefault(channel, deque())
        while recent and now - recent[0] > 60:
            recent.popleft()
        if len(recent) >= ASK_PER_MINUTE:
            return False
        recent.append(now)
        return True

    def session(self, channel: Channel, key: str) -> Session:
        """Return the live session for the channel and key, or a new one."""
        now = dt_util.utcnow()
        session = self.sessions.get((channel, key))
        if session is None or session.expires <= now:
            session = Session(channel=channel, key=key, expires=now + self.lifetime)
            self.sessions[(channel, key)] = session
        return session

    def for_context(self, context: Context | None) -> Session | None:
        """Return the session of a request made by quartermaster.ask."""
        if context is None:
            return None
        return self.running.get(context.id) or (
            self.running.get(context.parent_id) if context.parent_id else None
        )

    async def async_ask(
        self,
        text: str,
        *,
        channel: Channel,
        key: str,
        language: str | None,
        context: Context,
    ) -> AskReply:
        """Send the text to the agent within the session; answer in time."""
        if not self._within_rate(channel):
            return AskReply(
                speech=await async_phrase(self.hass, language, "ask_rate_limited"),
                continue_conversation=False,
                reprompt="",
                conversation_id=None,
            )
        session = self.session(channel, key)
        session.expires = dt_util.utcnow() + self.lifetime
        child = Context(user_id=context.user_id, parent_id=context.id)
        self.running[child.id] = session
        task = self.hass.async_create_background_task(
            self._async_process(text, session, language, child),
            f"{DOMAIN} ask",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        done, _ = await asyncio.wait({task}, timeout=REPLY_TIMEOUT)
        if done:
            return task.result()
        task.add_done_callback(lambda finished: self._announce(finished, session))
        return AskReply(
            speech=await async_phrase(self.hass, language, "ask_working"),
            continue_conversation=False,
            reprompt="",
            conversation_id=session.conversation_id,
            in_progress=True,
        )

    async def _async_process(
        self, text: str, session: Session, language: str | None, context: Context
    ) -> AskReply:
        data: dict[str, Any] = {"text": text}
        if self.agent_id:
            data["agent_id"] = self.agent_id
        if language:
            data["language"] = language
        if session.conversation_id:
            data["conversation_id"] = session.conversation_id
        try:
            response = await self.hass.services.async_call(
                "conversation",
                "process",
                data,
                blocking=True,
                return_response=True,
                context=context,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("The conversation agent failed: %s", err)
            return AskReply(
                speech=await async_phrase(self.hass, language, "ask_error"),
                continue_conversation=False,
                reprompt="",
                conversation_id=session.conversation_id,
            )
        finally:
            self.running.pop(context.id, None)
        if _dig(response, "response", "response_type") == "error":
            _LOGGER.warning(
                "The conversation agent returned an error: %s",
                _dig(response, "response", "speech", "plain", "speech"),
            )
            return AskReply(
                speech=await async_phrase(self.hass, language, "ask_error"),
                continue_conversation=False,
                reprompt="",
                conversation_id=session.conversation_id,
            )
        speech = str(_dig(response, "response", "speech", "plain", "speech") or "")
        session.conversation_id = _dig(response, "conversation_id") or None
        session.expires = dt_util.utcnow() + self.lifetime
        follow_up = bool(_dig(response, "continue_conversation"))
        if not follow_up:
            session.pending = None
        return AskReply(
            speech=speech,
            continue_conversation=follow_up,
            reprompt=reprompt_for(speech) if follow_up else "",
            conversation_id=session.conversation_id,
        )

    def _announce(self, task: asyncio.Task[AskReply], session: Session) -> None:
        """Publish a late reply; notifications pick it up (Phase 8)."""
        if task.cancelled() or task.exception() is not None:
            return
        self.hass.bus.async_fire(
            EVENT_ASK_RESULT,
            {"channel": session.channel.value, "session_key": session.key}
            | task.result().as_dict(),
        )

    async def async_shutdown(self) -> None:
        """Cancel requests still running."""
        for task in list(self._tasks):
            task.cancel()
