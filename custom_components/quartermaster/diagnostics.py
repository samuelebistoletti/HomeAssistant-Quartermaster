"""Diagnostics: counts and settings, with personal data redacted.

Spec §8: no user names, chat IDs, paths, product names or images. Product
and zone names are household data, so only counts are given.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from custom_components.quartermaster import QuartermasterConfigEntry
from custom_components.quartermaster.const import (
    CONF_AGENT_ID,
    CONF_AI_TASK_IMAGE,
    CONF_AI_TASK_TEXT,
    CONF_EMAIL_ENTRY,
    CONF_EMAIL_SENDERS,
    CONF_EXTERNAL_TODO,
    CONF_TELEGRAM_BOT,
    CONF_TELEGRAM_CHATS,
    CONF_TELEGRAM_EVENT,
)
from custom_components.quartermaster.notify import MessageType, option_key
from custom_components.quartermaster.storage.migrations import MIGRATIONS

TO_REDACT = {
    CONF_AGENT_ID,
    CONF_AI_TASK_IMAGE,
    CONF_AI_TASK_TEXT,
    CONF_EMAIL_ENTRY,
    CONF_EMAIL_SENDERS,
    CONF_EXTERNAL_TODO,
    CONF_TELEGRAM_BOT,
    CONF_TELEGRAM_CHATS,
    CONF_TELEGRAM_EVENT,
    *(option_key(kind) for kind in MessageType),
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: QuartermasterConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    data = coordinator.data
    journal = await runtime.inventory.async_list_journal(limit=1000)
    return {
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "schema_version": MIGRATIONS[-1][0],
        "last_update_success": coordinator.last_update_success,
        "counts": {
            "zones": len(data.zones),
            "products": len(data.products),
            "essentials": sum(1 for p in data.products.values() if p.essential),
            "stock_rows": len(data.stock),
            "journal_recent": len(journal),
            "shopping_items": len(data.shopping_items),
            "to_buy": len(data.analysis.to_buy),
            "suggestions": len(data.analysis.suggestions),
            "expiring": len(data.analysis.expiring),
            "pending_proposals": len(data.proposals),
            "photo_requests": len(data.photo_requests),
            "analyses_today": data.analyses_today,
        },
        "zones": [
            {
                "sources": [source.get("type") for source in zone.sources],
                "has_schedule": zone.check_schedule is not None,
                "last_check_status": (data.zone_checks.get(zone.id) or {}).get(
                    "status"
                ),
                "failures": data.zone_failures.get(zone.id, 0),
            }
            for zone in data.zones.values()
        ],
        "channels": {
            kind.value: sum(1 for e in journal if e.channel is kind)
            for kind in {entry_.channel for entry_ in journal}
        },
        "telegram_enabled": runtime.telegram is not None,
        "show_sources_suspended": len(runtime.show.suspended),
        "voice_sessions": len(runtime.voice.sessions),
    }
