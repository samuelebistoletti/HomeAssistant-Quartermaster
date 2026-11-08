"""Tests for notification routing."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.inventory import StockOperation
from custom_components.quartermaster.notify import in_quiet_hours
from custom_components.quartermaster.storage.models import Channel


def test_quiet_hours() -> None:
    """Windows across midnight and malformed values."""
    late, noon = datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 1, 12, 0)
    assert in_quiet_hours("22:00-07:00", late)
    assert not in_quiet_hours("22:00-07:00", noon)
    assert in_quiet_hours("11:00-13:00", noon)
    for spec in (None, "", "nonsense", "25:00-07:00"):
        assert not in_quiet_hours(spec, late)


class Notify:
    """notify.send_message recording messages per entity."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.messages: list[tuple[str, str]] = []
        self.fail: set[str] = set()
        hass.services.async_register("notify", "send_message", self._send)
        hass.states.async_set("notify.phone", "unknown")
        hass.states.async_set("notify.echo", "unavailable")
        hass.states.async_set("notify.tablet", "unknown")

    async def _send(self, call: ServiceCall) -> None:
        targets = call.data["entity_id"]
        for entity_id in [targets] if isinstance(targets, str) else targets:
            if entity_id in self.fail:
                raise ServiceValidationError("down")
            self.messages.append((entity_id, call.data["message"]))


@pytest.fixture
async def notified(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> tuple[MockConfigEntry, Notify]:
    """Every message type to echo (unavailable), then phone, then tablet."""
    hass.config.language = "it"
    notify = Notify(hass)
    chain = ["notify.echo", "notify.phone", "notify.tablet"]
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            f"notify_{kind}": chain
            for kind in (
                "photo_request",
                "check_result",
                "expiring",
                "camera",
                "essential_finished",
                "shopping",
                "weekly",
                "voice_reply",
            )
        },
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    inventory = init_integration.runtime_data.inventory
    await inventory.async_add_zone("Fridge", aliases=["frigo"])
    await inventory.async_add_product("Milk", default_zone="fridge", unit="bottle")
    await inventory.async_set_stock("milk", 2, channel=Channel.SERVICE)
    await init_integration.runtime_data.coordinator.async_refresh()
    return init_integration, notify


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_fallback_and_events(hass: HomeAssistant, notified: Any) -> None:
    """The first available entity gets the message; events become texts."""
    entry, notify = notified
    hass.bus.async_fire(
        f"{DOMAIN}_photo_request",
        {"zone": "Frigo", "reason": "low_confidence", "reminder": False},
    )
    await _settle(hass)
    assert notify.messages[-1] == (
        "notify.phone",
        "Mandami una foto di Frigo: l'immagine della telecamera non era abbastanza chiara.",
    )
    notify.fail.add("notify.phone")
    hass.bus.async_fire(
        f"{DOMAIN}_show_recognized",
        {"zone": "Garage", "product": "Passata", "action": "taken", "quantity": 1},
    )
    await _settle(hass)
    assert notify.messages[-1] == (
        "notify.tablet",
        "Ho segnato 1 Passata in meno in Garage.",
    )
    notify.fail.clear()
    hass.bus.async_fire(
        f"{DOMAIN}_ask_result", {"channel": "alexa", "speech": "Fatto, latte finito."}
    )
    hass.bus.async_fire(f"{DOMAIN}_ask_result", {"channel": "telegram", "speech": "x"})
    hass.bus.async_fire(
        f"{DOMAIN}_zone_checked",
        {"zone_id": "fridge", "status": "updated", "applied": 2, "proposal_id": 5},
    )
    hass.bus.async_fire(
        f"{DOMAIN}_zone_checked", {"zone_id": "fridge", "status": "failed"}
    )
    await _settle(hass)
    texts = [text for _, text in notify.messages[-2:]]
    assert texts == [
        "Fatto, latte finito.",
        "Controllo di Fridge fatto: 2 variazioni applicate, proposta in attesa: 1.",
    ]


async def test_essential_running_out(hass: HomeAssistant, notified: Any) -> None:
    """An essential going below its minimum is announced once."""
    entry, notify = notified
    inventory = entry.runtime_data.inventory
    await inventory.async_set_essential("milk", essential=True, min_stock=3)
    await entry.runtime_data.coordinator.async_refresh()
    await _settle(hass)
    await entry.runtime_data.coordinator.async_refresh()
    await _settle(hass)
    assert [t for _, t in notify.messages].count(
        "Milk sta finendo: l'ho messo in lista."
    ) == 1


async def test_shopping_message_with_reasons(
    hass: HomeAssistant, notified: Any
) -> None:
    """Go shopping sends the list, then suggestions with readable reasons."""
    entry, notify = notified
    inventory = entry.runtime_data.inventory
    await inventory.async_set_essential(
        "milk", essential=True, min_stock=1, target_stock=4
    )
    await inventory.async_add_product("Jam", default_zone="fridge")
    await inventory.async_set_stock("jam", 1, channel=Channel.SERVICE)
    await inventory.async_set_stock("jam", 0, channel=Channel.SERVICE)
    await inventory.async_adjust_stock(
        "jam", StockOperation.PURCHASE, channel=Channel.SERVICE
    )
    await inventory.async_adjust_stock(
        "jam", StockOperation.FINISH, channel=Channel.SERVICE
    )
    await hass.services.async_call(DOMAIN, "start_shopping", {}, blocking=True)
    await _settle(hass)
    texts = [t for _, t in notify.messages]
    assert "La lista della spesa è vuota." in texts
    suggestions = next(t for t in texts if t.startswith("Potrebbe servirti anche:"))
    today = dt_util.now().date()
    assert "• Milk: ne hai 2, di solito ne tieni 4" in suggestions
    assert f"• Jam: l'hai finito il {today.day}/{today.month}" in suggestions


async def test_quiet_hours_hold_messages(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notified: Any
) -> None:
    """During quiet hours messages wait; they go out when quiet hours end."""
    entry, notify = notified
    local = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 11, 8, 23, 0, tzinfo=local))
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "quiet_hours": "22:00-07:00"}
    )
    hass.bus.async_fire(
        f"{DOMAIN}_photo_request",
        {"zone": "Frigo", "reason": "reminder", "reminder": True},
    )
    await _settle(hass)
    assert notify.messages == []
    assert len(entry.runtime_data.notifier.queue) == 1
    freezer.move_to(datetime(2026, 11, 9, 7, 1, 30, tzinfo=local))
    async_fire_time_changed(hass)
    await _settle(hass)
    assert notify.messages[-1][1].startswith(
        "Promemoria: serve ancora una foto di Frigo"
    )


async def test_expiring_and_weekly(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, notified: Any
) -> None:
    """At 9:00 expiring products go out; on Monday also the weekly summary."""
    entry, notify = notified
    inventory = entry.runtime_data.inventory
    local = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 11, 9, 8, 0, tzinfo=local))  # Monday
    await inventory.async_set_stock(
        "milk", 1, channel=Channel.SERVICE, best_before=date(2026, 11, 10)
    )
    await entry.runtime_data.coordinator.async_refresh()
    freezer.move_to(datetime(2026, 11, 9, 9, 0, 30, tzinfo=local))
    async_fire_time_changed(hass)
    await _settle(hass)
    texts = [t for _, t in notify.messages]
    assert "In scadenza:\n• Milk (Fridge): 10/11" in texts
    assert any(t.startswith("Questa settimana:") for t in texts)

    # Nothing expiring: no message.
    await inventory.async_set_stock("milk", 0, channel=Channel.SERVICE)
    await entry.runtime_data.coordinator.async_refresh()
    count = len(notify.messages)
    await entry.runtime_data.notifier.async_send_expiring()
    assert len(notify.messages) == count


async def test_telegram_copies_with_buttons(hass: HomeAssistant, notified: Any) -> None:
    """With telegram_notify on, private allowed chats also get the message."""
    entry, _ = notified
    sent: list[dict[str, Any]] = []

    async def send(call: ServiceCall) -> None:
        sent.append(dict(call.data))

    hass.services.async_register("telegram_bot", "send_message", send)
    hass.states.async_set("event.bot", "unknown")
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "telegram_event_entity": "event.bot",
            "telegram_chats": ["11", "-22"],
            "telegram_notify": True,
        },
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire(
        f"{DOMAIN}_show_recognized",
        {"zone": "Garage", "product": "Passata", "action": "returned", "quantity": 2},
    )
    await _settle(hass)
    [message] = sent
    assert message["chat_id"] == [11]
    assert message["message"] == "Ho segnato 2 Passata rimesso in Garage."
    assert message["inline_keyboard"] == ["Annulla:/qm_undo_camera"]
