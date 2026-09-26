"""Deterministic subset selection for `--sample`.

Seeded from `--seed`, which has a fixed default, so the same directory and seed
always select the same files and two sample runs are comparable (AC-060).
"""

from __future__ import annotations

from collections.abc import Sequence

from .discovery import Candidate


def select_sample(
    candidates: Sequence[Candidate], count: int, *, seed: int
) -> list[Candidate]:
    """Exactly `count` candidates, or all of them when there are fewer."""
    raise NotImplementedError
