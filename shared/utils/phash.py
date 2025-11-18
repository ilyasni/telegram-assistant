"""Perceptual hash utilities (Context7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import io

import imagehash
from PIL import Image


@dataclass
class PhashResult:
    hash_hex: str
    size: int


def compute_phash(image_bytes: bytes, hash_size: int = 16) -> PhashResult:
    image = Image.open(io.BytesIO(image_bytes)).convert("L")
    phash = imagehash.phash(image, hash_size=hash_size)
    return PhashResult(hash_hex=str(phash), size=hash_size)


def hamming_distance(hash_a: str, hash_b: str) -> int:
    return imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b)

