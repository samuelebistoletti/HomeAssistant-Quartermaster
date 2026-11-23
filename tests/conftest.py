"""Shared fixtures for the Quartermaster tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.models import User
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import MockConfigEntry, MockUser

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.inventory import InventoryService
from custom_components.quartermaster.storage.sqlite import SQLiteBackend


@pytest.fixture(autouse=True)
async def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Keep the database and exports of each test in its own folder.

    Also set up the core `homeassistant` component, which `conversation`
    (a dependency) needs, as on a real instance.
    """
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "homeassistant", {})
    return tmp_path


@pytest.fixture
async def guest_user(hass: HomeAssistant) -> User:
    """A non-admin user Home Assistant lets control entities (Users group)."""
    group = await hass.auth.async_get_group(GROUP_ID_USER)
    assert group is not None
    return MockUser(name="Guest", groups=[group]).add_to_hass(hass)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return an unregistered Quartermaster config entry."""
    return MockConfigEntry(domain=DOMAIN, title="Quartermaster", data={})


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up Quartermaster and return its loaded entry."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


@pytest.fixture
async def backend(hass: HomeAssistant, tmp_path: Path) -> AsyncIterator[SQLiteBackend]:
    """Return an opened SQLite backend in the test folder."""
    store = SQLiteBackend(hass, tmp_path / "quartermaster" / "pantry.db")
    await store.async_open()
    yield store
    await store.async_close()


@pytest.fixture
def inventory(backend: SQLiteBackend) -> InventoryService:
    """Return an inventory service on an empty store."""
    return InventoryService(backend)
