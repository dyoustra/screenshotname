"""`plan.jsonl`: one record per candidate file, and the closed vocabularies it uses.

The plan is both the dry-run output a human reads and the input `--apply --plan`
replays, so `write_plan` and `read_plan` are exact inverses (AC-029, AC-034).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

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
    raise NotImplementedError


def write_plan(records: Sequence[PlanRecord], destination: Path) -> None:
    """Write one JSON object per record, in order."""
    raise NotImplementedError


def read_plan(source: Path) -> list[PlanRecord]:
    """Read back what `write_plan` wrote."""
    raise NotImplementedError
