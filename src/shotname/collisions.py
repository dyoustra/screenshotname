"""Deterministic collision resolution, correct on case-insensitive APFS.

Two proposals colliding within one run, and a proposal colliding with a file
already on disk, are the same problem: the loser gets a six-hex-character suffix
from its *own* content hash, which makes the outcome a function of the inputs and
therefore stable across runs (AC-023, AC-024).

Comparison is case-folded and NFC-normalized, because on APFS
`Stripe-Dashboard.png` and `stripe-dashboard.png` are the same directory entry
(AC-025).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Proposal:
    """One file's proposed name, before collisions are resolved."""

    path: Path
    content_hash: str
    proposed_name: str


def normalize_for_comparison(name: str) -> str:
    """Case-fold and NFC-normalize, so APFS-equal names compare equal (AC-025)."""
    raise NotImplementedError


def resolve_collisions(
    proposals: Sequence[Proposal], *, existing_names: Sequence[str]
) -> dict[Path, str]:
    """Map each proposal's path to its final, unique filename.

    `existing_names` are the directory entries the run is *not* renaming, which
    must never be clobbered (AC-026).
    """
    raise NotImplementedError
