"""Tests for the Quartermaster config flow."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.phrases import (
    async_phrase,
    configured_language,
)


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    hass.config.language = "it"
    hass.config.location_name = "Casa"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    # Entity names start with the entry name: the default says where they come
    # from, not the home name shared by every other device.
    [name_key, _] = result["data_schema"].schema
    assert name_key.default() == "Quartermaster"
    return result


async def test_guided_flow(hass: HomeAssistant) -> None:
    """Every step in order; zones, essentials and import become entry data."""
    result = await _start(hass)
    configure = hass.config_entries.flow.async_configure
    result = await configure(result["flow_id"], {"name": "Casa", "language": "it"})
    assert result["step_id"] == "models"
    result = await configure(result["flow_id"], {"structure_mode": "text"})
    assert result["step_id"] == "zones"
    schema = result["data_schema"].schema
    [zones_key] = schema
    assert zones_key.default() == ["Dispensa", "Frigorifero", "Freezer"]

    result = await configure(
        result["flow_id"], {"zones": ["Frigorifero", " Garage  scaffale ", ""]}
    )
    assert result["step_id"] == "zone"
    assert result["description_placeholders"] == {"zone": "Frigorifero"}
    result = await configure(
        result["flow_id"],
        {"camera": "camera.fridge", "roi": "0.1 0.2 0.9 1", "check": "list"},
    )
    assert result["description_placeholders"] == {"zone": "Garage scaffale"}
    result = await configure(
        result["flow_id"], {"camera": "camera.garage", "roi": "0.5, 0.5, 0.2, 1"}
    )
    assert result["errors"] == {"roi": "invalid_roi"}
    result = await configure(result["flow_id"], {"camera": "camera.garage"})
    assert result["step_id"] == "channels"
    result = await configure(result["flow_id"], {"alexa_read_only": True})
    assert result["step_id"] == "notifications"
    result = await configure(result["flow_id"], {"quiet_hours": "22:00-07:00"})
    assert result["step_id"] == "essentials"
    result = await configure(
        result["flow_id"],
        {
            "essentials": ["Caffè", " "],
            "import_todo": "todo.bring",
            "import_target": "products",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Casa"
    assert result["data"] == {
        "initial_zones": [
            {
                "name": "Frigorifero",
                "check": "list",
                "camera": "camera.fridge",
                "roi": [0.1, 0.2, 0.9, 1],
            },
            {"name": "Garage scaffale", "camera": "camera.garage"},
        ],
        "initial_essentials": ["Caffè"],
        "initial_import": {"entity_id": "todo.bring", "target": "products"},
    }
    options = result["options"]
    assert options["language"] == "it"
    assert options["structure_mode"] == "text"
    assert options["alexa_read_only"] is True
    assert options["quiet_hours"] == "22:00-07:00"


async def test_flow_without_zones(hass: HomeAssistant) -> None:
    """No zones and no essentials: an entry with empty data."""
    result = await _start(hass)
    configure = hass.config_entries.flow.async_configure
    result = await configure(result["flow_id"], {"name": " ", "language": "en"})
    result = await configure(result["flow_id"], {})
    assert result["data_schema"].schema
    [zones_key] = result["data_schema"].schema
    assert zones_key.default() == ["Pantry", "Fridge", "Freezer"]
    result = await configure(result["flow_id"], {"zones": []})
    assert result["step_id"] == "channels"
    result = await configure(result["flow_id"], {})
    result = await configure(result["flow_id"], {})
    result = await configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Quartermaster"
    assert result["data"] == {}


async def test_initial_setup_applied_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The first setup creates zones and essentials, then clears entry data."""
    hass.states.async_set("camera.fridge", "idle")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Casa",
        data={
            "initial_zones": [
                {"name": "Frigorifero", "camera": "camera.fridge", "roi": [0, 0, 1, 1]},
                {"name": "Dispensa"},
                {"name": "dispensa"},
                {"name": "Mobile", "check": "list"},
            ],
            "initial_essentials": ["Caffè", "Caffè"],
            "initial_import": {"entity_id": "todo.missing", "target": "essentials"},
        },
        options={"language": "it"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data == {}
    inventory = entry.runtime_data.inventory
    zones = {zone.id: zone for zone in await inventory.async_list_zones()}
    assert set(zones) == {"frigorifero", "dispensa", "mobile"}
    assert zones["mobile"].sources == [{"type": "ledger"}]  # 30 days by default
    assert [source["type"] for source in zones["frigorifero"].sources] == [
        "camera",
        "manual",
    ]
    assert zones["frigorifero"].sources[0]["roi"] == [0, 0, 1, 1]
    assert zones["dispensa"].sources == [{"type": "manual"}]
    coffee = await inventory.async_find_product("caffè")
    assert coffee.essential
    assert coffee.min_stock == 1
    assert "Initial zone dispensa not created" in caplog.text
    assert "Import from todo.missing failed" in caplog.text

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    inventory = entry.runtime_data.inventory
    assert len(await inventory.async_list_zones()) == 3


async def test_options_language(hass: HomeAssistant) -> None:
    """The language option drives phrases sent without a language."""
    entry = MockConfigEntry(domain=DOMAIN, title="Casa", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_phrase(hass, None, "zone_fridge") == "Fridge"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general"}
    )
    assert result["step_id"] == "general"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"language": "it"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert await async_phrase(hass, None, "zone_fridge") == "Frigorifero"
    assert configured_language(hass) == "it"


async def test_single_instance(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A second entry is refused."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
