"""Tests for setting up and unloading Quartermaster."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.quartermaster import QuartermasterRuntimeData
from custom_components.quartermaster.storage.sqlite import SchemaTooNewError


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The entry loads with runtime data and unloads cleanly."""
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert isinstance(mock_config_entry.runtime_data, QuartermasterRuntimeData)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_database_too_new(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A database from a newer version stops the setup with an error."""
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.quartermaster.SQLiteBackend.async_open",
        side_effect=SchemaTooNewError(9, 1),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.error_reason_translation_key == "database_too_new"


async def test_database_unreadable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, config_dir: Path
) -> None:
    """A file that is not a database stops the setup with an error."""
    database = config_dir / "quartermaster" / "pantry.db"
    database.parent.mkdir()
    database.write_bytes(b"not a database" * 100)
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.error_reason_translation_key == "database_error"
