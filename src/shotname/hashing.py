"""Content hashing: BLAKE2b over file bytes, truncated for filename suffixes."""

from __future__ import annotations

from pathlib import Path

#: Hex characters of the content hash used as a collision suffix (AC-023).
SHORT_HASH_CHARS = 6


def content_hash(path: Path) -> str:
    """The BLAKE2b hex digest of `path`'s contents, read in bounded chunks."""
    raise NotImplementedError


def short_hash(digest: str) -> str:
    """The first six hex characters of a content hash (AC-023)."""
    raise NotImplementedError
