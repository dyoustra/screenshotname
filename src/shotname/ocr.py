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
        return "\n".join(line.text for line in self.lines)

    @property
    def char_count(self) -> int:
        """Total recognized characters, the quantity `--min-ocr-chars` bounds."""
        return sum(len(line.text) for line in self.lines)


class Ocr(Protocol):
    """The recognition seam. One method, so a fake is trivial and total."""

    def recognize(self, path: Path) -> OcrResult: ...


class VisionOcr:
    """`ocrmac` over `VNRecognizeTextRequest` at `.accurate`, native resolution."""

    def __init__(self, *, languages: Sequence[str] | None = None) -> None:
        self.languages = tuple(languages) if languages is not None else ()

    def recognize(self, path: Path) -> OcrResult:
        """Recognize `path` at its native resolution, top line first.

        `ocrmac` is imported here rather than at module scope so that importing
        `shotname` does not pull in pyobjc and the Vision framework on a machine
        that is only ever going to run `--local` or the test suite.
        """
        from ocrmac import ocrmac

        annotations = ocrmac.OCR(
            str(path),
            recognition_level="accurate",
            language_preference=list(self.languages) or None,
        ).recognize()
        # Vision's origin is bottom-left, so the topmost line is the one with the
        # largest y. The app-first rule depends on that ordering (AC-019).
        ordered = sorted(annotations, key=lambda item: -float(item[2][1]))
        return OcrResult(
            lines=tuple(
                OcrLine(
                    text=str(text),
                    confidence=float(confidence),
                    bbox=(
                        float(bbox[0]),
                        float(bbox[1]),
                        float(bbox[2]),
                        float(bbox[3]),
                    ),
                )
                for text, confidence, bbox in ordered
            )
        )
