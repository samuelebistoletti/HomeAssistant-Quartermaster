"""Image checks and preparation with Pillow (run in the executor).

Spec §5 and §7: crop to the zone's region of interest, refuse images too
small, too dark or blurred, fix the EXIF rotation, drop all metadata, resize
to 1280 px on the long side and encode as JPEG quality 80.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import io

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from custom_components.quartermaster.vision.phash import dhash

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
    except (UnidentifiedImageError, OSError, ValueError) as err:
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
