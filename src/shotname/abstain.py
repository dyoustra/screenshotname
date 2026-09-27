"""Abstention: deciding that a capture has nothing to say.

Only three things trigger it — an empty-looking image, a model refusal, and a
schema failure that survived its retry. Low model confidence on its own never
does: a thin name is better than a half-renamed folder (AC-080).

The empty-image test is an AND of thin OCR text and low image entropy, so a
screenshot of a dense diagram with no text is not mistaken for a blank one
(AC-044).
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from .plan import Reason
from .settings import RunSettings

#: Shannon entropy (bits per pixel) below which an image is "blank enough".
DEFAULT_MIN_ENTROPY = 1.5


def image_entropy(path: Path) -> float:
    """Shannon entropy of `path`'s greyscale histogram, in bits per pixel."""
    with Image.open(path) as image:
        histogram = image.convert("L").histogram()
    total = sum(histogram)
    if total == 0:
        return 0.0
    return -sum(
        (count / total) * math.log2(count / total) for count in histogram if count
    )


def abstain_reason(
    *, ocr_char_count: int, entropy: float, settings: RunSettings
) -> Reason | None:
    """`Reason.EMPTY_IMAGE` when both thresholds are under, otherwise None.

    An AND, not an OR: a dense diagram carries no text and a wall of terminal
    output carries no visual variety, and neither is an empty capture (AC-044).
    """
    if ocr_char_count < settings.min_ocr_chars and entropy < DEFAULT_MIN_ENTROPY:
        return Reason.EMPTY_IMAGE
    return None
