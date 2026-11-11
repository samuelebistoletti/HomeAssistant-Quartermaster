"""Tests for small rules: validation, translated errors and odd model output."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.quartermaster.core.events import is_fresh_event
from custom_components.quartermaster.core.inventory import (
    InventoryError,
    InventoryService,
)
from custom_components.quartermaster.core.phrases import async_error_text
from custom_components.quartermaster.core.transfer import (
    async_export_json,
    async_import_json,
)
from custom_components.quartermaster.storage.models import Channel
from custom_components.quartermaster.vision.schema import result_from_dict


def test_fresh_event() -> None:
    """Only a state that is the time of an event just fired is fresh."""
    assert is_fresh_event(dt_util.utcnow().isoformat())
    assert not is_fresh_event("2026-01-01T10:00:00+00:00")
    assert not is_fresh_event("unknown")


async def test_quiet_hours_are_validated(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A quiet-hours text that is no time window is refused with an error."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "notifications"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"quiet_hours": "22-7pm", "telegram_notify": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"quiet_hours": "invalid_quiet_hours"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"quiet_hours": "22:00-07:00", "telegram_notify": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert init_integration.options["quiet_hours"] == "22:00-07:00"


async def test_error_text_in_the_chat_language(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A translated error is rendered in the asked language, English if missing."""
    err = InventoryError("product_not_found", product="Tofu")
    italian = await async_error_text(hass, "it", err)
    english = await async_error_text(hass, "en", err)
    assert "Tofu" in italian
    assert "Tofu" in english
    assert italian != english
    assert await async_error_text(hass, "xx", err) == english


def test_model_flag_as_text() -> None:
    """Models that answer "false" as text do not make the image usable."""
    base = {"overall_confidence": 0.9, "items": []}
    assert result_from_dict({**base, "image_usable": "false"}).image_usable is False
    assert result_from_dict({**base, "image_usable": "true"}).image_usable is True
    assert result_from_dict({**base, "image_usable": False}).image_usable is False


async def test_json_import_refuses_bad_dates(inventory: InventoryService) -> None:
    """A date the store could not read back is refused before anything is written."""
    await inventory.async_add_zone("Fridge")
    await inventory.async_add_product("Milk", default_zone="fridge")
    await inventory.async_set_stock("milk", 2, channel=Channel.SERVICE)
    document = json.loads(await async_export_json(inventory))
    document["tables"]["stock"][0]["best_before"] = "next week"
    with pytest.raises(InventoryError) as err:
        await async_import_json(inventory, json.dumps(document))
    assert err.value.translation_key == "invalid_import"
    [level] = await inventory.async_get_stock()
    assert level.quantity == 2
