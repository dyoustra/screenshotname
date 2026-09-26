"""S-07 — Journal, undo, cache, and resume.

Covers AC-035 through AC-043.

One durability story from four directions. AC-035 is the ordering that makes the
rest trustworthy, and it is asserted the only way an ordering can be: by
recording when `os.fsync` ran relative to when `rename(2)` did.

`shotname.rename.rename_file` is the tool's single `rename(2)` call site. These
tests patch it on its own module, so the pipeline has to reach it through
`shotname.rename` rather than binding it at import time.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import shotname.rename
from shotname.cache import Cache, cache_path, model_cache_key, ocr_cache_key
from shotname.hashing import content_hash
from shotname.journal import Journal, JournalRecord, journal_path
from shotname.model import NameSuggestion
from shotname.plan import Action, Reason
from shotname.prompt import BUILTIN_PROMPT_VERSION
from shotname.settings import DEFAULT_MODEL, Resolution
from tests.conftest import Inject
from tests.support.cli import (
    actions_by_name,
    output_of,
    plan_records,
    proposed_names,
    records_by_name,
    run_cli,
    undo_cli,
)
from tests.support.corpus import build_bulk_corpus, build_corpus, screenshot_name
from tests.support.fakes import (
    FakeOcr,
    FakeTransport,
    OverrideProbe,
    SimulatedKill,
    kill_at,
    ocr_result,
)
from tests.support.faults import fail_nth_rename
from tests.support.images import write_noise_png

CONTENT_HASH = "a3f9c1" + "0" * 26


# --------------------------------------------------------------------------- #
# AC-035
# --------------------------------------------------------------------------- #


def test_ac035_journal_record_is_fsynced_before_the_rename(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-035: every rename is preceded immediately by a journal fsync."""
    corpus = build_corpus(corpus_root)
    events: list[str] = []
    real_fsync = os.fsync
    real_rename = shotname.rename.rename_file

    def recording_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def recording_rename(old: Path, new: Path) -> None:
        events.append("rename")
        real_rename(old, new)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(shotname.rename, "rename_file", recording_rename)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    assert "rename" in events, "nothing was renamed, so ordering is untested"
    for index, event in enumerate(events):
        if event == "rename":
            assert index > 0 and events[index - 1] == "fsync", (
                f"rename at position {index} was not preceded by an fsync: {events}"
            )


def test_ac035_journal_record_carries_the_specified_fields(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-035: run_id, ts, old_path, new_path, and content_hash are all recorded."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())

    run_cli(runner, corpus_root, "--apply", "--wait")
    records = Journal(journal_path()).records()

    assert records
    for record in records:
        assert isinstance(record, JournalRecord)
        assert record.run_id
        assert record.ts
        assert record.old_path != record.new_path
        assert record.content_hash == content_hash(record.new_path)


# --------------------------------------------------------------------------- #
# AC-036
# --------------------------------------------------------------------------- #


def test_ac036_kill_between_journal_write_and_rename_is_reconcilable(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-036: the journal knows about a rename that never happened, and a
    re-run sorts it out."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    fail_nth_rename(
        monkeypatch,
        after=0,
        error=SimulatedKill("killed after the journal write, before rename(2)"),
    )

    with pytest.raises(SimulatedKill):
        run_cli(runner, corpus_root, "--apply", "--wait")

    records = Journal(journal_path()).records()
    assert records, "the journal lost the record for the file that was in flight"
    stranded = records[-1]
    assert stranded.old_path.exists(), "the file was renamed despite the kill"
    assert not stranded.new_path.exists()

    monkeypatch.undo()
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    assert not stranded.old_path.exists()
    assert stranded.new_path.exists()


# --------------------------------------------------------------------------- #
# AC-037
# --------------------------------------------------------------------------- #


def test_ac037_undo_restores_the_most_recent_run(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-037: every renamed file goes back, and the count is printed."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--apply", "--wait")

    renamed = [r for r in plan_records() if r.action is Action.RENAME]
    assert renamed

    result = undo_cli(runner)
    output = output_of(result)

    assert result.exit_code == 0
    assert str(len(renamed)) in output
    for record in renamed:
        assert record.path.exists()
        assert record.proposed_name is not None
        assert not (corpus.root / record.proposed_name).exists()


def test_ac037_undo_reports_nothing_to_do_on_an_empty_journal(
    runner: CliRunner,
) -> None:
    """AC-037: undo with no recorded run restores zero files, not an error."""
    result = undo_cli(runner)

    assert result.exit_code == 0
    assert "0" in output_of(result)


# --------------------------------------------------------------------------- #
# AC-038
# --------------------------------------------------------------------------- #


def test_ac038_undo_run_id_restores_that_run_and_leaves_later_ones(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-038: --run-id targets an earlier run, not the most recent one."""
    first_batch = build_bulk_corpus(corpus_root, 3)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--apply", "--wait")
    first_names = proposed_names(plan_records())

    later = write_noise_png(corpus_root / screenshot_name(50), seed=5000)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--apply", "--wait")
    later_name = proposed_names(plan_records())[later.name]

    runs = Journal(journal_path()).runs()
    assert len(runs) == 2

    result = undo_cli(runner, "--run-id", runs[0])

    assert result.exit_code == 0
    for path in first_batch:
        assert path.exists(), f"{path.name} was not restored"
    for name in first_names.values():
        assert not (corpus_root / name).exists()
    assert (corpus_root / later_name).exists(), "a later run was undone too"


# --------------------------------------------------------------------------- #
# AC-039
# --------------------------------------------------------------------------- #


def test_ac039_undo_refuses_files_whose_contents_changed(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-039: a changed file is listed, left alone, and makes undo exit non-zero."""
    build_bulk_corpus(corpus_root, 3)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--apply", "--wait")

    names = proposed_names(plan_records())
    original, proposed = next(iter(names.items()))
    edited = corpus_root / proposed
    edited.write_bytes(edited.read_bytes() + b"# edited since the rename")

    result = undo_cli(runner)
    output = output_of(result)

    assert result.exit_code != 0
    assert edited.exists(), "a modified file was renamed back anyway"
    assert not (corpus_root / original).exists()
    assert proposed in output
    # The untouched files still went back.
    for other_original, other_proposed in names.items():
        if other_proposed == proposed:
            continue
        assert (corpus_root / other_original).exists()


# --------------------------------------------------------------------------- #
# AC-040
# --------------------------------------------------------------------------- #


def test_ac040_undo_skips_entries_whose_original_name_is_occupied(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-040: undo never overwrites whatever now sits at the original name."""
    build_bulk_corpus(corpus_root, 3)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--apply", "--wait")

    names = proposed_names(plan_records())
    original, proposed = next(iter(names.items()))
    squatter = write_noise_png(corpus_root / original, seed=777)
    squatter_hash = content_hash(squatter)

    result = undo_cli(runner)
    output = output_of(result)

    assert content_hash(corpus_root / original) == squatter_hash
    assert (corpus_root / proposed).exists(), "the renamed file was lost"
    assert original in output or proposed in output


# --------------------------------------------------------------------------- #
# AC-041
# --------------------------------------------------------------------------- #


def test_ac041_second_run_over_a_renamed_directory_proposes_nothing(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-041: already-renamed files are skipped with reason "already_renamed"."""
    build_bulk_corpus(corpus_root, 3)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--apply", "--wait")
    renamed_names = set(proposed_names(plan_records()).values())

    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    records = records_by_name(plan_records())
    assert Reason.ALREADY_RENAMED.value == "already_renamed"
    assert Action.RENAME not in actions_by_name(plan_records()).values()
    for name in renamed_names:
        assert records[name].action is Action.SKIP
        assert records[name].reason == Reason.ALREADY_RENAMED.value


# --------------------------------------------------------------------------- #
# AC-042
# --------------------------------------------------------------------------- #


def test_ac042_resume_after_sigkill_reprocesses_nothing_already_done(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-042: zero OCR and zero model requests for files the first run finished.

    "Completed" here means cached, not renamed: the content-hash cache is what
    makes a resume free, and a kill during dispatch happens long before the
    rename phase begins.
    """
    paths = build_bulk_corpus(corpus_root, 8)
    hashes = {path: content_hash(path) for path in paths}
    inject(
        probe=OverrideProbe(),
        ocr=FakeOcr(),
        transport=FakeTransport(on_send=kill_at(4)),
    )

    with pytest.raises(SimulatedKill):
        run_cli(runner, corpus_root, "--apply", "--sync", "--concurrency", "1")

    cache = Cache(cache_path())
    try:
        cached_hashes = {
            digest
            for digest in hashes.values()
            if cache.get_suggestion(
                model_cache_key(
                    content_hash=digest,
                    model=DEFAULT_MODEL,
                    resolution=Resolution.STANDARD,
                    prompt_template_version=BUILTIN_PROMPT_VERSION,
                )
            )
            is not None
        }
    finally:
        cache.close()

    assert cached_hashes, "the first run cached nothing, so resume is untested"
    cached_paths = {path for path, digest in hashes.items() if digest in cached_hashes}

    second_ocr = FakeOcr()
    second_transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=second_ocr, transport=second_transport)
    result = run_cli(runner, corpus_root, "--apply", "--sync")

    assert result.exit_code == 0
    assert not (set(second_ocr.calls) & cached_paths), "a cached file was re-OCRed"
    assert not (set(second_transport.custom_ids) & cached_hashes), (
        "a cached file was sent to the model again"
    )
    for path in paths:
        assert not path.exists(), f"{path.name} was never renamed"


# --------------------------------------------------------------------------- #
# AC-043
# --------------------------------------------------------------------------- #


def test_ac043_model_cache_key_covers_model_resolution_and_prompt_version() -> None:
    """AC-043: changing any of the four inputs invalidates the cached suggestion."""
    base = model_cache_key(
        content_hash=CONTENT_HASH,
        model=DEFAULT_MODEL,
        resolution=Resolution.STANDARD,
        prompt_template_version=BUILTIN_PROMPT_VERSION,
    )
    variants = {
        base,
        model_cache_key(
            content_hash="b7e2d0" + "1" * 26,
            model=DEFAULT_MODEL,
            resolution=Resolution.STANDARD,
            prompt_template_version=BUILTIN_PROMPT_VERSION,
        ),
        model_cache_key(
            content_hash=CONTENT_HASH,
            model="claude-sonnet-5",
            resolution=Resolution.STANDARD,
            prompt_template_version=BUILTIN_PROMPT_VERSION,
        ),
        model_cache_key(
            content_hash=CONTENT_HASH,
            model=DEFAULT_MODEL,
            resolution=Resolution.HIGH,
            prompt_template_version=BUILTIN_PROMPT_VERSION,
        ),
        model_cache_key(
            content_hash=CONTENT_HASH,
            model=DEFAULT_MODEL,
            resolution=Resolution.STANDARD,
            prompt_template_version="v99",
        ),
    }

    assert len(variants) == 5


def test_ac043_ocr_cache_key_depends_only_on_the_content_hash() -> None:
    """AC-043: OCR output outlives a change of model, tier, or prompt."""
    assert ocr_cache_key(CONTENT_HASH) == ocr_cache_key(CONTENT_HASH)
    assert ocr_cache_key(CONTENT_HASH) != ocr_cache_key("b7e2d0" + "1" * 26)


def test_ac043_changing_the_model_reuses_ocr_but_not_the_suggestion(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-043: end to end, a new model id re-queries but never re-OCRs."""
    build_bulk_corpus(corpus_root, 3)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--wait")

    second_ocr = FakeOcr()
    second_transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=second_ocr, transport=second_transport)
    result = run_cli(runner, corpus_root, "--wait", "--model", "claude-sonnet-5")

    assert result.exit_code == 0
    assert second_ocr.calls == [], "OCR was redone for an unchanged file"
    assert len(second_transport.all_requests) == 3


def test_ac043_cache_round_trips_ocr_and_suggestions() -> None:
    """AC-043: the cache the keys address actually stores and returns results."""
    result = ocr_result("Slack\nstripe outage thread")
    key = model_cache_key(
        content_hash=CONTENT_HASH,
        model=DEFAULT_MODEL,
        resolution=Resolution.STANDARD,
        prompt_template_version=BUILTIN_PROMPT_VERSION,
    )
    suggestion = NameSuggestion(app="Slack", subject="stripe outage thread", confidence=0.9)

    cache = Cache(cache_path())
    try:
        assert cache.get_ocr(CONTENT_HASH) is None
        cache.put_ocr(CONTENT_HASH, result)
        assert cache.get_ocr(CONTENT_HASH) == result

        assert cache.get_suggestion(key) is None
        cache.put_suggestion(key, suggestion)
        assert cache.get_suggestion(key) == suggestion
    finally:
        cache.close()
