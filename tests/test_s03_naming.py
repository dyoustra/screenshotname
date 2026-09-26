"""S-03 — Filename assembly: date prefix, extension, byte limit, determinism.

Covers AC-010, AC-011, AC-013, AC-014, AC-016, AC-018.

S-02 turned text into a slug; this slice turns a slug into a real filename. The
one criterion that reaches outside pure assembly is AC-016, whose second clause
("the image bytes were not re-encoded") is about the rename call site, so it
uses `shotname.rename.rename_file` — the single `rename(2)` wrapper the whole
tool goes through — together with `shotname.hashing.content_hash`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from shotname.hashing import content_hash
from shotname.naming import (
    MAX_FILENAME_BYTES,
    NAME_PATTERN,
    build_filename,
    date_prefix,
    fit_to_byte_limit,
    normalize_extension,
    parse_capture_date,
)
from shotname.rename import rename_file
from shotname.slug import compose_slug
from tests.support.corpus import NARROW_SPACE_NAME, NNBSP
from tests.support.images import write_noise_png

#: A birthtime with no special properties, used where the filename is unparseable.
BIRTHTIME = 1_768_400_531.0


# --------------------------------------------------------------------------- #
# AC-010
# --------------------------------------------------------------------------- #


def test_ac010_name_pattern_is_the_regex_the_criterion_specifies() -> None:
    """AC-010: the output contract is exactly this regular expression."""
    assert NAME_PATTERN.pattern == r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*\.[a-z0-9]+$"


@pytest.mark.parametrize(
    ("app", "subject", "extension"),
    [
        ("Slack", "Stripe outage thread", ".png"),
        (None, "terminal output", ".PNG"),
        ("Google Chrome", "Café Übergröße dashboard", ".jpeg"),
        ("Terminal", "3:42 PM / dashboard", ".gif"),
        (None, "日本語 dashboard", ".webp"),
    ],
)
def test_ac010_every_proposed_filename_matches_the_pattern(
    app: str | None, subject: str, extension: str
) -> None:
    """AC-010: assembly output satisfies the regex for varied inputs."""
    name = build_filename(
        date="2026-01-14",
        slug=compose_slug(app=app, subject=subject),
        extension=extension,
    )

    assert NAME_PATTERN.match(name), name


def test_ac010_disambiguation_suffix_still_matches_the_pattern() -> None:
    """AC-010: the collision suffix is just another kebab segment."""
    name = build_filename(
        date="2026-01-14",
        slug="terminal-output",
        extension=".png",
        suffix="a3f9c1",
    )

    assert name == "2026-01-14-terminal-output-a3f9c1.png"
    assert NAME_PATTERN.match(name)


# --------------------------------------------------------------------------- #
# AC-011
# --------------------------------------------------------------------------- #


def test_ac011_capture_date_is_parsed_from_the_original_filename() -> None:
    """AC-011: a parseable capture name supplies the date prefix."""
    assert parse_capture_date(NARROW_SPACE_NAME) == date(2026, 1, 14)
    assert date_prefix(NARROW_SPACE_NAME, birthtime=BIRTHTIME) == "2026-01-14"


def test_ac011_localized_capture_date_is_parsed() -> None:
    """AC-011: the date, not the "Screenshot" word, is what gets parsed."""
    assert parse_capture_date("Bildschirmfoto 2026-01-20 um 10.11.12.png") == date(
        2026, 1, 20
    )


def test_ac011_unparseable_filename_falls_back_to_birthtime_in_local_time() -> None:
    """AC-011: otherwise st_birthtime, rendered in the machine's local zone."""
    # Local time is the requirement (AC-011), not an oversight.
    expected = datetime.fromtimestamp(BIRTHTIME).strftime("%Y-%m-%d")  # noqa: DTZ006

    assert parse_capture_date("slack thread from january.png") is None
    assert date_prefix("slack thread from january.png", birthtime=BIRTHTIME) == expected


def test_ac011_narrow_no_break_space_does_not_defeat_date_parsing() -> None:
    """AC-011: U+202F in the name is not a reason to fall back to birthtime."""
    assert parse_capture_date(NARROW_SPACE_NAME.replace(NNBSP, " ")) == date(2026, 1, 14)


# --------------------------------------------------------------------------- #
# AC-013 / AC-014
# --------------------------------------------------------------------------- #


def test_ac013_proposed_filename_fits_255_bytes() -> None:
    """AC-013: the limit is 255 *bytes* of UTF-8, extension included."""
    slug = compose_slug(app="Slack", subject=" ".join(["verbose"] * 120))
    name = build_filename(date="2026-01-14", slug=slug, extension=".png")

    assert len(name.encode("utf-8")) <= MAX_FILENAME_BYTES
    assert MAX_FILENAME_BYTES == 255


def test_ac013_limit_counts_the_extension() -> None:
    """AC-013: a long extension eats into the budget for the stem."""
    name = fit_to_byte_limit("2026-01-14-" + "-".join(["seg"] * 200), ".jpeg")

    assert len(name.encode("utf-8")) <= MAX_FILENAME_BYTES
    assert name.endswith(".jpeg")


def test_ac014_truncation_lands_on_a_segment_boundary() -> None:
    """AC-014: no segment is cut in half and no name ends with a hyphen."""
    segments = [f"seg{index:03d}" for index in range(80)]
    name = build_filename(
        date="2026-01-14", slug="-".join(segments), extension=".png"
    )

    stem = name.removesuffix(".png")
    kept = stem.split("-")[3:]  # after the YYYY-MM-DD prefix

    assert len(name.encode("utf-8")) <= MAX_FILENAME_BYTES
    assert not stem.endswith("-")
    assert kept, "truncation removed every subject segment"
    assert kept == segments[: len(kept)], "segments were reordered or split"


def test_ac014_truncated_name_never_ends_with_a_hyphen() -> None:
    """AC-014: not even when the cut falls exactly on a separator."""
    for length in range(1, 120):
        name = fit_to_byte_limit("2026-01-14-" + "-".join(["ab"] * length), ".png")
        stem = name.removesuffix(".png")
        assert not stem.endswith("-"), name


def test_ac014_short_names_are_left_alone() -> None:
    """AC-014: fitting is a no-op when the name already fits."""
    assert fit_to_byte_limit("2026-01-14-slack-thread", ".png") == (
        "2026-01-14-slack-thread.png"
    )


# --------------------------------------------------------------------------- #
# AC-016
# --------------------------------------------------------------------------- #


def test_ac016_extension_is_preserved_and_lowercased() -> None:
    """AC-016: `.PNG` becomes `.png`; the format itself is unchanged."""
    assert normalize_extension("Screenshot 2026-01-15 at 9.01.02 AM.PNG") == ".png"
    assert normalize_extension("shot.JPEG") == ".jpeg"
    assert normalize_extension("shot.png") == ".png"


def test_ac016_assembled_name_carries_the_lowercased_extension() -> None:
    """AC-016: assembly uses the normalized extension, not the original."""
    name = build_filename(
        date="2026-01-15",
        slug="slack-stripe-outage-thread",
        extension=normalize_extension("Screenshot 2026-01-15 at 9.01.02 AM.PNG"),
    )

    assert name.endswith(".png")


def test_ac016_content_hash_is_unchanged_by_the_rename(tmp_path: Path) -> None:
    """AC-016: renaming moves the directory entry, never the image bytes."""
    original = write_noise_png(tmp_path / "Screenshot 2026-01-15 at 9.01.02 AM.PNG")
    before = content_hash(original)

    renamed = tmp_path / "2026-01-15-slack-stripe-outage-thread.png"
    rename_file(original, renamed)

    assert renamed.exists()
    assert not original.exists()
    assert content_hash(renamed) == before


# --------------------------------------------------------------------------- #
# AC-018
# --------------------------------------------------------------------------- #


def test_ac018_assembly_is_deterministic_within_a_process() -> None:
    """AC-018: identical inputs give a byte-identical name, every time."""
    names = {
        build_filename(
            date="2026-01-14",
            slug=compose_slug(app="Google Chrome", subject="Stripe outage — Café"),
            extension=".png",
        )
        for _ in range(20)
    }

    assert len(names) == 1


def test_ac018_assembly_is_deterministic_across_processes() -> None:
    """AC-018: two separate runs agree, even under different hash seeds.

    A different PYTHONHASHSEED reorders set and dict iteration, which is the
    usual way name generation becomes accidentally non-deterministic between
    runs.
    """
    script = (
        "from shotname.naming import build_filename\n"
        "from shotname.slug import compose_slug\n"
        "slug = compose_slug(app='Google Chrome', "
        "subject='Stripe outage thread — Café 日本語')\n"
        "print(build_filename(date='2026-01-14', slug=slug, extension='.PNG'))\n"
    )

    outputs: set[str] = set()
    for seed in ("1", "2", "1000"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = os.pathsep.join(sys.path)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        outputs.add(result.stdout.strip())

    assert len(outputs) == 1, outputs
    assert NAME_PATTERN.match(outputs.pop())
