"""`plan.jsonl`: one record per candidate file, and the closed vocabularies it uses.

The plan is both the dry-run output a human reads and the input `--apply --plan`
replays, so `write_plan` and `read_plan` are exact inverses (AC-029, AC-034).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .errors import UsageError
from .settings import state_dir_path

#: The plan record schema, exactly (AC-029). Records carry these and nothing else.
PLAN_FIELDS: tuple[str, ...] = (
    "path",
    "content_hash",
    "action",
    "proposed_name",
    "reason",
    "ocr_char_count",
    "est_cost_usd",
)

PLAN_FILENAME = "plan.jsonl"


class Action(Enum):
    """What the run decided to do with a file. A closed set of four (AC-030)."""

    RENAME = "rename"
    ABSTAIN = "abstain"
    SKIP = "skip"
    ERROR = "error"


class Reason(Enum):
    """Why a file was not renamed. Recorded per file in the plan."""

    NOT_SCREENSHOT = "not_screenshot"
    UNSUPPORTED_FORMAT = "unsupported_format"
    DATALESS = "dataless"
    ALREADY_RENAMED = "already_renamed"
    EMPTY_IMAGE = "empty_image"
    MODEL_DECLINED = "model_declined"
    SCHEMA_INVALID = "schema_invalid"
    BATCH_ENTRY_FAILED = "batch_entry_failed"
    NOT_SAMPLED = "not_sampled"
    RENAME_FAILED = "rename_failed"


@dataclass(frozen=True)
class PlanRecord:
    """One candidate file's verdict."""

    path: Path
    content_hash: str | None
    action: Action
    proposed_name: str | None
    reason: str | None
    ocr_char_count: int
    est_cost_usd: float


def plan_path(state_dir: Path | None = None) -> Path:
    """Where a run writes its plan, under the durable state directory."""
    return state_dir_path(state_dir) / PLAN_FILENAME


def _as_json(record: PlanRecord) -> dict[str, object]:
    return {
        "path": str(record.path),
        "content_hash": record.content_hash,
        "action": record.action.value,
        "proposed_name": record.proposed_name,
        "reason": record.reason,
        "ocr_char_count": record.ocr_char_count,
        "est_cost_usd": record.est_cost_usd,
    }


def write_plan(records: Sequence[PlanRecord], destination: Path) -> None:
    """Write one JSON object per record, in order."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(
        json.dumps(_as_json(record), ensure_ascii=False) + "\n" for record in records
    )
    destination.write_text(lines, encoding="utf-8")


def read_plan(source: Path) -> list[PlanRecord]:
    """Read back what `write_plan` wrote."""
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as unreadable:
        raise UsageError(
            f"cannot read the plan at {source}: {unreadable}"
        ) from unreadable

    records: list[PlanRecord] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            records.append(
                PlanRecord(
                    path=Path(raw["path"]),
                    content_hash=raw["content_hash"],
                    action=Action(raw["action"]),
                    proposed_name=raw["proposed_name"],
                    reason=raw["reason"],
                    ocr_char_count=int(raw["ocr_char_count"]),
                    est_cost_usd=float(raw["est_cost_usd"]),
                )
            )
        except (KeyError, ValueError, TypeError) as malformed:
            raise UsageError(
                f"{source} line {number} is not a plan record: {malformed}"
            ) from malformed
    return records
