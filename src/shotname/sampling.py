"""Deterministic subset selection for `--sample`.

Seeded from `--seed`, which has a fixed default, so the same directory and seed
always select the same files and two sample runs are comparable (AC-060).
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from .discovery import Candidate


def select_sample(
    candidates: Sequence[Candidate], count: int, *, seed: int
) -> list[Candidate]:
    """Exactly `count` candidates, or all of them when there are fewer.

    The selection is returned in enumeration order rather than in draw order, so
    the sample table reads chronologically while still being the same subset for
    a given seed (AC-060).
    """
    if count >= len(candidates):
        return list(candidates)
    chosen = random.Random(seed).sample(range(len(candidates)), count)
    return [candidates[index] for index in sorted(chosen)]
