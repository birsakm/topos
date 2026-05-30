"""Stub image-gen backend — returns a procedural noise PNG. For tests that
exercise the tool pipeline without spending real API tokens.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from .base import ImageGenResult


def _make_noise_png(size: int = 128) -> bytes:
    """Generate a tiny PNG manually (no PIL dep). Used by the stub backend."""
    import random
    rnd = random.Random(42)
    # PNG header
    sig = b"\x89PNG\r\n\x1a\n"
    # IHDR
    width, height = size, size
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    ihdr = _chunk(b"IHDR", ihdr_data)
    # IDAT — random RGB pixels
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter byte
        for x in range(width):
            raw.append(rnd.randrange(256))
            raw.append(rnd.randrange(256))
            raw.append(rnd.randrange(256))
    idat = _chunk(b"IDAT", zlib.compress(bytes(raw), level=6))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _chunk(typ: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    return length + typ + data + crc


@dataclass
class StubBackend:
    name: str = "stub"

    def generate(
        self,
        prompt: str,
        *,
        condition_image: Path | bytes | None = None,
        size: int = 1024,
        timeout_s: int | None = None,
    ) -> ImageGenResult:
        png = _make_noise_png(min(size, 256))
        return ImageGenResult(
            success=True,
            png_bytes=png,
            mime_type="image/png",
            duration_s=0.001,
            model="stub",
        )
