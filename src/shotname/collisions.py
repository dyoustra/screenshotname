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

import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

from .errors import ShotnameError
from .hashing import SHORT_HASH_CHARS, short_hash
from .naming import fit_to_byte_limit


@dataclass(frozen=True)
class Proposal:
    """One file's proposed name, before collisions are resolved."""

    path: Path
    content_hash: str
    proposed_name: str


def normalize_for_comparison(name: str) -> str:
    """Case-fold and NFC-normalize, so APFS-equal names compare equal (AC-025)."""
    return unicodedata.normalize("NFC", name).casefold()


def _with_suffix(name: str, suffix: str) -> str:
    """Insert `-suffix` before the extension, within the 255-byte limit."""
    extension = PurePath(name).suffix
    stem = name[: len(name) - len(extension)] if extension else name
    return fit_to_byte_limit(stem, f"-{suffix}{extension}")


def resolve_collisions(
    proposals: Sequence[Proposal], *, existing_names: Sequence[str]
) -> dict[Path, str]:
    """Map each proposal's path to its final, unique filename.

    `existing_names` are the directory entries the run is *not* renaming, which
    must never be clobbered (AC-026).
    """
    wanted = Counter(normalize_for_comparison(p.proposed_name) for p in proposals)
    taken = {normalize_for_comparison(name) for name in existing_names}

    resolved: dict[Path, str] = {}
    for proposal in proposals:
        name = proposal.proposed_name
        contested = wanted[normalize_for_comparison(name)] > 1
        # Two proposals wanting the same name are symmetric: neither has a claim
        # on the bare form, so both are suffixed (AC-023). A single proposal is
        # only suffixed when something already on disk holds the name (AC-026).
        if contested or normalize_for_comparison(name) in taken:
            name = _with_suffix(name, short_hash(proposal.content_hash))
        length = SHORT_HASH_CHARS
        while normalize_for_comparison(name) in taken:
            # Only reachable when two files share a six-hex prefix, or when a
            # file already on disk sits on the suffixed name. Lengthening the
            # suffix keeps the outcome a function of the contents (AC-024).
            length += 1
            if length > len(proposal.content_hash):
                raise ShotnameError(
                    f"cannot find a free name for {proposal.path}: "
                    f"{proposal.proposed_name!r} is taken under every suffix"
                )
            name = _with_suffix(
                proposal.proposed_name, short_hash(proposal.content_hash, length=length)
            )
        resolved[proposal.path] = name
        taken.add(normalize_for_comparison(name))
    return resolved
