"""The dashboard contract: a sample snapshot kept in `tests/snapshot_sample.json`.

The dashboard repository type-checks this file against its `src/types.ts`
(see docs/dashboard-api.md). When the snapshot changes on purpose, write the
file again with `QM_WRITE_SAMPLE=1 .venv/bin/python -m pytest
tests/test_snapshot_contract.py` and update the dashboard.
"""

from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.zones import CheckOutcome, CheckStatus
from custom_components.quartermaster.storage.models import Channel
from custom_components.quartermaster.vision.schema import SeenItem, ZoneAnalysis

SAMPLE = Path(__file__).parent / "snapshot_sample.json"
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+(\+\d{2}:\d{2}|Z)?$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def _household(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Every kind of data the cards show, invented."""
    runtime = entry.runtime_data
    inventory = runtime.inventory
    await inventory.async_add_zone("Garage shelf", aliases=["garage"])
    await hass.services.async_call(
        DOMAIN,
        "set_zone",
        {
            "zone": "garage_shelf",
            "sources": [
                {"type": "camera", "entity_id": "camera.garage", "roi": [0, 0, 0.5, 1]},
                {
                    "type": "camera_show",
                    "entity_id": "camera.garage",
                    "trigger_entity": "binary_sensor.garage_motion",
                },
                {"type": "manual"},
            ],
            "check_schedule": "0 19 * * *",
        },
        blocking=True,
    )
    await inventory.async_add_zone("Fridge", aliases=["frigo"])
    await inventory.async_add_product(
        "Milk",
        aliases=["latte"],
        category="dairy",
        default_zone="fridge",
        unit="bottle",
        essential=True,
        min_stock=2,
        target_stock=4,
        shelf_life_days=7,
    )
    await inventory.async_add_product(
        "Passata", default_zone="garage_shelf", visual_hints="red jar"
    )
    await inventory.async_add_product("Yogurt", default_zone="fridge")
    await inventory.async_add_product("Old jam", default_zone="fridge")
    await inventory.async_set_stock(
        "milk",
        1,
        best_before=dt_util.now().date() + timedelta(days=2),
        channel=Channel.SERVICE,
    )
    await inventory.async_set_stock("passata", 4, channel=Channel.SERVICE)
    await inventory.async_set_stock("yogurt", 2, channel=Channel.DASHBOARD)
    await inventory.async_archive_product("old jam", channel=Channel.SERVICE)
    await runtime.shopping.async_add(text="Paper towels", quantity=2)

    garage = await inventory.async_find_zone("garage_shelf")
    fridge = await inventory.async_find_zone("fridge")
    assert runtime.zones is not None
    await runtime.zones._async_record(
        CheckOutcome(
            zone_id=fridge.id,
            status=CheckStatus.UPDATED,
            source="photo",
            overall_confidence=0.82,
        )
    )
    await runtime.zones.async_request_photo(garage, "stale", channel=Channel.SERVICE)
    # A count with a lower quantity, one product not seen and one unknown.
    await runtime.proposals.async_create(
        fridge,
        ZoneAnalysis(
            image_usable=True,
            overall_confidence=0.8,
            items=[
                SeenItem(product="yogurt", quantity=1, confidence=0.9),
                SeenItem(product="kefir", quantity=2, confidence=0.7, unit="bottle"),
            ],
            expected_not_seen=["milk"],
            unknown_items=["kefir"],
        ),
        channel=Channel.DASHBOARD,
        user_id=None,
        automatic=False,
        threshold=0.95,
    )
    assert runtime.purchases is not None
    await runtime.purchases._async_propose(
        ZoneAnalysis(
            image_usable=True,
            overall_confidence=0.9,
            items=[SeenItem(product="latte", quantity=2, confidence=0.95)],
        ),
        channel=Channel.TELEGRAM,
        user_id=None,
        source="receipt",
    )
    passata = await inventory.async_find_product("passata")
    await runtime.proposals.async_propose_movement(
        garage.id,
        passata,
        action="taken",
        quantity=1,
        current=4,
        confidence=0.9,
        seen_as="Passata",
        source="garage_shelf:camera_show/camera.garage/binary_sensor.garage_motion",
    )
    await runtime.coordinator.async_refresh()


def _stable(value: Any, entry_id: str) -> Any:
    """Replace the values that change on every run: ids, times and dates."""
    if isinstance(value, dict):
        return {
            key: "OPERATION_ID"
            if key == "operation_id" and item
            else _stable(item, entry_id)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_stable(item, entry_id) for item in value]
    if value == entry_id:
        return "ENTRY_ID"
    if isinstance(value, str) and TIMESTAMP.match(value):
        return "2026-11-10T09:00:00+00:00"
    if isinstance(value, str) and DATE.match(value):
        return "2026-11-12"
    return value


async def test_sample_snapshot(
    hass: HomeAssistant,
    hass_ws_client: Any,
    init_integration: MockConfigEntry,
) -> None:
    """The committed sample is what the integration sends today."""
    await _household(hass, init_integration)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/snapshot"})
    snapshot = (await client.receive_json())["result"]
    await client.send_json_auto_id({"type": f"{DOMAIN}/journal", "limit": 10})
    journal = (await client.receive_json())["result"]
    sample = _stable(
        {"snapshot": snapshot, "journal": journal}, init_integration.entry_id
    )
    text = json.dumps(sample, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("QM_WRITE_SAMPLE"):
        SAMPLE.write_text(text, encoding="utf-8")
    assert SAMPLE.read_text(encoding="utf-8") == text, (
        "The snapshot changed: write tests/snapshot_sample.json again "
        "(QM_WRITE_SAMPLE=1) and update the dashboard types"
    )
