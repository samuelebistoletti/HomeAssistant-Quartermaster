"""Perceptual difference hash (dHash) with Pillow."""

from __future__ import annotations

from PIL import Image

HASH_SIZE = 8
# Images whose hashes differ by at most this many bits count as unchanged.
SAME_IMAGE_BITS = 4


def dhash(image: Image.Image) -> str:
    """Return the 64-bit dHash of an image as 16 hex digits."""
    small = image.convert("L").resize(
        (HASH_SIZE + 1, HASH_SIZE), Image.Resampling.LANCZOS
    )
    pixels = list(small.getdata())
    bits = 0
    for row in range(HASH_SIZE):
        for col in range(HASH_SIZE):
            left = pixels[row * (HASH_SIZE + 1) + col]
            right = pixels[row * (HASH_SIZE + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:016x}"


def distance(first: str, second: str) -> int:
    """Return the number of differing bits between two hashes."""
    return (int(first, 16) ^ int(second, 16)).bit_count()
