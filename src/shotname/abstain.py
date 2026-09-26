"""Abstention: deciding that a capture has nothing to say.

Only three things trigger it — an empty-looking image, a model refusal, and a
schema failure that survived its retry. Low model confidence on its own never
does: a thin name is better than a half-renamed folder (AC-080).

The empty-image test is an AND of thin OCR text and low image entropy, so a
screenshot of a dense diagram with no text is not mistaken for a blank one
(AC-044).
"""

from __future__ import annotations

from pathlib import Path

from .plan import Reason
from .settings import RunSettings

#: Shannon entropy (bits per pixel) below which an image is "blank enough".
DEFAULT_MIN_ENTROPY = 1.5


def image_entropy(path: Path) -> float:
    """Shannon entropy of `path`'s greyscale histogram, in bits per pixel."""
    raise NotImplementedError


def abstain_reason(
    *, ocr_char_count: int, entropy: float, settings: RunSettings
) -> Reason | None:
    """`Reason.EMPTY_IMAGE` when both thresholds are under, otherwise None."""
    raise NotImplementedError
