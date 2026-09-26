"""S-11 — Sampling, run settings, and completion report.

Covers AC-060, AC-061, AC-062, AC-063, AC-064, AC-076, AC-077.

The observation surface. AC-077 is the criterion that gives the other two flags
their purpose: unless the estimate and the report both state the model and the
tier, two `--sample` runs at different settings cannot be compared, which is the
whole reason `--model` and `--resolution` are run parameters rather than
constants.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shotname.cost import estimate_cost
from shotname.discovery import discover_candidates
from shotname.plan import Action, PlanRecord
from shotname.prompt import BUILTIN_PROMPT, load_prompt
from shotname.report import REPORT_SAMPLE_SIZE, build_report, format_report
from shotname.sampling import select_sample
from shotname.settings import (
    DEFAULT_MODEL,
    DEFAULT_SEED,
    Resolution,
    RunSettings,
)
from tests.conftest import Inject
from tests.support.cli import output_of, plan_records, run_cli
from tests.support.corpus import build_bulk_corpus, build_corpus
from tests.support.fakes import FakeOcr, FakeTransport, OverrideProbe, SyntheticProbe
from tests.support.fsutil import snapshot_dir

SETTINGS = RunSettings(root=Path("."))


def _rename_record(index: int, name: str) -> PlanRecord:
    return PlanRecord(
        path=Path("/corpus") / f"original-{index}.png",
        content_hash=f"{index:064x}",
        action=Action.RENAME,
        proposed_name=name,
        reason=None,
        ocr_char_count=120,
        est_cost_usd=0.001,
    )


def _sample_records() -> list[PlanRecord]:
    return [
        _rename_record(0, "2026-01-14-terminal-output-a3f9c1.png"),
        _rename_record(1, "2026-01-15-terminal-output-b7e2d0.png"),
        _rename_record(2, "2026-01-16-slack-stripe-outage-thread.png"),
        PlanRecord(
            path=Path("/corpus/blank.png"),
            content_hash=f"{9:064x}",
            action=Action.ABSTAIN,
            proposed_name=None,
            reason="empty_image",
            ocr_char_count=2,
            est_cost_usd=0.0,
        ),
        PlanRecord(
            path=Path("/corpus/photo.png"),
            content_hash=None,
            action=Action.SKIP,
            proposed_name=None,
            reason="not_screenshot",
            ocr_char_count=0,
            est_cost_usd=0.0,
        ),
    ]


# --------------------------------------------------------------------------- #
# AC-060
# --------------------------------------------------------------------------- #


def test_ac060_seed_has_a_fixed_default() -> None:
    """AC-060: the default seed is a constant, so sampling repeats by default."""
    assert isinstance(DEFAULT_SEED, int)
    assert RunSettings(root=Path(".")).seed == DEFAULT_SEED


def test_ac060_select_sample_is_deterministic_in_the_seed(corpus_root: Path) -> None:
    """AC-060: same candidates and same seed means the same N files."""
    candidates = list(
        discover_candidates(
            RunSettings(root=corpus_root), probe=SyntheticProbe(count=200)
        )
    )
    assert len(candidates) == 200

    first = select_sample(candidates, 30, seed=7)
    again = select_sample(candidates, 30, seed=7)
    other = select_sample(candidates, 30, seed=8)

    assert len(first) == 30
    assert [c.path for c in first] == [c.path for c in again]
    assert {c.path for c in first} != {c.path for c in other}


def test_ac060_select_sample_returns_everything_when_n_exceeds_the_corpus(
    corpus_root: Path,
) -> None:
    """AC-060: asking for more than exists is not an error."""
    candidates = list(
        discover_candidates(
            RunSettings(root=corpus_root), probe=SyntheticProbe(count=5)
        )
    )

    assert len(select_sample(candidates, 30, seed=DEFAULT_SEED)) == 5


def test_ac060_sample_flag_processes_exactly_n_files(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-060: --sample 5 does five files' worth of work, no more."""
    build_bulk_corpus(corpus_root, 20)
    ocr = FakeOcr()
    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=ocr, transport=transport)

    result = run_cli(runner, corpus_root, "--sample", "5")

    assert result.exit_code == 0
    assert len(ocr.calls) == 5
    assert len(transport.all_requests) == 5


def test_ac060_the_same_seed_selects_the_same_files(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-060: the default seed makes two sample runs comparable."""
    build_bulk_corpus(corpus_root, 20)

    first_ocr = FakeOcr()
    inject(probe=OverrideProbe(), ocr=first_ocr, transport=FakeTransport())
    run_cli(runner, corpus_root, "--sample", "5")

    second_ocr = FakeOcr()
    inject(probe=OverrideProbe(), ocr=second_ocr, transport=FakeTransport())
    run_cli(runner, corpus_root, "--sample", "5")

    third_ocr = FakeOcr()
    inject(probe=OverrideProbe(), ocr=third_ocr, transport=FakeTransport())
    run_cli(runner, corpus_root, "--sample", "5", "--seed", "99")

    assert set(first_ocr.calls) == set(second_ocr.calls)
    assert set(first_ocr.calls) != set(third_ocr.calls)


# --------------------------------------------------------------------------- #
# AC-061
# --------------------------------------------------------------------------- #


def test_ac061_sample_run_changes_nothing_even_with_apply(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-061: --sample overrides --apply; sampling is always read-only."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    before = snapshot_dir(corpus.root)

    result = run_cli(runner, corpus_root, "--sample", "3", "--apply")

    assert result.exit_code == 0
    assert snapshot_dir(corpus.root) == before


# --------------------------------------------------------------------------- #
# AC-062
# --------------------------------------------------------------------------- #


def test_ac062_report_counts_actions_slugs_duplicates_and_name_lengths() -> None:
    """AC-062: every number the completion report has to carry."""
    records = _sample_records()
    lengths = [
        len(record.proposed_name)
        for record in records
        if record.proposed_name is not None
    ]

    report = build_report(records, settings=SETTINGS, total_cost_usd=0.42)

    assert report.action_counts[Action.RENAME] == 3
    assert report.action_counts[Action.ABSTAIN] == 1
    assert report.action_counts[Action.SKIP] == 1
    assert report.total_renames == 3
    assert report.distinct_slugs == 2, "slugs differing only by date are not distinct"
    assert report.duplicate_slug_count == 1
    assert report.name_length_min == min(lengths)
    assert report.name_length_max == max(lengths)
    assert report.name_length_median == pytest.approx(statistics.median(lengths))


def test_ac062_formatted_report_shows_those_numbers() -> None:
    """AC-062: the numbers are actually printed, not merely computed."""
    report = build_report(_sample_records(), settings=SETTINGS, total_cost_usd=0.42)

    text = format_report(report)

    for word in ("rename", "abstain", "skip", "error", "duplicate", "median"):
        assert word in text.lower(), f"the report never mentions {word!r}"
    assert str(report.duplicate_slug_count) in text
    assert str(report.name_length_max) in text


def test_ac062_the_run_prints_a_completion_report(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-062: the report reaches stdout at the end of a real run."""
    build_bulk_corpus(corpus_root, 4)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--apply", "--wait")
    output = output_of(result).lower()

    assert result.exit_code == 0
    for word in ("rename", "abstain", "skip", "error", "duplicate", "median"):
        assert word in output


# --------------------------------------------------------------------------- #
# AC-063
# --------------------------------------------------------------------------- #


def test_ac063_report_samples_twenty_pairs() -> None:
    """AC-063: twenty old-to-new pairs when there are more than twenty renames."""
    records = [
        _rename_record(index, f"2026-01-14-capture-{index:04d}.png")
        for index in range(25)
    ]

    report = build_report(records, settings=SETTINGS, total_cost_usd=1.0)

    assert REPORT_SAMPLE_SIZE == 20
    assert len(report.samples) == 20
    proposed = {record.proposed_name for record in records}
    for old, new in report.samples:
        assert new in proposed
        assert old != new


def test_ac063_report_samples_all_pairs_when_there_are_fewer_than_twenty() -> None:
    """AC-063: five renames means five pairs, not five of twenty."""
    records = [
        _rename_record(index, f"2026-01-14-capture-{index:04d}.png")
        for index in range(5)
    ]

    report = build_report(records, settings=SETTINGS, total_cost_usd=1.0)

    assert len(report.samples) == 5


def test_ac063_sampled_pairs_are_deterministic_in_the_seed() -> None:
    """AC-063: the same run reports the same twenty pairs."""
    records = [
        _rename_record(index, f"2026-01-14-capture-{index:04d}.png")
        for index in range(40)
    ]

    first = build_report(records, settings=SETTINGS, total_cost_usd=1.0, seed=3)
    again = build_report(records, settings=SETTINGS, total_cost_usd=1.0, seed=3)

    assert first.samples == again.samples


# --------------------------------------------------------------------------- #
# AC-064
# --------------------------------------------------------------------------- #


def test_ac064_load_prompt_reads_the_given_path() -> None:
    """AC-064: a path means that file; no path means the built-in prompt."""
    assert load_prompt(None) == BUILTIN_PROMPT


def test_ac064_prompt_template_flag_replaces_the_built_in_prompt(
    runner: CliRunner, corpus_root: Path, inject: Inject, tmp_path: Path
) -> None:
    """AC-064: --prompt-template is what reaches the model."""
    build_bulk_corpus(corpus_root, 2)
    template = tmp_path / "terser.txt"
    template.write_text("TERSER-PROMPT-MARKER: name this capture in three words\n")
    assert load_prompt(template) == template.read_text()

    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(
        runner, corpus_root, "--wait", "--prompt-template", str(template)
    )

    assert result.exit_code == 0
    assert transport.all_requests
    for request in transport.all_requests:
        assert "TERSER-PROMPT-MARKER" in request.prompt


def test_ac064_print_prompt_writes_the_built_in_prompt_to_stdout(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-064: --print-prompt emits the prompt so it can be copied and edited."""
    build_bulk_corpus(corpus_root, 2)
    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--print-prompt")

    assert result.exit_code == 0
    assert BUILTIN_PROMPT.strip() in result.output
    assert transport.all_requests == []


# --------------------------------------------------------------------------- #
# AC-076
# --------------------------------------------------------------------------- #


def test_ac076_model_and_resolution_defaults() -> None:
    """AC-076: haiku and the standard tier are the starting point."""
    assert DEFAULT_MODEL == "claude-haiku-4-5-20251001"

    settings = RunSettings(root=Path("."))
    assert settings.model == DEFAULT_MODEL
    assert settings.resolution is Resolution.STANDARD


def test_ac076_resolution_accepts_exactly_standard_and_high() -> None:
    """AC-076: the tier vocabulary is closed at two values."""
    assert {tier.value for tier in Resolution} == {"standard", "high"}


@pytest.mark.parametrize("tier", ["standard", "high"])
def test_ac076_both_tiers_are_accepted_on_the_command_line(
    runner: CliRunner, corpus_root: Path, inject: Inject, tier: str
) -> None:
    """AC-076: --resolution standard and --resolution high both run."""
    build_bulk_corpus(corpus_root, 2)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--wait", "--resolution", tier)

    assert result.exit_code == 0


def test_ac076_an_unknown_tier_is_rejected(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-076: "exactly" means a third value is a usage error."""
    build_bulk_corpus(corpus_root, 2)
    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--resolution", "ultra")

    assert result.exit_code != 0
    assert transport.all_requests == []


def test_ac076_model_flag_reaches_the_request(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-076: --model overrides the default for the run."""
    build_bulk_corpus(corpus_root, 2)
    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--wait", "--model", "claude-sonnet-5")

    assert result.exit_code == 0
    assert transport.all_requests
    for request in transport.all_requests:
        assert request.model == "claude-sonnet-5"


# --------------------------------------------------------------------------- #
# AC-077
# --------------------------------------------------------------------------- #


def test_ac077_cost_estimate_states_the_model_and_the_tier() -> None:
    """AC-077: the estimate names the settings it was computed for."""
    settings = RunSettings(root=Path("."), resolution=Resolution.HIGH)
    estimate = estimate_cost(file_count=30, settings=settings)

    text = estimate.format()

    assert estimate.model == DEFAULT_MODEL
    assert estimate.resolution is Resolution.HIGH
    assert DEFAULT_MODEL in text
    assert "high" in text
    assert "$" in text


def test_ac077_report_states_the_model_the_tier_and_the_realised_cost() -> None:
    """AC-077: the report states what was actually incurred, not the estimate."""
    settings = RunSettings(root=Path("."), model="claude-sonnet-5", resolution=Resolution.HIGH)

    report = build_report(_sample_records(), settings=settings, total_cost_usd=0.42)
    text = format_report(report)

    assert report.model == "claude-sonnet-5"
    assert report.resolution is Resolution.HIGH
    assert report.total_cost_usd == pytest.approx(0.42)
    assert "claude-sonnet-5" in text
    assert "high" in text
    assert "0.42" in text


def test_ac077_two_sample_runs_at_different_settings_are_comparable(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-077: each sample run prints its own model, tier, and cost."""
    build_bulk_corpus(corpus_root, 6)

    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    standard = output_of(run_cli(runner, corpus_root, "--sample", "3"))

    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    high = output_of(
        run_cli(
            runner,
            corpus_root,
            "--sample",
            "3",
            "--resolution",
            "high",
            "--model",
            "claude-sonnet-5",
        )
    )

    assert DEFAULT_MODEL in standard
    assert "standard" in standard
    assert "$" in standard

    assert "claude-sonnet-5" in high
    assert "high" in high
    assert "$" in high


def test_ac077_plan_records_carry_a_per_file_estimated_cost(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-077: the realised total has per-file numbers behind it."""
    build_bulk_corpus(corpus_root, 3)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())

    run_cli(runner, corpus_root, "--wait")
    renames = [r for r in plan_records() if r.action is Action.RENAME]

    assert renames
    assert all(record.est_cost_usd > 0 for record in renames)
