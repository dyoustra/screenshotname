"""Content hashing: BLAKE2b over file bytes, truncated for filename suffixes."""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Hex characters of the content hash used as a collision suffix (AC-023).
SHORT_HASH_CHARS = 6

#: Read size. Fixed, so hashing a 40 MB Retina capture costs the same resident
#: memory as hashing a 4 KB one (AC-009).
CHUNK_BYTES = 1 << 20


def content_hash(path: Path) -> str:
    """The BLAKE2b hex digest of `path`'s contents, read in bounded chunks."""
    digest = hashlib.blake2b()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def short_hash(digest: str, *, length: int = SHORT_HASH_CHARS) -> str:
    """The first six hex characters of a content hash (AC-023).

    `length` exists only for the pathological case where six characters are not
    enough to separate two names; collision resolution lengthens the suffix
    rather than inventing a counter, so the result stays a function of the
    contents (AC-024).
    """
    return digest[:length]
