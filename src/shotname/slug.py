"""Text to kebab-case slug: transliteration, sanitization, and the app-first rule.

Pure functions over strings. `compose_slug` is the seam the rest of the pipeline
uses: it takes the application the model identified and the subject it described
and returns the body of the filename, application token first (AC-019).
"""

from __future__ import annotations

from pathlib import Path

#: The application alias map is data, not code, so it can be edited (AC-020).
ALIAS_MAP_PATH = Path(__file__).parent / "data" / "app_aliases.json"

#: App values that mean "no application was identified" (AC-022).
ABSENT_APP_VALUES = frozenset({"", "unknown", "none", "n/a"})


def transliterate_ascii(text: str) -> str:
    """Fold `text` to ASCII, dropping anything with no transliteration (AC-017)."""
    raise NotImplementedError


def slugify(text: str) -> str:
    """Lowercase kebab-case ASCII, with no leading or trailing separator.

    Every character outside `[a-z0-9]` becomes a segment boundary, which is what
    makes a colon or a slash impossible in the output (AC-012, AC-015).
    """
    raise NotImplementedError


def load_alias_map() -> dict[str, str]:
    """Read `ALIAS_MAP_PATH`: normalized application name to canonical token."""
    raise NotImplementedError


def canonical_app_token(app: str) -> str:
    """Canonicalize through the alias map, *then* slugify (AC-020, AC-021)."""
    raise NotImplementedError


def compose_slug(*, app: str | None, subject: str) -> str:
    """The filename body: the app token first, then the subject's segments."""
    raise NotImplementedError
