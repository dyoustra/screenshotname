"""Downscaling: the only place the `--resolution` tier means anything.

A vision model downscales whatever it is sent, so sending a 6016x3384 Retina
capture buys nothing but tokens. Doing the downscale here makes the tier an
explicit run parameter that the cost estimate and the cache key can both see
(AC-043, AC-076), and it is why the OCR pass has to run first, at native
resolution, before this throws the 11px UI text away (D-014).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from .settings import Resolution

#: Longest edge, in pixels, the image is reduced to per tier.
MAX_EDGE_PER_TIER: dict[Resolution, int] = {
    Resolution.STANDARD: 1_024,
    Resolution.HIGH: 1_568,
}


def encode_for_model(path: Path, resolution: Resolution) -> bytes:
    """`path` downscaled to `resolution`'s tier and re-encoded as PNG.

    PNG rather than JPEG because screenshots are flat-colour UI, where lossy
    encoding costs legibility of exactly the small text the model is being asked
    to read, and compresses worse besides.
    """
    edge = MAX_EDGE_PER_TIER[resolution]
    with Image.open(path) as image:
        image.load()
        downscaled = image.convert("RGB")
        downscaled.thumbnail((edge, edge))
        buffer = BytesIO()
        downscaled.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
