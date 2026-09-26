"""Filename assembly: date prefix, extension, and the 255-byte limit.

`slug.py` turned text into a slug; this module turns a slug into a real filename.
`NAME_PATTERN` is the output contract every proposed name satisfies (AC-010).
"""

from __future__ import annotations

import re
from datetime import date

#: AC-010's regular expression, verbatim.
NAME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*\.[a-z0-9]+$")

#: APFS and HFS+ cap a single path component at 255 *bytes*, not characters.
MAX_FILENAME_BYTES = 255


def parse_capture_date(name: str) -> date | None:
    """The capture date embedded in a screenshot filename, or None.

    Locale-independent: the `YYYY-MM-DD` run is what is parsed, not the leading
    word, so a German "Bildschirmfoto" name works (AC-011). U+202F between the
    seconds and the AM/PM marker is not a reason to fail.
    """
    raise NotImplementedError


def date_prefix(name: str, *, birthtime: float) -> str:
    """The `YYYY-MM-DD` prefix: the parsed capture date, else `birthtime` local."""
    raise NotImplementedError


def normalize_extension(name: str) -> str:
    """The original extension, lowercased, with its leading dot (AC-016)."""
    raise NotImplementedError


def fit_to_byte_limit(stem: str, extension: str) -> str:
    """Join `stem` and `extension` within `MAX_FILENAME_BYTES`.

    Truncation drops whole kebab segments from the end, so no segment is ever cut
    in half and the result never ends with a hyphen (AC-014).
    """
    raise NotImplementedError


def build_filename(
    *, date: str, slug: str, extension: str, suffix: str | None = None
) -> str:
    """Assemble `<date>-<slug>[-<suffix>]<extension>` within the byte limit."""
    raise NotImplementedError
