"""S-05 — Dry run, plan output, and apply.

Covers AC-028, AC-029, AC-030, AC-031, AC-032, AC-033, AC-034, AC-078, AC-079.

The hinge slice: after this, the pipeline runs end to end. Two criteria are
assertions about *ordering* rather than about output, and both use the same
technique — wrap the production function that produces the artefact and record
when it ran, relative to when a request went out. That is a stronger statement
than searching printed text for a dollar sign.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shotname.cost import PRICE_PER_MTOK, CostEstimate
from shotname.model import ModelRequest
from shotname.plan import (
    PLAN_FIELDS,
    Action,
    PlanRecord,
    plan_path,
    read_plan,
    write_plan,
)
from shotname.settings import DEFAULT_MODEL, RunSettings
from tests.conftest import Inject
from tests.support.cli import output_of, plan_records, proposed_names, run_cli
from tests.support.corpus import build_corpus
from tests.support.fakes import FakeOcr, FakeTransport
from tests.support.fsutil import snapshot_dir

ALLOWED_ACTIONS = {"rename", "abstain", "skip", "error"}


# --------------------------------------------------------------------------- #
# AC-028
# --------------------------------------------------------------------------- #


def test_ac028_dry_run_changes_nothing_on_disk(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-028: name, inode, mtime, and st_flags all survive a run with no --apply."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    before = snapshot_dir(corpus.root)

    result = run_cli(runner, corpus_root, "--wait")

    assert result.exit_code == 0
    assert snapshot_dir(corpus.root) == before


# --------------------------------------------------------------------------- #
# AC-029 / AC-030
# --------------------------------------------------------------------------- #


def test_ac029_dry_run_writes_one_plan_record_per_candidate(
    runner: CliRunner, corpus_root: Path, inject: Inject, state_dir: Path
) -> None:
    """AC-029: plan.jsonl holds exactly one record per candidate file."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())

    run_cli(runner, corpus_root, "--wait")

    path = plan_path(state_dir)
    assert path.exists()
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    records = read_plan(path)

    inspected = {entry.name for entry in corpus.root.iterdir() if entry.is_file()}
    assert len(lines) == len(inspected)
    assert {record.path.name for record in records} == inspected


def test_ac029_each_plan_record_carries_exactly_the_specified_fields(
    runner: CliRunner, corpus_root: Path, inject: Inject, state_dir: Path
) -> None:
    """AC-029: path, content_hash, action, proposed_name, reason, ocr_char_count,
    est_cost_usd — and nothing else."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())

    run_cli(runner, corpus_root, "--wait")

    assert set(PLAN_FIELDS) == {
        "path",
        "content_hash",
        "action",
        "proposed_name",
        "reason",
        "ocr_char_count",
        "est_cost_usd",
    }
    for line in plan_path(state_dir).read_text().splitlines():
        if not line.strip():
            continue
        assert set(json.loads(line)) == set(PLAN_FIELDS)


def test_ac030_every_plan_action_is_one_of_the_four_values(
    runner: CliRunner, corpus_root: Path, inject: Inject, state_dir: Path
) -> None:
    """AC-030: the action vocabulary is closed."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())

    run_cli(runner, corpus_root, "--wait")

    assert {action.value for action in Action} == ALLOWED_ACTIONS
    for line in plan_path(state_dir).read_text().splitlines():
        if not line.strip():
            continue
        assert json.loads(line)["action"] in ALLOWED_ACTIONS


# --------------------------------------------------------------------------- #
# AC-031
# --------------------------------------------------------------------------- #


def test_ac031_dry_run_is_the_built_in_default() -> None:
    """AC-031: nothing in the settings object renames by default."""
    assert RunSettings(root=Path(".")).apply is False


def test_ac031_no_environment_or_config_value_enables_renaming(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-031: --apply is the only thing that authorises a rename."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())

    for name in ("SHOTNAME_APPLY", "SHOTNAME_FORCE", "SHOTNAME_RENAME", "APPLY"):
        monkeypatch.setenv(name, "1")
    (corpus_root / "shotname.toml").write_text("apply = true\n")
    (corpus_root / ".shotname.toml").write_text("apply = true\n")

    before = snapshot_dir(corpus.root)
    result = run_cli(runner, corpus_root, "--wait")

    assert result.exit_code == 0
    assert snapshot_dir(corpus.root) == before


# --------------------------------------------------------------------------- #
# AC-032
# --------------------------------------------------------------------------- #


def test_ac032_cost_estimate_is_printed_before_any_model_request(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-032: the estimate is formatted before the first request goes out."""
    corpus = build_corpus(corpus_root)
    events: list[str] = []

    real_format = CostEstimate.format

    def recording_format(self: CostEstimate) -> str:
        events.append("estimate")
        return real_format(self)

    def on_send(index: int, request: ModelRequest) -> None:
        events.append("request")

    monkeypatch.setattr(CostEstimate, "format", recording_format)
    inject(
        probe=corpus.probe(),
        ocr=FakeOcr(),
        transport=FakeTransport(on_send=on_send),
    )

    result = run_cli(runner, corpus_root, "--sync")

    assert "request" in events, "no model request was issued, so ordering is untested"
    assert events[0] == "estimate"
    assert "$" in output_of(result)


# --------------------------------------------------------------------------- #
# AC-033
# --------------------------------------------------------------------------- #


def test_ac033_max_cost_below_the_estimate_aborts_before_any_request(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-033: the ceiling is checked before spending, not after."""
    corpus = build_corpus(corpus_root)
    transport = FakeTransport()
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=transport)
    before = snapshot_dir(corpus.root)

    result = run_cli(runner, corpus_root, "--apply", "--max-cost", "0.0000001")

    assert result.exit_code != 0
    assert transport.all_requests == []
    assert snapshot_dir(corpus.root) == before


# --------------------------------------------------------------------------- #
# AC-034
# --------------------------------------------------------------------------- #


def test_ac034_replaying_a_plan_issues_no_model_requests(
    runner: CliRunner, corpus_root: Path, inject: Inject, tmp_path: Path
) -> None:
    """AC-034: --apply --plan executes the recorded renames and calls nothing."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--wait")

    saved = tmp_path / "saved-plan.jsonl"
    saved.write_text(plan_path().read_text())
    expected = proposed_names(read_plan(saved))
    assert expected

    replay_transport = FakeTransport()
    replay_ocr = FakeOcr()
    inject(probe=corpus.probe(), ocr=replay_ocr, transport=replay_transport)

    result = run_cli(runner, corpus_root, "--apply", "--plan", str(saved))

    assert result.exit_code == 0
    assert replay_transport.all_requests == []
    for proposed in expected.values():
        assert (corpus.root / proposed).exists()


def test_ac034_replaying_a_filtered_plan_renames_only_what_it_records(
    runner: CliRunner, corpus_root: Path, inject: Inject, tmp_path: Path
) -> None:
    """AC-034: "exactly the renames recorded" means the others are left alone."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    run_cli(runner, corpus_root, "--wait")

    renames = [r for r in plan_records() if r.action is Action.RENAME]
    assert len(renames) >= 2
    chosen = renames[0]
    untouched = [record.path for record in renames[1:]]

    filtered = tmp_path / "one-rename.jsonl"
    write_plan([chosen], filtered)

    transport = FakeTransport()
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=transport)
    result = run_cli(runner, corpus_root, "--apply", "--plan", str(filtered))

    assert result.exit_code == 0
    assert transport.all_requests == []
    assert chosen.proposed_name is not None
    assert (corpus.root / chosen.proposed_name).exists()
    assert not chosen.path.exists()
    for path in untouched:
        assert path.exists(), f"{path.name} was renamed but was not in the plan"


# --------------------------------------------------------------------------- #
# AC-078
# --------------------------------------------------------------------------- #


def test_ac078_apply_without_a_plan_generates_names_and_renames(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-078: the one-pass form of --apply needs no pre-existing plan file."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())
    assert not plan_path().exists()

    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    renamed = [r for r in plan_records() if r.action is Action.RENAME]
    assert renamed
    for record in renamed:
        assert record.proposed_name is not None
        assert (corpus.root / record.proposed_name).exists()
        assert not record.path.exists()


# --------------------------------------------------------------------------- #
# AC-079
# --------------------------------------------------------------------------- #


def test_ac079_there_is_no_built_in_cost_ceiling() -> None:
    """AC-079: --max-cost is opt-in; the default is no ceiling at all."""
    assert RunSettings(root=Path(".")).max_cost_usd is None


def test_ac079_an_arbitrarily_large_estimate_proceeds(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-079: absurd pricing prints an absurd estimate and then runs anyway."""
    corpus = build_corpus(corpus_root)
    monkeypatch.setitem(PRICE_PER_MTOK, DEFAULT_MODEL, (1.0e9, 1.0e9))
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    assert "$" in output_of(result)
    assert any(record.action is Action.RENAME for record in plan_records())


def test_plan_round_trips_through_jsonl(tmp_path: Path) -> None:
    """AC-029: the plan a run writes is the plan a replay can read back."""
    record = PlanRecord(
        path=tmp_path / "Screenshot 2026-01-14 at 3.42.11 PM.png",
        content_hash="a3f9c1b2",
        action=Action.RENAME,
        proposed_name="2026-01-14-slack-stripe-outage-thread.png",
        reason=None,
        ocr_char_count=128,
        est_cost_usd=0.0012,
    )
    destination = tmp_path / "plan.jsonl"

    write_plan([record], destination)

    assert read_plan(destination) == [record]
