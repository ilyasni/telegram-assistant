"""Perceptual hash utilities (Context7)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

try:
    from PIL import Image
    import imagehash
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("imagehash and Pillow must be installed for phash utilities") from exc


@dataclass(frozen=True)
class PhashResult:
    hash_hex: str
    size: int


def compute_phash(image_bytes: bytes, hash_size: int = 16) -> PhashResult:
    """Calculate perceptual hash for изображение."""

    image = Image.open(io.BytesIO(image_bytes)).convert("L")
    phash = imagehash.phash(image, hash_size=hash_size)
    return PhashResult(hash_hex=str(phash), size=hash_size)


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Calculate Hamming distance between two hex hashes."""

    return imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b)


