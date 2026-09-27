"""S-01 — Enumeration and candidate identification.

Covers AC-001 through AC-009.

Every test here drives `discover_candidates` directly with an injected probe.
The macOS-only signals — `kMDItemIsScreenCapture` and `SF_DATALESS` — cannot be
set on an ordinary temporary file, so `OverrideProbe` forces them per path over
a real `RealFsProbe`; everything else about the filesystem stays real.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from shotname.discovery import (
    SUPPORTED_IMAGE_FORMATS,
    Candidate,
    DiscoveryStats,
    FsProbe,
    RealFsProbe,
    discover_candidates,
    format_discovery_summary,
    matches_screenshot_name,
    summarize,
)
from shotname.plan import Reason
from shotname.settings import RunSettings
from tests.support.corpus import (
    HAND_RENAMED_NAME,
    LOCALIZED_NAME,
    NARROW_SPACE_NAME,
    NNBSP,
    Corpus,
    build_corpus,
)
from tests.support.fakes import SyntheticProbe
from tests.support.images import write_noise_png


def _candidates(settings: RunSettings, probe: FsProbe) -> list[Candidate]:
    return list(discover_candidates(settings, probe=probe))


def _included_names(settings: RunSettings, probe: FsProbe) -> set[str]:
    return {c.path.name for c in _candidates(settings, probe) if c.included}


def _excluded(settings: RunSettings, probe: FsProbe, name: str) -> Candidate:
    matches = [c for c in _candidates(settings, probe) if c.path.name == name]
    assert matches, f"{name!r} was not inspected at all"
    return matches[0]


# --------------------------------------------------------------------------- #
# AC-001
# --------------------------------------------------------------------------- #


def test_ac001_name_with_narrow_no_break_space_is_a_candidate(
    corpus_root: Path,
) -> None:
    """AC-001: a U+202F in the capture name does not hide the file."""
    corpus = build_corpus(corpus_root)
    settings = RunSettings(root=corpus.root)

    assert NNBSP in NARROW_SPACE_NAME
    assert corpus.narrow_space.name in _included_names(settings, corpus.probe())


def test_ac001_screenshot_name_matcher_accepts_narrow_no_break_space() -> None:
    """AC-001: the filename pattern itself is U+202F-safe, not just the walk."""
    assert matches_screenshot_name(NARROW_SPACE_NAME)
    assert matches_screenshot_name(NARROW_SPACE_NAME.replace(NNBSP, " "))


# --------------------------------------------------------------------------- #
# AC-002
# --------------------------------------------------------------------------- #


def test_ac002_spotlight_attribute_includes_localized_capture_name(
    corpus_root: Path,
) -> None:
    """AC-002: a German-locale "Bildschirmfoto" capture is a candidate."""
    corpus = build_corpus(corpus_root)
    settings = RunSettings(root=corpus.root)

    assert not matches_screenshot_name(LOCALIZED_NAME)
    assert corpus.localized.name in _included_names(settings, corpus.probe())


def test_ac002_spotlight_attribute_includes_hand_renamed_capture(
    corpus_root: Path,
) -> None:
    """AC-002: a capture the user already renamed by hand is still a candidate."""
    corpus = build_corpus(corpus_root)
    settings = RunSettings(root=corpus.root)

    assert not matches_screenshot_name(HAND_RENAMED_NAME)
    assert corpus.hand_renamed.name in _included_names(settings, corpus.probe())


# --------------------------------------------------------------------------- #
# AC-003
# --------------------------------------------------------------------------- #


def test_ac003_ordinary_image_is_excluded(corpus_root: Path) -> None:
    """AC-003: neither the attribute nor the name pattern means not a candidate."""
    corpus = build_corpus(corpus_root)
    settings = RunSettings(root=corpus.root)

    candidate = _excluded(settings, corpus.probe(), corpus.plain_image.name)
    assert not candidate.included
    assert candidate.excluded is Reason.NOT_SCREENSHOT


def test_ac003_all_images_flag_includes_ordinary_image(corpus_root: Path) -> None:
    """AC-003: --all-images brings the same file into the candidate set."""
    corpus = build_corpus(corpus_root)
    settings = RunSettings(root=corpus.root, all_images=True)

    assert corpus.plain_image.name in _included_names(settings, corpus.probe())


# --------------------------------------------------------------------------- #
# AC-004
# --------------------------------------------------------------------------- #


def test_ac004_enumeration_is_non_recursive_by_default(corpus_root: Path) -> None:
    """AC-004: a capture in a subdirectory is not reached without --recursive."""
    corpus = build_corpus(corpus_root)
    settings = RunSettings(root=corpus.root)

    inspected = {c.path for c in _candidates(settings, corpus.probe())}
    assert corpus.nested not in inspected


def test_ac004_recursive_flag_descends_into_subdirectories(corpus_root: Path) -> None:
    """AC-004: --recursive reaches it."""
    corpus = build_corpus(corpus_root)
    settings = RunSettings(root=corpus.root, recursive=True)

    included = {c.path for c in _candidates(settings, corpus.probe()) if c.included}
    assert corpus.nested in included


# --------------------------------------------------------------------------- #
# AC-005
# --------------------------------------------------------------------------- #


def test_ac005_directory_symlinks_are_not_followed_and_no_file_is_visited_twice(
    corpus_root: Path,
) -> None:
    """AC-005: --recursive does not traverse a symlinked directory."""
    real = corpus_root / "real"
    shot = write_noise_png(real / NARROW_SPACE_NAME, seed=42)
    (corpus_root / "link").symlink_to(real, target_is_directory=True)

    settings = RunSettings(root=corpus_root, recursive=True)
    paths = [c.path for c in _candidates(settings, RealFsProbe())]

    assert paths.count(shot) == 1
    assert len(paths) == len(set(paths)), "a file was visited more than once"
    assert not any("link" in path.parts for path in paths)


# --------------------------------------------------------------------------- #
# AC-006 / AC-007
# --------------------------------------------------------------------------- #


def test_ac006_dataless_file_is_skipped_without_being_read(corpus_root: Path) -> None:
    """AC-006: SF_DATALESS means skip, and skip means never open the file."""
    corpus = build_corpus(corpus_root)
    probe = corpus.probe()
    settings = RunSettings(root=corpus.root)

    candidate = _excluded(settings, probe, corpus.dataless.name)

    assert candidate.excluded is Reason.DATALESS
    assert corpus.dataless not in probe.sniffed, "a dataless file was opened"


def test_ac006_summary_states_dataless_count_and_nominal_bytes(
    corpus_root: Path,
) -> None:
    """AC-006: the run report carries the count and the total nominal size."""
    corpus = build_corpus(corpus_root)
    nominal_size = corpus.dataless.stat().st_size
    settings = RunSettings(root=corpus.root)

    stats = summarize(discover_candidates(settings, probe=corpus.probe()))

    assert isinstance(stats, DiscoveryStats)
    assert stats.dataless_skipped == 1
    assert stats.dataless_bytes == nominal_size

    summary = format_discovery_summary(stats)
    assert "1" in summary
    assert str(nominal_size) in summary


def test_ac007_materialize_processes_dataless_files(corpus_root: Path) -> None:
    """AC-007: --materialize treats a dataless file like any other candidate."""
    corpus = build_corpus(corpus_root)
    settings = RunSettings(root=corpus.root, materialize=True)
    probe = corpus.probe()

    included = _included_names(settings, probe)

    assert corpus.dataless.name in included
    assert corpus.dataless in probe.sniffed


# --------------------------------------------------------------------------- #
# AC-008
# --------------------------------------------------------------------------- #


def test_ac008_unsupported_image_format_is_excluded_with_a_reason(
    corpus_root: Path,
) -> None:
    """AC-008: a BMP capture is excluded and says why."""
    corpus = build_corpus(corpus_root)
    settings = RunSettings(root=corpus.root)

    candidate = _excluded(settings, corpus.probe(), corpus.unsupported.name)

    assert not candidate.included
    assert candidate.excluded is Reason.UNSUPPORTED_FORMAT


def test_ac008_supported_formats_are_exactly_jpeg_png_gif_webp() -> None:
    """AC-008: the supported set is the four formats the criterion names."""
    assert set(SUPPORTED_IMAGE_FORMATS) == {"jpeg", "png", "gif", "webp"}


# --------------------------------------------------------------------------- #
# AC-009
# --------------------------------------------------------------------------- #


def test_ac009_enumeration_is_lazy(corpus_root: Path) -> None:
    """AC-009: discovery hands back an iterator, not a materialised list."""
    settings = RunSettings(root=corpus_root)
    result = discover_candidates(settings, probe=SyntheticProbe(count=10))

    assert isinstance(result, Iterator)


# Run in a fresh interpreter per corpus size, so one measurement cannot inherit
# the other's heap. Peak resident memory is what AC-009 specifies. tracemalloc
# is not a substitute: it starts from a few KB, so CPython interning each unique
# path segment (pathlib does, per filename) outgrows 2x on its own.
_RSS_PROBE = """
import resource, sys, tempfile
from pathlib import Path
from shotname.discovery import discover_candidates
from shotname.settings import RunSettings
from tests.support.fakes import SyntheticProbe
settings = RunSettings(root=Path(tempfile.mkdtemp()))
for _ in discover_candidates(settings, probe=SyntheticProbe(count=int(sys.argv[1]))):
    pass
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
"""


def _peak_rss_for(count: int) -> int:
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, "-c", _RSS_PROBE, str(count)],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=True,
    )
    return int(done.stdout.strip())


def test_ac009_peak_memory_does_not_scale_with_corpus_size() -> None:
    """AC-009: 50,000 candidates use no more than twice the peak RSS of 500."""
    peak_small = _peak_rss_for(500)
    peak_large = _peak_rss_for(50_000)

    assert peak_large <= 2 * peak_small, (
        f"peak RSS grew from {peak_small} to {peak_large} across a 100x corpus"
    )


def test_ac009_every_synthetic_candidate_is_still_yielded(corpus_root: Path) -> None:
    """AC-009: laziness must not come at the cost of dropping files."""
    settings = RunSettings(root=corpus_root)
    count = sum(1 for _ in discover_candidates(settings, probe=SyntheticProbe(count=777)))
    assert count == 777


def test_corpus_fixture_matches_the_spec(corpus_root: Path) -> None:
    """The fixture corpus the spec's Testing row calls for is actually built."""
    corpus = build_corpus(corpus_root)
    assert isinstance(corpus, Corpus)
    for path in (
        corpus.narrow_space,
        corpus.uppercase_ext,
        corpus.localized,
        corpus.hand_renamed,
        corpus.plain_image,
        corpus.unsupported,
        corpus.near_blank,
        corpus.dataless,
        corpus.occupied,
        corpus.nested,
    ):
        assert path.exists()
    assert corpus.uppercase_ext.suffix == ".PNG"
