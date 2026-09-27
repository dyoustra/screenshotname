"""The content-hash-keyed result cache: SQLite in WAL mode.

Two key functions, and the difference between them is the point of AC-043. OCR
output depends only on the image bytes, so it survives a change of model, tier, or
prompt. A model suggestion depends on all four, so changing any of them
invalidates it while the cached OCR for the same file is still reused.

This is also what makes a crash resume nearly free: "already done" means "already
cached", not "already renamed" (AC-042).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .model import NameSuggestion
from .ocr import OcrLine, OcrResult
from .settings import Resolution, state_dir_path

CACHE_FILENAME = "cache.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ocr (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS suggestion (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
"""


def cache_path(state_dir: Path | None = None) -> Path:
    """The cache database's location under the durable state directory."""
    return state_dir_path(state_dir) / CACHE_FILENAME


def ocr_cache_key(content_hash: str) -> str:
    """OCR depends on the image bytes and nothing else (AC-043)."""
    return f"ocr:{content_hash}"


def model_cache_key(
    *,
    content_hash: str,
    model: str,
    resolution: Resolution,
    prompt_template_version: str,
) -> str:
    """A suggestion depends on all four inputs, so all four are in the key."""
    return f"suggestion:{content_hash}:{model}:{resolution.value}:{prompt_template_version}"


def _ocr_payload(result: OcrResult) -> str:
    return json.dumps(
        [
            {"text": line.text, "confidence": line.confidence, "bbox": list(line.bbox)}
            for line in result.lines
        ]
    )


def _ocr_from_payload(payload: str) -> OcrResult:
    raw = json.loads(payload)
    return OcrResult(
        lines=tuple(
            OcrLine(
                text=line["text"],
                confidence=float(line["confidence"]),
                bbox=(
                    float(line["bbox"][0]),
                    float(line["bbox"][1]),
                    float(line["bbox"][2]),
                    float(line["bbox"][3]),
                ),
            )
            for line in raw
        )
    )


class Cache:
    """SQLite-backed store for OCR results and model suggestions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        # WAL so a worker pool can read while another writes (D-018).
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def _fetch(self, statement: str, key: str) -> str | None:
        row = self._connection.execute(statement, (key,)).fetchone()
        return None if row is None else str(row[0])

    def _store(self, statement: str, key: str, payload: str) -> None:
        self._connection.execute(statement, (key, payload))
        self._connection.commit()

    def get_ocr(self, content_hash: str) -> OcrResult | None:
        payload = self._fetch(
            "SELECT payload FROM ocr WHERE key = ?", ocr_cache_key(content_hash)
        )
        return None if payload is None else _ocr_from_payload(payload)

    def put_ocr(self, content_hash: str, result: OcrResult) -> None:
        self._store(
            "INSERT OR REPLACE INTO ocr (key, payload) VALUES (?, ?)",
            ocr_cache_key(content_hash),
            _ocr_payload(result),
        )

    def get_suggestion(self, key: str) -> NameSuggestion | None:
        payload = self._fetch("SELECT payload FROM suggestion WHERE key = ?", key)
        if payload is None:
            return None
        raw = json.loads(payload)
        return NameSuggestion(
            app=raw["app"], subject=raw["subject"], confidence=float(raw["confidence"])
        )

    def put_suggestion(self, key: str, suggestion: NameSuggestion) -> None:
        self._store(
            "INSERT OR REPLACE INTO suggestion (key, payload) VALUES (?, ?)",
            key,
            suggestion.to_json_text(),
        )

    def close(self) -> None:
        self._connection.close()
