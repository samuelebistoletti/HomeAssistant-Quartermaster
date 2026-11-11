"""Image checks and preparation with Pillow (run in the executor).

Spec §5 and §7: crop to the zone's region of interest, refuse images too
small, too dark or blurred, fix the EXIF rotation, drop all metadata, resize
to 1280 px on the long side and encode as JPEG quality 80.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import io

from PIL import (
    Image,
    ImageChops,
    ImageFilter,
    ImageOps,
    ImageStat,
    UnidentifiedImageError,
)

from custom_components.quartermaster.vision.phash import dhash

# What Pillow raises for data it cannot or will not decode; an image with an
# absurd pixel count (a decompression bomb) is refused like a broken one.
_UNREADABLE = (
    UnidentifiedImageError,
    Image.DecompressionBombError,
    OSError,
    ValueError,
)

MIN_SHORT_SIDE = 640
LONG_SIDE = 1280
JPEG_QUALITY = 80
MIN_BRIGHTNESS = 40.0
MIN_SHARPNESS = 25.0
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
# Laplacian on a fixed size so sharpness compares across cameras.
_SHARPNESS_SIZE = 640
_LAPLACIAN = ImageFilter.Kernel(
    (3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128
)

Roi = tuple[float, float, float, float]


class ImageIssue(StrEnum):
    """Why an image cannot be analyzed."""

    UNREADABLE = "unreadable"
    TOO_SMALL = "too_small"
    TOO_DARK = "too_dark"
    BLURRED = "blurred"


class ImageRejectedError(Exception):
    """The image fails a quality check."""

    def __init__(self, issue: ImageIssue) -> None:
        """Store the issue."""
        super().__init__(issue.value)
        self.issue = issue


@dataclass(slots=True)
class PreparedImage:
    """An image ready to send to the model."""

    jpeg: bytes
    width: int
    height: int
    brightness: float
    sharpness: float
    perceptual_hash: str


def crop(image: Image.Image, roi: Roi | None) -> Image.Image:
    """Crop to a region given as x1, y1, x2, y2 fractions of the image."""
    if roi is None:
        return image
    x1, y1, x2, y2 = roi
    width, height = image.size
    box = (
        round(min(x1, x2) * width),
        round(min(y1, y2) * height),
        round(max(x1, x2) * width),
        round(max(y1, y2) * height),
    )
    return image.crop(box)


def brightness(gray: Image.Image) -> float:
    """Return the mean luminance (0-255)."""
    return float(ImageStat.Stat(gray).mean[0])


def sharpness(gray: Image.Image) -> float:
    """Return the variance of the Laplacian at a fixed size."""
    small = gray.copy()
    small.thumbnail((_SHARPNESS_SIZE, _SHARPNESS_SIZE))
    edges = small.filter(_LAPLACIAN)
    return float(ImageStat.Stat(edges).var[0])


def prepare(data: bytes, roi: Roi | None = None) -> PreparedImage:
    """Check and prepare an image; raise ImageRejectedError when unusable."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except _UNREADABLE as err:
        raise ImageRejectedError(ImageIssue.UNREADABLE) from err
    image = crop(image, roi)
    if min(image.size) < MIN_SHORT_SIDE:
        raise ImageRejectedError(ImageIssue.TOO_SMALL)
    gray = image.convert("L")
    light = brightness(gray)
    if light < MIN_BRIGHTNESS:
        raise ImageRejectedError(ImageIssue.TOO_DARK)
    sharp = sharpness(gray)
    if sharp < MIN_SHARPNESS:
        raise ImageRejectedError(ImageIssue.BLURRED)
    image.thumbnail((LONG_SIDE, LONG_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    # A fresh RGB image carries no EXIF, GPS or other metadata.
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return PreparedImage(
        jpeg=buffer.getvalue(),
        width=image.width,
        height=image.height,
        brightness=round(light, 1),
        sharpness=round(sharp, 1),
        perceptual_hash=dhash(image),
    )


DOCUMENT_LONG_SIDE = 1600
DOCUMENT_MIN_SIDE = 200


def prepare_document(data: bytes) -> bytes:
    """Prepare a receipt photo: readable, not dark, resized, no metadata.

    Receipts are long and narrow and their text is fine detail, so the zone
    checks on the short side and sharpness do not apply.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except _UNREADABLE as err:
        raise ImageRejectedError(ImageIssue.UNREADABLE) from err
    if min(image.size) < DOCUMENT_MIN_SIDE:
        raise ImageRejectedError(ImageIssue.TOO_SMALL)
    if brightness(image.convert("L")) < MIN_BRIGHTNESS:
        raise ImageRejectedError(ImageIssue.TOO_DARK)
    image.thumbnail((DOCUMENT_LONG_SIDE, DOCUMENT_LONG_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


@dataclass(slots=True)
class Frame:
    """A camera frame cut to the show area, with its scores."""

    index: int
    jpeg: bytes
    sharpness: float
    change: float
    perceptual_hash: str


_CHANGE_SIZE = (64, 48)


def change_score(gray: Image.Image, reference: Image.Image | None) -> float:
    """Return the mean absolute difference (0-255) from the reference scene."""
    if reference is None:
        return 0.0
    small = gray.resize(_CHANGE_SIZE)
    base = reference.resize(_CHANGE_SIZE)
    return float(ImageStat.Stat(ImageChops.difference(small, base)).mean[0])


def prepare_frame(
    data: bytes, roi: Roi | None, reference: bytes | None, index: int
) -> Frame:
    """Crop a frame to the show area and score it (no size checks)."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            image = crop(ImageOps.exif_transpose(source).convert("RGB"), roi)
        ref_gray = None
        if reference is not None:
            with Image.open(io.BytesIO(reference)) as ref:
                ref.load()
                ref_gray = crop(
                    ImageOps.exif_transpose(ref).convert("RGB"), roi
                ).convert("L")
    except _UNREADABLE as err:
        raise ImageRejectedError(ImageIssue.UNREADABLE) from err
    gray = image.convert("L")
    image.thumbnail((LONG_SIDE, LONG_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return Frame(
        index=index,
        jpeg=buffer.getvalue(),
        sharpness=round(sharpness(gray), 1),
        change=round(change_score(gray, ref_gray), 1),
        perceptual_hash=dhash(gray),
    )
