"""S-06 — Collision resolution and metadata-preserving rename.

Covers AC-023, AC-024, AC-025, AC-026, AC-027, AC-058, AC-059.

The collision rules are pure enough to test directly against
`resolve_collisions`, which is where the suffix decision is made; the
metadata criteria and AC-027 are properties of the rename step, so they go
through the CLI with --apply.

AC-027 is the slice's backstop: if the inode set before a run equals the inode
set after it, then whatever else went wrong, no file was clobbered.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shotname.collisions import Proposal, normalize_for_comparison, resolve_collisions
from shotname.hashing import content_hash, short_hash
from shotname.plan import Action
from tests.conftest import Inject
from tests.support.cli import plan_records, proposed_names, run_cli
from tests.support.corpus import build_collision_pair, build_corpus
from tests.support.fakes import FakeOcr, FakeTransport, OverrideProbe
from tests.support.fsutil import (
    birthtime,
    get_xattr,
    inode_set,
    set_mtime,
    set_xattr,
    xattr_available,
)
from tests.support.images import write_noise_png

HASH_A = "a3f9c1" + "0" * 26
HASH_B = "b7e2d0" + "1" * 26

SCREEN_CAPTURE_XATTR = "com.apple.metadata:kMDItemIsScreenCapture"
USER_TAGS_XATTR = "com.apple.metadata:_kMDItemUserTags"


def _proposal(name: str, *, content: str, stem: str = "a") -> Proposal:
    return Proposal(
        path=Path("/corpus") / f"{stem}.png",
        content_hash=content,
        proposed_name=name,
    )


# --------------------------------------------------------------------------- #
# AC-023
# --------------------------------------------------------------------------- #


def test_ac023_colliding_proposals_each_get_their_own_content_hash_suffix() -> None:
    """AC-023: the suffix is six hex chars from *that* file's own hash."""
    first = _proposal(
        "2026-01-14-terminal-output.png", content=HASH_A, stem="first"
    )
    second = _proposal(
        "2026-01-14-terminal-output.png", content=HASH_B, stem="second"
    )

    resolved = resolve_collisions([first, second], existing_names=[])

    assert resolved[first.path] == "2026-01-14-terminal-output-a3f9c1.png"
    assert resolved[second.path] == "2026-01-14-terminal-output-b7e2d0.png"
    assert short_hash(HASH_A) == "a3f9c1"
    assert len(short_hash(HASH_A)) == 6


def test_ac023_non_colliding_proposal_gets_no_suffix() -> None:
    """AC-023: the suffix appears only where there is a collision to resolve."""
    only = _proposal("2026-01-14-slack-thread.png", content=HASH_A)

    resolved = resolve_collisions([only], existing_names=[])

    assert resolved[only.path] == "2026-01-14-slack-thread.png"


def test_ac023_colliding_files_are_disambiguated_end_to_end(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-023: two same-day captures land on two distinct names."""
    first, second = build_collision_pair(corpus_root)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--apply", "--wait")
    names = proposed_names(plan_records())

    assert result.exit_code == 0
    assert names[first.name] != names[second.name]
    assert len({names[first.name], names[second.name]}) == 2
    for path in (first, second):
        assert (corpus_root / names[path.name]).exists()


# --------------------------------------------------------------------------- #
# AC-024
# --------------------------------------------------------------------------- #


def test_ac024_suffixes_are_stable_across_calls() -> None:
    """AC-024: resolution is a function of its inputs, not of iteration order."""
    proposals = [
        _proposal("2026-01-14-terminal-output.png", content=HASH_A, stem="first"),
        _proposal("2026-01-14-terminal-output.png", content=HASH_B, stem="second"),
    ]

    assert resolve_collisions(proposals, existing_names=[]) == resolve_collisions(
        proposals, existing_names=[]
    )


def test_ac024_re_running_reproduces_the_same_suffixes(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-024: a second dry run over the same corpus proposes the same names."""
    build_collision_pair(corpus_root)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())

    run_cli(runner, corpus_root, "--wait")
    first_pass = proposed_names(plan_records())

    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--wait")
    second_pass = proposed_names(plan_records())

    assert first_pass == second_pass
    assert first_pass


# --------------------------------------------------------------------------- #
# AC-025
# --------------------------------------------------------------------------- #


def test_ac025_comparison_is_case_folded_and_nfc_normalized() -> None:
    """AC-025: the normalization used for existence checks folds both axes."""
    assert normalize_for_comparison("Stripe-Dashboard.png") == normalize_for_comparison(
        "stripe-dashboard.png"
    )
    assert normalize_for_comparison(
        unicodedata.normalize("NFD", "café.png")
    ) == normalize_for_comparison(unicodedata.normalize("NFC", "café.png"))


def test_ac025_differently_cased_existing_file_forces_disambiguation() -> None:
    """AC-025: "Stripe-Dashboard.png" on disk blocks "stripe-dashboard.png"."""
    proposal = _proposal("stripe-dashboard.png", content=HASH_A)

    resolved = resolve_collisions(
        [proposal], existing_names=["Stripe-Dashboard.png"]
    )

    assert resolved[proposal.path] == "stripe-dashboard-a3f9c1.png"


def test_ac025_existing_file_with_different_case_is_not_overwritten(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-025: end to end, the differently-cased file keeps its inode and bytes."""
    corpus = build_corpus(corpus_root)
    blocker = write_noise_png(
        corpus_root / "2026-01-14-SLACK-Stripe-Outage-Thread.png", seed=99
    )
    blocker_hash = content_hash(blocker)
    blocker_inode = blocker.lstat().st_ino

    probe = corpus.probe()
    probe.not_screen_capture.add(blocker)
    inject(probe=probe, ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    assert blocker.exists()
    assert blocker.lstat().st_ino == blocker_inode
    assert content_hash(blocker) == blocker_hash


# --------------------------------------------------------------------------- #
# AC-026
# --------------------------------------------------------------------------- #


def test_ac026_pre_existing_file_the_run_is_not_renaming_is_never_clobbered(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-026: a non-candidate already sitting on the proposed name survives."""
    corpus = build_corpus(corpus_root)
    blocker = write_noise_png(
        corpus_root / "2026-01-14-slack-stripe-outage-thread.png", seed=98
    )
    blocker_hash = content_hash(blocker)

    probe = corpus.probe()
    probe.not_screen_capture.add(blocker)
    inject(probe=probe, ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--apply", "--wait")
    names = proposed_names(plan_records())

    assert result.exit_code == 0
    assert blocker.exists()
    assert content_hash(blocker) == blocker_hash
    assert names[corpus.narrow_space.name] != blocker.name


def test_ac026_existing_name_forces_a_suffix_in_resolution() -> None:
    """AC-026: the same rule at the level where the decision is made."""
    proposal = _proposal("2026-01-14-slack-thread.png", content=HASH_A)

    resolved = resolve_collisions(
        [proposal], existing_names=["2026-01-14-slack-thread.png"]
    )

    assert resolved[proposal.path] == "2026-01-14-slack-thread-a3f9c1.png"


# --------------------------------------------------------------------------- #
# AC-027
# --------------------------------------------------------------------------- #


def test_ac027_inode_set_is_identical_before_and_after_apply(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-027: no run ever reduces or replaces files."""
    corpus = build_corpus(corpus_root)
    build_collision_pair(corpus_root)
    write_noise_png(corpus_root / "2026-01-14-slack-stripe-outage-thread.png", seed=97)

    probe = corpus.probe()
    probe.not_screen_capture.add(
        corpus_root / "2026-01-14-slack-stripe-outage-thread.png"
    )
    inject(probe=probe, ocr=FakeOcr(), transport=FakeTransport())

    before = inode_set(corpus.root)
    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    assert any(record.action is Action.RENAME for record in plan_records())
    assert inode_set(corpus.root) == before


# --------------------------------------------------------------------------- #
# AC-058
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not xattr_available(), reason="/usr/bin/xattr is unavailable")
def test_ac058_extended_attributes_survive_the_rename(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-058: kMDItemIsScreenCapture and _kMDItemUserTags are still there."""
    corpus = build_corpus(corpus_root)
    target = corpus.narrow_space
    set_xattr(target, SCREEN_CAPTURE_XATTR, "1")
    set_xattr(target, USER_TAGS_XATTR, "Red")

    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    proposed = proposed_names(plan_records())[target.name]
    renamed = corpus.root / proposed

    assert renamed.exists()
    assert get_xattr(renamed, SCREEN_CAPTURE_XATTR) == "1"
    assert get_xattr(renamed, USER_TAGS_XATTR) == "Red"


# --------------------------------------------------------------------------- #
# AC-059
# --------------------------------------------------------------------------- #


def test_ac059_mtime_and_birthtime_survive_the_rename(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-059: the rename moves the directory entry, not the timestamps."""
    corpus = build_corpus(corpus_root)
    target = corpus.narrow_space
    set_mtime(target, 1_700_000_000.0)
    mtime_before = target.lstat().st_mtime
    birthtime_before = birthtime(target)

    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    renamed = corpus.root / proposed_names(plan_records())[target.name]

    assert renamed.lstat().st_mtime == mtime_before
    assert birthtime(renamed) == birthtime_before
