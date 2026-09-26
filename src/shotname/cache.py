"""The content-hash-keyed result cache: SQLite in WAL mode.

Two key functions, and the difference between them is the point of AC-043. OCR
output depends only on the image bytes, so it survives a change of model, tier, or
prompt. A model suggestion depends on all four, so changing any of them
invalidates it while the cached OCR for the same file is still reused.

This is also what makes a crash resume nearly free: "already done" means "already
cached", not "already renamed" (AC-042).
"""

from __future__ import annotations

from pathlib import Path

from .model import NameSuggestion
from .ocr import OcrResult
from .settings import Resolution

CACHE_FILENAME = "cache.sqlite"


def cache_path(state_dir: Path | None = None) -> Path:
    """The cache database's location under the durable state directory."""
    raise NotImplementedError


def ocr_cache_key(content_hash: str) -> str:
    """OCR depends on the image bytes and nothing else (AC-043)."""
    raise NotImplementedError


def model_cache_key(
    *,
    content_hash: str,
    model: str,
    resolution: Resolution,
    prompt_template_version: str,
) -> str:
    """A suggestion depends on all four inputs, so all four are in the key."""
    raise NotImplementedError


class Cache:
    """SQLite-backed store for OCR results and model suggestions."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def get_ocr(self, content_hash: str) -> OcrResult | None:
        raise NotImplementedError

    def put_ocr(self, content_hash: str, result: OcrResult) -> None:
        raise NotImplementedError

    def get_suggestion(self, key: str) -> NameSuggestion | None:
        raise NotImplementedError

    def put_suggestion(self, key: str, suggestion: NameSuggestion) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
