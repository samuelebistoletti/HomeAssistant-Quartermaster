"""Shared fixtures for the Quartermaster tests."""

from __future__ import annotations

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.quartermaster.const import DOMAIN


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return an unregistered Quartermaster config entry."""
    return MockConfigEntry(domain=DOMAIN, title="Quartermaster", data={})
