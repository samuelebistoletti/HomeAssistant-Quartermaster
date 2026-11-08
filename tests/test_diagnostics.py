"""Tests for diagnostics."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.quartermaster.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.quartermaster.storage.models import Channel


async def test_diagnostics_redact(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Counts are given; entity ids, chats and names are not."""
    hass.config_entries.async_update_entry(
        init_integration,
        options={"telegram_chats": ["12345"], "agent_id": "conversation.home_agent"},
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    inventory = init_integration.runtime_data.inventory
    await inventory.async_add_zone("Back room")
    await inventory.async_add_product("Family sauce", default_zone="back_room")
    await inventory.async_set_stock("family sauce", 1, channel=Channel.SERVICE)
    await init_integration.runtime_data.coordinator.async_refresh()

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    text = repr(result)
    for secret in ("12345", "conversation.home_agent", "Back room", "Family sauce"):
        assert secret not in text
    assert result["counts"]["products"] == 1
    assert result["channels"] == {"service": 1}
    assert result["zones"] == [
        {"sources": [], "has_schedule": False, "last_check_status": None, "failures": 0}
    ]
