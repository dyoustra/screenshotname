"""Filename assembly: date prefix, extension, and the 255-byte limit.

`slug.py` turned text into a slug; this module turns a slug into a real filename.
`NAME_PATTERN` is the output contract every proposed name satisfies (AC-010).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import PurePath

#: AC-010's regular expression, verbatim.
NAME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*\.[a-z0-9]+$")

#: APFS and HFS+ cap a single path component at 255 *bytes*, not characters.
MAX_FILENAME_BYTES = 255

#: The `YYYY-MM-DD` run inside a capture filename. Anchored on nothing, because
#: the leading word is localized ("Screenshot", "Bildschirmfoto", or whatever the
#: user set with `defaults write`) and is therefore not a signal (AC-011).
_CAPTURE_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_capture_date(name: str) -> date | None:
    """The capture date embedded in a screenshot filename, or None.

    Locale-independent: the `YYYY-MM-DD` run is what is parsed, not the leading
    word, so a German "Bildschirmfoto" name works (AC-011). U+202F between the
    seconds and the AM/PM marker is not a reason to fail.
    """
    match = _CAPTURE_DATE.search(name)
    if match is None:
        return None
    year, month, day = (int(group) for group in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        # A well-shaped run that names no real day, such as 2026-13-45.
        return None


def date_prefix(name: str, *, birthtime: float) -> str:
    """The `YYYY-MM-DD` prefix: the parsed capture date, else `birthtime` local."""
    parsed = parse_capture_date(name)
    if parsed is not None:
        return parsed.isoformat()
    # Local time, not UTC: AC-011 asks for the date the human took the shot.
    return datetime.fromtimestamp(birthtime).date().isoformat()  # noqa: DTZ006


def normalize_extension(name: str) -> str:
    """The original extension, lowercased, with its leading dot (AC-016)."""
    return PurePath(name).suffix.lower()


def fit_to_byte_limit(stem: str, extension: str) -> str:
    """Join `stem` and `extension` within `MAX_FILENAME_BYTES`.

    Truncation drops whole kebab segments from the end, so no segment is ever cut
    in half and the result never ends with a hyphen (AC-014).
    """
    budget = MAX_FILENAME_BYTES - len(extension.encode("utf-8"))
    if len(stem.encode("utf-8")) <= budget:
        return stem + extension

    segments = stem.split("-")
    kept: list[str] = []
    length = 0
    for segment in segments:
        # The separator costs a byte for every segment after the first.
        cost = len(segment.encode("utf-8")) + (1 if kept else 0)
        if length + cost > budget:
            break
        kept.append(segment)
        length += cost
    if kept:
        return "-".join(kept) + extension

    # Degenerate: the first segment alone overruns the budget, so there is no
    # boundary left to cut at and a byte-wise cut is all that remains. Reachable
    # only from an extension long enough to consume most of the 255 bytes.
    head = segments[0].encode("utf-8")[: max(budget, 0)].decode("utf-8", "ignore")
    return head.rstrip("-") + extension


def build_filename(
    *, date: str, slug: str, extension: str, suffix: str | None = None
) -> str:
    """Assemble `<date>-<slug>[-<suffix>]<extension>` within the byte limit."""
    # The disambiguation suffix rides along with the extension as a fixed tail,
    # so shortening can never be what drops it and makes two names collide
    # (AC-023). Only subject segments are ever given up.
    tail = extension.lower() if suffix is None else f"-{suffix}{extension.lower()}"
    stem = "-".join(part for part in (date, slug) if part)
    return fit_to_byte_limit(stem, tail)
