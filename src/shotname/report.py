"""The completion report: the only way to verify a run over thousands of files.

Eyeballing a big corpus is not possible, so the report is built to be read instead
of the directory: counts per action, how many distinct slugs the run produced
against how many renames it made, the name-length distribution, and twenty
sampled old-to-new pairs (AC-062, AC-063).

"Distinct slugs" ignores the date prefix on purpose — two captures from different
days that both became `terminal-output` are the duplicate the number is there to
surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .plan import Action, PlanRecord
from .settings import DEFAULT_SEED, Resolution, RunSettings

#: Old-to-new pairs the report shows, or all of them when there are fewer (AC-063).
REPORT_SAMPLE_SIZE = 20


@dataclass(frozen=True)
class Report:
    """Everything the completion report states."""

    action_counts: dict[Action, int]
    total_renames: int
    distinct_slugs: int
    duplicate_slug_count: int
    name_length_min: int
    name_length_max: int
    name_length_median: float
    samples: tuple[tuple[str, str], ...]
    model: str
    resolution: Resolution
    total_cost_usd: float


def build_report(
    records: Sequence[PlanRecord],
    *,
    settings: RunSettings,
    total_cost_usd: float,
    seed: int = DEFAULT_SEED,
) -> Report:
    """Fold the plan into the report's numbers and its sampled pairs."""
    raise NotImplementedError


def format_report(report: Report) -> str:
    """Render the report for stdout, naming every number it carries."""
    raise NotImplementedError
