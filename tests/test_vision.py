"""Tests for image checks, the result schema, analyses and proposals."""

from __future__ import annotations

import io
import json
from pathlib import Path
import random
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import llm
from PIL import Image
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.quartermaster.const import DOMAIN
from custom_components.quartermaster.core.inventory import InventoryService
from custom_components.quartermaster.storage.models import Channel
from custom_components.quartermaster.vision.phash import dhash, distance
from custom_components.quartermaster.vision.quality import (
    ImageIssue,
    ImageRejectedError,
    crop,
    prepare,
)
from custom_components.quartermaster.vision.schema import (
    InvalidResultError,
    StructureMode,
    parse_result,
    structure_for,
)


def _photo(
    size: tuple[int, int] = (1600, 1200),
    *,
    seed: int = 1,
    level: int = 255,
    flat: bool = False,
    exif_rotate: bool = False,
) -> bytes:
    """Return a JPEG of noise over shapes (sharp), or a flat color (blurred).

    `seed` moves the shapes, so different seeds give different hashes.
    """
    if flat:
        image = Image.new("RGB", size, (level // 2,) * 3)
    else:
        rng = random.Random(seed)
        base = Image.new("L", size, 90)
        for _ in range(12):
            x, y = rng.randrange(size[0]), rng.randrange(size[1])
            base.paste(rng.randrange(256), (x, y, x + size[0] // 4, y + size[1] // 4))
        noise = Image.effect_noise(size, 40)
        mixed = Image.blend(base, noise, 0.35).point(lambda v: v * level // 255)
        image = Image.merge("RGB", (mixed, mixed, mixed))
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "TestCam"  # Make
    if exif_rotate:
        exif[0x0112] = 6  # rotate 90 degrees on display
    image.save(buffer, format="JPEG", quality=90, exif=exif)
    return buffer.getvalue()


# Image checks


def test_prepare_resizes_and_strips_metadata() -> None:
    """A good photo is resized to 1280 px, re-encoded and loses its EXIF."""
    prepared = prepare(_photo(exif_rotate=True))
    # Rotated by the EXIF tag: portrait after preparation.
    assert (prepared.width, prepared.height) == (960, 1280)
    with Image.open(io.BytesIO(prepared.jpeg)) as image:
        assert image.format == "JPEG"
        assert not image.getexif()
    assert prepared.brightness > 40
    assert prepared.sharpness > 25
    assert len(prepared.perceptual_hash) == 16


@pytest.mark.parametrize(
    ("data", "roi", "issue"),
    [
        (b"not an image", None, ImageIssue.UNREADABLE),
        (_photo((800, 500)), None, ImageIssue.TOO_SMALL),
        (_photo(), (0.0, 0.0, 0.3, 0.3), ImageIssue.TOO_SMALL),
        (_photo(level=40), None, ImageIssue.TOO_DARK),
        (_photo(flat=True), None, ImageIssue.BLURRED),
    ],
)
def test_prepare_rejects(data: bytes, roi: Any, issue: ImageIssue) -> None:
    """Unreadable, small, dark and blurred images are refused."""
    with pytest.raises(ImageRejectedError) as err:
        prepare(data, roi)
    assert err.value.issue is issue


def test_crop_and_hash() -> None:
    """The region is cut from fractions; similar images hash close."""
    image = Image.new("RGB", (1000, 500))
    assert crop(image, (0.5, 0.0, 1.0, 1.0)).size == (500, 500)
    assert crop(image, None) is image
    # Left-to-right gradient: dHash compares horizontal neighbours.
    gradient = (
        Image.linear_gradient("L")
        .transpose(Image.Transpose.ROTATE_90)
        .resize((640, 480))
        .convert("RGB")
    )
    same = dhash(gradient)
    brighter = dhash(gradient.point(lambda v: min(v + 10, 255)))
    flipped = dhash(gradient.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    assert distance(same, brighter) <= 4
    assert distance(same, flipped) > 4


# Schema


def test_structures() -> None:
    """Nested mode asks for an items list; text mode for JSON in a field."""
    nested = structure_for(StructureMode.NESTED)
    assert nested["items"]["selector"]["object"]["multiple"] is True
    assert "items_json" in structure_for(StructureMode.TEXT)


def test_parse_result_validates() -> None:
    """Bad items are dropped, scores clamped, unknown ids ignored."""
    analysis = parse_result(
        {
            "image_usable": True,
            "overall_confidence": 1.7,
            "items": [
                {
                    "product": " Milk ",
                    "quantity": "2",
                    "confidence": 0.9,
                    "matched_product_id": 1,
                },
                {
                    "product": "Jam",
                    "quantity": 1,
                    "confidence": -1,
                    "matched_product_id": 99,
                },
                {"product": "", "quantity": 1},
                {"product": "Bad", "quantity": "lots"},
                {"product": "Huge", "quantity": 10**9},
                "nonsense",
            ],
            "image_issues": "glare",
            "expected_not_seen": ["Butter", " "],
        },
        expected_ids={1},
    )
    assert analysis.overall_confidence == 1.0
    assert [
        (i.product, i.quantity, i.confidence, i.matched_product_id)
        for i in analysis.items
    ] == [
        ("Milk", 2.0, 0.9, 1),
        ("Jam", 1.0, 0.0, None),
    ]
    assert analysis.image_issues == ["glare"]
    assert analysis.expected_not_seen == ["Butter"]


def test_parse_result_text_mode_and_errors() -> None:
    """The text fallback is parsed; malformed answers raise."""
    analysis = parse_result(
        {
            "image_usable": True,
            "overall_confidence": 0.8,
            "items_json": json.dumps(
                [{"product": "Rice", "quantity": 3, "confidence": 0.8}]
            ),
        },
        expected_ids=set(),
    )
    assert analysis.items[0].product == "Rice"
    for data in (None, {}, {"items_json": "not json"}, {"items": "x"}):
        with pytest.raises(InvalidResultError):
            parse_result(data, set())


# Analyses through a simulated AI Task


class FakeAITask:
    """Records calls to ai_task.generate_data and answers a fixed result."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.calls: list[dict[str, Any]] = []
        self.files_seen: list[bool] = []
        self.answer: dict[str, Any] = {}
        hass.services.async_register(
            "ai_task",
            "generate_data",
            self._handle,
            supports_response=SupportsResponse.ONLY,
        )

    async def _handle(self, call: ServiceCall) -> dict[str, Any]:
        self.calls.append(dict(call.data))
        media_id = call.data["attachments"][0]["media_content_id"]
        relative = media_id.removeprefix("media-source://media_source/local/")
        path = Path(self.hass.config.media_dirs["local"]) / relative
        self.files_seen.append(path.exists())
        return {"conversation_id": "x", "data": self.answer}


@pytest.fixture
async def setup(
    hass: HomeAssistant, init_integration: MockConfigEntry, tmp_path: Path
) -> tuple[MockConfigEntry, FakeAITask]:
    """Loaded entry with an AI Task entity chosen, a fridge and two products."""
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    fake = FakeAITask(hass)
    hass.config_entries.async_update_entry(
        init_integration, options={"ai_task_image": "ai_task.vision"}
    )
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    inventory: InventoryService = init_integration.runtime_data.inventory
    await inventory.async_add_zone("Fridge", aliases=["frigo"])
    milk = await inventory.async_add_product(
        "Milk", default_zone="fridge", unit="bottle"
    )
    yogurt = await inventory.async_add_product("Yogurt", default_zone="fridge")
    await inventory.async_set_stock("milk", 1, channel=Channel.SERVICE)
    fake.answer = {
        "image_usable": True,
        "overall_confidence": 0.9,
        "items": [
            {
                "product": "Milk",
                "matched_product_id": milk.id,
                "quantity": 3,
                "confidence": 0.9,
            },
            {
                "product": "Yoghurt",
                "matched_product_id": yogurt.id,
                "quantity": 2,
                "confidence": 0.6,
            },
            {"product": "Kefir", "quantity": 1, "confidence": 0.8},
        ],
        "expected_not_seen": [],
        "unknown_items": ["jar"],
    }
    return init_integration, fake


async def _analyze(hass: HomeAssistant, photo: Path, **data: Any) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN,
        "analyze_image",
        {"zone": "frigo", "file": str(photo), **data},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    return dict(response)


@pytest.fixture
def photo(hass: HomeAssistant, tmp_path: Path) -> Path:
    """A photo in a folder Home Assistant may read."""
    path = tmp_path / "photos" / "fridge.jpg"
    path.parent.mkdir()
    path.write_bytes(_photo())
    hass.config.allowlist_external_dirs = {str(path.parent)}
    return path


async def test_confirm_mode_creates_a_proposal(
    hass: HomeAssistant, setup: tuple[MockConfigEntry, FakeAITask], photo: Path
) -> None:
    """In confirm mode nothing is written; the proposal lists the changes."""
    entry, fake = setup
    inventory: InventoryService = entry.runtime_data.inventory
    result = await _analyze(hass, photo)
    assert result["applied"] == []
    assert result["unknown"] == ["jar"]
    proposed = {row["seen_as"]: row for row in result["proposed"]}
    assert (proposed["Milk"]["current"], proposed["Milk"]["proposed"]) == (1, 3)
    assert proposed["Kefir"]["product_id"] is None
    assert (await inventory.async_get_stock(zone_id="fridge"))[0].quantity == 1

    [call] = fake.calls
    assert call["entity_id"] == "ai_task.vision"
    assert "Milk" in call["instructions"]
    assert fake.files_seen == [True]
    assert not any((Path(hass.config.media_dirs["local"]) / DOMAIN / "tmp").iterdir())

    state = hass.states.get("sensor.quartermaster_pending_proposals")
    assert state is not None
    assert state.state == "1"
    analyses = hass.states.get("sensor.quartermaster_analyses_today")
    assert analyses is not None
    assert (analyses.state, analyses.attributes["limit"]) == ("1", 50)


async def test_confirm_with_corrections_learns_aliases(
    hass: HomeAssistant, setup: tuple[MockConfigEntry, FakeAITask], photo: Path
) -> None:
    """Confirming applies chosen items as one operation; corrections teach aliases."""
    entry, _ = setup
    inventory: InventoryService = entry.runtime_data.inventory
    result = await _analyze(hass, photo)
    keys = {row["seen_as"]: row["key"] for row in result["proposed"]}
    await inventory.async_add_product("Kefir drink", default_zone="fridge")
    response = await hass.services.async_call(
        DOMAIN,
        "confirm_pending",
        {
            "proposal_id": result["proposal_id"],
            "items": [keys["Milk"], keys["Kefir"]],
            "corrections": [
                {"key": keys["Kefir"], "product": "Kefir drink", "quantity": 2}
            ],
        },
        blocking=True,
        return_response=True,
    )
    assert len(response["applied"]) == 2  # type: ignore[index, arg-type]
    levels = {
        (await inventory.async_find_product(p)).name: q
        for p, q in (("milk", 3), ("kefir drink", 2))
    }
    for name, quantity in levels.items():
        product = await inventory.async_find_product(name)
        [level] = await inventory.async_get_stock(product_id=product.id)
        assert level.quantity == quantity
    assert "Kefir" in (await inventory.async_find_product("kefir drink")).aliases
    originals, _ = await inventory.async_undo_last(channel=Channel.SERVICE)
    assert len(originals) == 2
    assert hass.states.get("sensor.quartermaster_pending_proposals").state == "0"  # type: ignore[union-attr]

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "confirm_pending",
            {"proposal_id": result["proposal_id"]},
            blocking=True,
        )
    assert err.value.translation_key == "proposal_not_found"


async def test_reject_and_unchanged_image(
    hass: HomeAssistant, setup: tuple[MockConfigEntry, FakeAITask], photo: Path
) -> None:
    """Rejecting writes nothing; the same image again reuses the last result."""
    _entry, fake = setup
    result = await _analyze(hass, photo)
    await hass.services.async_call(
        DOMAIN,
        "confirm_pending",
        {"proposal_id": result["proposal_id"], "decision": "reject"},
        blocking=True,
    )
    again = await _analyze(hass, photo)
    assert again["reused_previous"] is True
    assert len(fake.calls) == 1
    assert again["proposal_id"] != result["proposal_id"]


async def test_automatic_mode(
    hass: HomeAssistant, setup: tuple[MockConfigEntry, FakeAITask], photo: Path
) -> None:
    """Above the threshold changes are applied; the rest is proposed."""
    entry, _fake = setup
    inventory: InventoryService = entry.runtime_data.inventory
    # Milk 2 -> 3 is not a large change; only Yoghurt (0 -> 2) is.
    await inventory.async_set_stock("milk", 2, channel=Channel.SERVICE)
    await hass.services.async_call(
        "select",
        "select_option",
        {"option": "automatic"},
        target={"entity_id": "select.quartermaster_update_mode"},
        blocking=True,
    )
    result = await _analyze(hass, photo)
    assert [row["seen_as"] for row in result["applied"]] == ["Milk"]
    assert {row["seen_as"] for row in result["proposed"]} == {"Yoghurt", "Kefir"}
    milk = await inventory.async_find_product("milk")
    assert (await inventory.async_get_stock(product_id=milk.id))[0].quantity == 3


async def test_automatic_mode_holds_large_multi_product_changes(
    hass: HomeAssistant, setup: tuple[MockConfigEntry, FakeAITask], photo: Path
) -> None:
    """Several products changing by more than half are always proposed."""
    _entry, fake = setup
    for item in fake.answer["items"]:
        item["confidence"] = 0.95
    await hass.services.async_call(
        "select",
        "select_option",
        {"option": "automatic"},
        target={"entity_id": "select.quartermaster_update_mode"},
        blocking=True,
    )
    result = await _analyze(hass, photo)
    assert result["applied"] == []
    assert len(result["proposed"]) == 3


async def test_analysis_errors(
    hass: HomeAssistant,
    setup: tuple[MockConfigEntry, FakeAITask],
    photo: Path,
    tmp_path: Path,
) -> None:
    """Bad images, paths, limits and model failures give clear errors."""
    entry, fake = setup
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(_photo())
    blurred = photo.parent / "blurred.jpg"
    blurred.write_bytes(_photo(flat=True))
    cases = [
        ({"file": str(outside)}, "path_not_allowed"),
        ({"file": str(photo.parent / "missing.jpg")}, "import_unreadable"),
        ({"file": str(blurred)}, "image_rejected"),
    ]
    for data, key in cases:
        with pytest.raises(ServiceValidationError) as err:
            await _analyze(hass, photo, **data)
        assert err.value.translation_key == key

    fake.answer = {"no": "items"}
    with pytest.raises(ServiceValidationError) as err:
        await _analyze(hass, photo)
    assert err.value.translation_key == "analysis_failed"

    entry.runtime_data.vision.daily_limit = 1
    other = photo.parent / "other.jpg"
    other.write_bytes(_photo(seed=9))
    with pytest.raises(ServiceValidationError) as err:
        await _analyze(hass, other)
    assert err.value.translation_key == "daily_limit"

    entry.runtime_data.vision.ai_task_entity = None
    with pytest.raises(ServiceValidationError) as err:
        await _analyze(hass, other)
    assert err.value.translation_key == "ai_task_missing"


async def test_model_call_failure_and_media_dir(
    hass: HomeAssistant, setup: tuple[MockConfigEntry, FakeAITask], photo: Path
) -> None:
    """A failing AI Task call or a missing media folder is reported."""
    _entry, _fake = setup

    async def broken(call: ServiceCall) -> dict[str, Any]:
        raise ServiceValidationError("entity not found")

    hass.services.async_register(
        "ai_task", "generate_data", broken, supports_response=SupportsResponse.ONLY
    )
    with pytest.raises(ServiceValidationError) as err:
        await _analyze(hass, photo)
    assert err.value.translation_key == "analysis_failed"

    hass.config.media_dirs = {}
    other = photo.parent / "other.jpg"
    other.write_bytes(_photo(seed=8))
    with pytest.raises(ServiceValidationError) as err:
        await _analyze(hass, other)
    assert err.value.translation_key == "media_dir_missing"


async def test_analyze_camera_frame(
    hass: HomeAssistant, setup: tuple[MockConfigEntry, FakeAITask]
) -> None:
    """A camera frame is taken with camera.snapshot and deleted after reading."""
    written: list[Path] = []

    async def snapshot(call: ServiceCall) -> None:
        path = Path(call.data["filename"])
        written.append(path)
        path.write_bytes(_photo(seed=5))

    hass.services.async_register("camera", "snapshot", snapshot)
    result = await hass.services.async_call(
        DOMAIN,
        "analyze_image",
        {"zone": "fridge", "camera_entity_id": "camera.fridge"},
        blocking=True,
        return_response=True,
    )
    assert result["proposal_id"] is not None  # type: ignore[index]
    assert written and not written[0].exists()

    async def failing(call: ServiceCall) -> None:
        raise ServiceValidationError("unavailable")

    hass.services.async_register("camera", "snapshot", failing)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "analyze_image",
            {"zone": "fridge", "camera_entity_id": "camera.fridge"},
            blocking=True,
        )
    assert err.value.translation_key == "camera_unavailable"

    hass.config.media_dirs = {}
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "analyze_image",
            {"zone": "fridge", "camera_entity_id": "camera.fridge"},
            blocking=True,
        )
    assert err.value.translation_key == "media_dir_missing"


async def test_confirm_tool(
    hass: HomeAssistant, setup: tuple[MockConfigEntry, FakeAITask], photo: Path
) -> None:
    """The LLM tool lists, confirms and rejects proposals."""
    from homeassistant.components import conversation
    from homeassistant.core import Context

    await _analyze(hass, photo)
    api = await llm.async_get_api(
        hass,
        llm.LLM_API_ASSIST,
        llm.LLMContext("test", Context(), "en", conversation.DOMAIN, None),
    )

    async def call(**args: Any) -> dict[str, Any]:
        result = await api.async_call_tool(
            llm.ToolInput(tool_name=f"{DOMAIN}__confirm_pending", tool_args=args)
        )
        assert isinstance(result, llm.ToolResult)
        return dict(result.data)

    [pending] = (await call())["pending"]
    milk = next(item for item in pending["items"] if item["seen_as"] == "Milk")
    confirmed = await call(proposal_id=pending["proposal_id"], items=[milk["key"]])
    assert confirmed["status"] == "confirmed"
    assert (await call())["pending"] == []

    other = photo.parent / "other.jpg"
    other.write_bytes(_photo(seed=11))
    await _analyze(hass, other)
    [pending] = (await call())["pending"]
    assert (await call(proposal_id=pending["proposal_id"], decision="reject")) == {
        "status": "rejected"
    }
