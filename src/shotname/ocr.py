"""Stage 1: on-device Apple Vision text recognition.

Per-line bounding boxes are the reason this is a structured result rather than a
blob of text: the app-first rule needs title-bar text — the topmost line — to
outrank body text when the application is identified (AC-019).

Boxes are in Vision's normalized coordinate space, origin bottom-left, as
`(x, y, width, height)`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class OcrLine:
    """One recognized line, with the confidence and box Vision reported for it."""

    text: str
    confidence: float
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class OcrResult:
    """Every line recognized in one image, in top-to-bottom reading order."""

    lines: tuple[OcrLine, ...]

    @property
    def text(self) -> str:
        """The lines joined by newlines: what the model prompt embeds."""
        raise NotImplementedError

    @property
    def char_count(self) -> int:
        """Total recognized characters, the quantity `--min-ocr-chars` bounds."""
        raise NotImplementedError


class Ocr(Protocol):
    """The recognition seam. One method, so a fake is trivial and total."""

    def recognize(self, path: Path) -> OcrResult: ...


class VisionOcr:
    """`ocrmac` over `VNRecognizeTextRequest` at `.accurate`, native resolution."""

    def __init__(self, *, languages: Sequence[str] | None = None) -> None:
        self.languages = tuple(languages) if languages is not None else ()

    def recognize(self, path: Path) -> OcrResult:
        raise NotImplementedError
