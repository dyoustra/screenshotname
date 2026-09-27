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

import random
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from .hashing import SHORT_HASH_CHARS
from .plan import Action, PlanRecord
from .settings import DEFAULT_SEED, Resolution, RunSettings

#: Old-to-new pairs the report shows, or all of them when there are fewer (AC-063).
REPORT_SAMPLE_SIZE = 20

#: A proposed name, split into its date prefix, its slug, and the collision
#: suffix and extension the slug count deliberately ignores.
_PROPOSED_NAME = re.compile(
    rf"^\d{{4}}-\d{{2}}-\d{{2}}-(?P<slug>.+?)(?:-[0-9a-f]{{{SHORT_HASH_CHARS},}})?\.[^.]+$"
)


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


def slug_of(proposed_name: str) -> str:
    """The comparable part of a proposed name: no date, no suffix, no extension.

    Two captures from different days that both became `terminal-output` are
    exactly the duplicate the count exists to surface, and so are two that
    collided and were separated by a hash suffix.
    """
    match = _PROPOSED_NAME.match(proposed_name)
    return proposed_name if match is None else match.group("slug")


def build_report(
    records: Sequence[PlanRecord],
    *,
    settings: RunSettings,
    total_cost_usd: float,
    seed: int = DEFAULT_SEED,
) -> Report:
    """Fold the plan into the report's numbers and its sampled pairs."""
    action_counts = dict.fromkeys(Action, 0)
    for record in records:
        action_counts[record.action] += 1

    renames = [
        record
        for record in records
        if record.action is Action.RENAME and record.proposed_name is not None
    ]
    slugs = [
        slug_of(record.proposed_name) for record in renames if record.proposed_name
    ]
    lengths = [
        len(record.proposed_name)
        for record in records
        if record.proposed_name is not None
    ]

    pairs = [
        (record.path.name, record.proposed_name)
        for record in renames
        if record.proposed_name is not None
    ]
    if len(pairs) > REPORT_SAMPLE_SIZE:
        drawn = random.Random(seed).sample(range(len(pairs)), REPORT_SAMPLE_SIZE)
        pairs = [pairs[index] for index in sorted(drawn)]

    return Report(
        action_counts=action_counts,
        total_renames=len(renames),
        distinct_slugs=len(set(slugs)),
        duplicate_slug_count=len(slugs) - len(set(slugs)),
        name_length_min=min(lengths, default=0),
        name_length_max=max(lengths, default=0),
        name_length_median=statistics.median(lengths) if lengths else 0.0,
        samples=tuple(pairs),
        model=settings.effective_model,
        resolution=settings.resolution,
        total_cost_usd=total_cost_usd,
    )


def format_report(report: Report) -> str:
    """Render the report for stdout, naming every number it carries."""
    counts = "  ".join(
        f"{action.value}: {report.action_counts.get(action, 0)}" for action in Action
    )
    lines = [
        "run report",
        f"  {counts}",
        (
            f"  slugs: {report.distinct_slugs} distinct of {report.total_renames} "
            f"renames, {report.duplicate_slug_count} duplicate"
        ),
        (
            f"  name length: min {report.name_length_min}, "
            f"max {report.name_length_max}, median {report.name_length_median:g}"
        ),
        f"  settings: {report.model} at {report.resolution.value} resolution",
        f"  cost: ${report.total_cost_usd:.4f}",
    ]
    if report.samples:
        lines.append(f"  sample of {len(report.samples)} renames:")
        lines.extend(f"    {old} -> {new}" for old, new in report.samples)
    return "\n".join(lines)
