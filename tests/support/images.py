"""Image writers for the fixture corpus.

These are hand-rolled on top of `zlib` and `struct` so that building fixtures
needs no third-party image library. Only what the criteria actually require is
supported: 24-bit truecolour PNG (the format every real screenshot fixture
uses), and 24-bit BMP purely so AC-008 has an image whose format is outside the
supported set.
"""

from __future__ import annotations

import random
import struct
import zlib
from collections.abc import Sequence
from pathlib import Path

Pixel = tuple[int, int, int]

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def write_png(path: Path, rows: Sequence[Sequence[Pixel]]) -> Path:
    """Write `rows` as an 8-bit RGB PNG and return the path."""
    height = len(rows)
    width = len(rows[0])
    raw = b"".join(
        b"\x00" + bytes(channel for pixel in row for channel in pixel) for row in rows
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        PNG_MAGIC
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, 6))
        + _png_chunk(b"IEND", b"")
    )
    return path


def write_solid_png(
    path: Path,
    *,
    width: int = 24,
    height: int = 24,
    color: Pixel = (255, 255, 255),
) -> Path:
    """A single-colour image: the near-blank screenshot AC-044 abstains on."""
    row: list[Pixel] = [color] * width
    return write_png(path, [row] * height)


def write_noise_png(
    path: Path,
    *,
    width: int = 32,
    height: int = 32,
    seed: int = 0,
) -> Path:
    """A high-entropy image, standing in for a screenshot with content in it."""
    rng = random.Random(seed)
    rows = [
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for _ in range(width)
        ]
        for _ in range(height)
    ]
    return write_png(path, rows)


def write_bmp(
    path: Path,
    *,
    width: int = 8,
    height: int = 8,
    color: Pixel = (0, 0, 255),
) -> Path:
    """A 24-bit BMP: an image whose format is outside JPEG/PNG/GIF/WebP."""
    stride = width * 3
    padding = (-stride) % 4
    bgr = bytes((color[2], color[1], color[0]))
    pixels = b"".join(bgr * width + b"\x00" * padding for _ in range(height))
    offset = 54
    file_header = b"BM" + struct.pack("<IHHI", offset + len(pixels), 0, 0, offset)
    info_header = struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file_header + info_header + pixels)
    return path
