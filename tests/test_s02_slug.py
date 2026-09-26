"""S-02 — Slug composition and text sanitization.

Covers AC-012, AC-015, AC-017, AC-019, AC-020, AC-021, AC-022.

Pure functions over strings: nothing here touches the filesystem, the model, or
the clock. `compose_slug` is the seam the rest of the pipeline uses — it takes
the application the model identified and the subject it described, and returns
the kebab-case body of the filename, app token first.
"""

from __future__ import annotations

import re

import pytest

from shotname.slug import (
    ALIAS_MAP_PATH,
    canonical_app_token,
    compose_slug,
    load_alias_map,
    slugify,
    transliterate_ascii,
)

SLUG_BODY = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# --------------------------------------------------------------------------- #
# AC-012
# --------------------------------------------------------------------------- #


def test_ac012_model_text_with_colon_and_slash_yields_neither() -> None:
    """AC-012: "3:42 PM / dashboard" produces no ':' and no '/'."""
    slug = compose_slug(app=None, subject="3:42 PM / dashboard")

    assert ":" not in slug
    assert "/" not in slug
    assert SLUG_BODY.match(slug)


def test_ac012_slugify_strips_path_and_time_punctuation() -> None:
    """AC-012: the same holds for the primitive `compose_slug` is built on."""
    assert ":" not in slugify("3:42 PM")
    assert "/" not in slugify("reports/q3")


# --------------------------------------------------------------------------- #
# AC-015
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        ".hidden dashboard",
        "-leading hyphen",
        "...dots...",
        "trailing space ",
        "trailing period.",
        "   ",
        "-",
        ".",
        "",
        "--weird--input--",
    ],
)
def test_ac015_slug_never_starts_with_period_or_hyphen_or_ends_with_space_or_period(
    text: str,
) -> None:
    """AC-015: leading '.'/'-' and trailing ' '/'.' are impossible outputs."""
    for slug in (slugify(text), compose_slug(app=None, subject=text)):
        assert not slug.startswith(".")
        assert not slug.startswith("-")
        assert not slug.endswith(" ")
        assert not slug.endswith(".")
        assert not slug.endswith("-")


def test_ac015_app_token_obeys_the_same_boundary_rules() -> None:
    """AC-015: an app name of pure punctuation cannot leak a boundary char."""
    slug = compose_slug(app="...Chrome...", subject="-dashboard-")

    assert not slug.startswith((".", "-"))
    assert not slug.endswith((" ", ".", "-"))


# --------------------------------------------------------------------------- #
# AC-017
# --------------------------------------------------------------------------- #


def test_ac017_accented_latin_text_is_transliterated_to_ascii() -> None:
    """AC-017: accents are folded rather than dropped."""
    assert transliterate_ascii("Café Übergröße") == "Cafe Ubergrosse"
    assert slugify("Café Übergröße") == "cafe-ubergrosse"


def test_ac017_untransliterable_characters_are_dropped_not_emitted_raw() -> None:
    """AC-017: CJK text has no transliteration, so it disappears."""
    slug = slugify("日本語 dashboard")

    assert slug.isascii()
    assert slug == "dashboard"


def test_ac017_slug_is_always_ascii() -> None:
    """AC-017: no path through slugification emits a non-ASCII byte."""
    for text in ("Ωμέγα", "→ arrow ←", "emoji 🎉 party", "Ελλάδα dashboard"):
        slug = compose_slug(app=None, subject=text)
        assert slug.isascii(), f"{text!r} produced {slug!r}"


# --------------------------------------------------------------------------- #
# AC-019
# --------------------------------------------------------------------------- #


def test_ac019_application_token_comes_first() -> None:
    """AC-019: the app is the segment immediately after the date prefix."""
    slug = compose_slug(app="Slack", subject="Stripe outage thread")

    assert slug == "slack-stripe-outage-thread"
    assert slug.split("-")[0] == "slack"


def test_ac019_app_first_even_when_the_subject_mentions_another_app() -> None:
    """AC-019: subject wording never displaces the app from the lead position."""
    slug = compose_slug(app="Slack", subject="stripe dashboard in chrome")

    assert slug.startswith("slack-")


# --------------------------------------------------------------------------- #
# AC-020
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("app", ["Google Chrome", "Chrome", "Chrome Canary"])
def test_ac020_alias_map_canonicalizes_chrome_variants(app: str) -> None:
    """AC-020: three spellings of the same browser yield one token."""
    assert canonical_app_token(app) == "chrome"


def test_ac020_alias_map_lives_in_a_data_file() -> None:
    """AC-020: the alias map is data, loadable from a file on disk."""
    assert ALIAS_MAP_PATH.exists()

    aliases = load_alias_map()
    assert aliases
    assert all(isinstance(key, str) and isinstance(value, str) for key, value in aliases.items())


def test_ac020_canonicalization_happens_before_slugification() -> None:
    """AC-020: "Google Chrome" must not survive as "google-chrome"."""
    slug = compose_slug(app="Google Chrome", subject="stripe dashboard")

    assert slug == "chrome-stripe-dashboard"


# --------------------------------------------------------------------------- #
# AC-021
# --------------------------------------------------------------------------- #


def test_ac021_unknown_application_is_slugified_unchanged() -> None:
    """AC-021: an app absent from the alias map keeps its own name."""
    assert "acmewidgetstudio" not in load_alias_map()
    assert canonical_app_token("Acme Widget Studio") == "acme-widget-studio"


def test_ac021_unknown_application_token_is_stable() -> None:
    """AC-021: the same input app name always yields the same token."""
    tokens = {canonical_app_token("Acme Widget Studio") for _ in range(5)}
    assert len(tokens) == 1


# --------------------------------------------------------------------------- #
# AC-022
# --------------------------------------------------------------------------- #


def test_ac022_no_placeholder_segment_when_the_app_is_unknown() -> None:
    """AC-022: an unidentifiable app means subject segments only."""
    slug = compose_slug(app=None, subject="Stripe outage thread")

    assert slug == "stripe-outage-thread"
    assert "unknown" not in slug
    assert "app" not in slug.split("-")


@pytest.mark.parametrize("app", [None, "", "   ", "unknown"])
def test_ac022_empty_or_unknown_app_adds_no_segment(app: str | None) -> None:
    """AC-022: blank and literal-"unknown" app values are all treated as absent."""
    slug = compose_slug(app=app, subject="terminal output")

    assert slug == "terminal-output"
