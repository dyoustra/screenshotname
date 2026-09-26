"""Helpers for driving the Typer app and reading back what it wrote."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner, Result

from shotname.cli import app
from shotname.plan import Action, PlanRecord, plan_path, read_plan


def run_cli(runner: CliRunner, root: Path, *args: str) -> Result:
    return runner.invoke(app, ["run", str(root), *args])


def undo_cli(runner: CliRunner, *args: str) -> Result:
    return runner.invoke(app, ["undo", *args])


def plan_records(state_dir: Path | None = None) -> list[PlanRecord]:
    return read_plan(plan_path(state_dir))


def records_by_name(records: list[PlanRecord]) -> dict[str, PlanRecord]:
    return {record.path.name: record for record in records}


def actions_by_name(records: list[PlanRecord]) -> dict[str, Action]:
    return {record.path.name: record.action for record in records}


def proposed_names(records: list[PlanRecord]) -> dict[str, str]:
    return {
        record.path.name: record.proposed_name
        for record in records
        if record.proposed_name is not None
    }


def output_of(result: Result) -> str:
    """Everything the run printed, stdout and stderr together.

    Which stream a Click `Result` exposes separately depends on the Click
    version, so criteria that only care that a message was *printed* go through
    here rather than picking a stream.
    """
    combined = result.output
    try:
        stderr = result.stderr
    except (AttributeError, ValueError):
        return combined
    if stderr and stderr not in combined:
        combined += stderr
    return combined
