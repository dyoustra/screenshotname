"""Text to kebab-case slug: transliteration, sanitization, and the app-first rule.

Pure functions over strings. `compose_slug` is the seam the rest of the pipeline
uses: it takes the application the model identified and the subject it described
and returns the body of the filename, application token first (AC-019).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from anyascii import anyascii

#: The application alias map is data, not code, so it can be edited (AC-020).
ALIAS_MAP_PATH = Path(__file__).parent / "data" / "app_aliases.json"

#: App values that mean "no application was identified" (AC-022).
ABSENT_APP_VALUES = frozenset({"", "unknown", "none", "n/a"})

#: Unicode character-name prefixes whose scripts transliterate phonetically, so
#: a reader of the filename recovers roughly what the screen said. Everything
#: else non-ASCII is dropped instead: `anyascii` will happily render 日本語 as
#: "RiBenYu", which is noise in a filename rather than a transliteration, and
#: the same goes for emoji (🎉 -> ":tada:") and arrows (-> "<", ">") (AC-017).
_TRANSLITERABLE_SCRIPTS = ("LATIN", "GREEK", "CYRILLIC")

_SEGMENT = re.compile(r"[a-z0-9]+")


def _fold_char(char: str) -> str:
    if char.isascii():
        return char
    if unicodedata.name(char, "").startswith(_TRANSLITERABLE_SCRIPTS):
        folded = anyascii(char)
        if folded.isascii():
            return folded
    # A space, not "", so that dropping a character cannot fuse the words on
    # either side of it into one segment.
    return " "


def transliterate_ascii(text: str) -> str:
    """Fold `text` to ASCII, dropping anything with no transliteration (AC-017)."""
    return "".join(_fold_char(char) for char in unicodedata.normalize("NFC", text))


def slugify(text: str) -> str:
    """Lowercase kebab-case ASCII, with no leading or trailing separator.

    Every character outside `[a-z0-9]` becomes a segment boundary, which is what
    makes a colon or a slash impossible in the output (AC-012, AC-015).
    """
    return "-".join(_SEGMENT.findall(transliterate_ascii(text).lower()))


@lru_cache(maxsize=1)
def _alias_map() -> Mapping[str, str]:
    raw: dict[str, str] = json.loads(ALIAS_MAP_PATH.read_text(encoding="utf-8"))
    return MappingProxyType({slugify(name): token for name, token in raw.items()})


def load_alias_map() -> dict[str, str]:
    """Read `ALIAS_MAP_PATH`: normalized application name to canonical token."""
    return dict(_alias_map())


def canonical_app_token(app: str) -> str:
    """Canonicalize through the alias map, *then* slugify (AC-020, AC-021)."""
    token = slugify(app)
    return _alias_map().get(token, token)


def _app_token(app: str | None) -> str:
    """The leading segment for `app`, or "" when no application was identified."""
    if app is None or app.strip().lower() in ABSENT_APP_VALUES:
        return ""
    return canonical_app_token(app)


def compose_slug(*, app: str | None, subject: str) -> str:
    """The filename body: the app token first, then the subject's segments."""
    segments = [segment for segment in (_app_token(app), slugify(subject)) if segment]
    return "-".join(segments)
