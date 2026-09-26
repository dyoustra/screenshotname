"""Fault injection at the rename call site.

The pipeline cannot rename anything until it has every suggestion in hand —
collision resolution (AC-023) compares proposed names across the whole run — so
an interrupt raised from the transport lands in the dispatch phase, before any
`rename(2)`. Criteria about what survives an interrupt *during renaming* inject
the fault here instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import shotname.rename


def fail_nth_rename(
    monkeypatch: pytest.MonkeyPatch, *, after: int, error: BaseException
) -> list[tuple[Path, Path]]:
    """Let `after` renames through, then raise `error` on the next one.

    Returns the list the successful `(old, new)` pairs are appended to, so a
    test can see exactly how far the run got.
    """
    done: list[tuple[Path, Path]] = []
    real_rename = shotname.rename.rename_file

    def patched(old: Path, new: Path) -> None:
        if len(done) == after:
            raise error
        real_rename(old, new)
        done.append((old, new))

    monkeypatch.setattr(shotname.rename, "rename_file", patched)
    return done


def record_renames(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, Path]]:
    """Observe every rename without changing behaviour."""
    done: list[tuple[Path, Path]] = []
    real_rename = shotname.rename.rename_file

    def patched(old: Path, new: Path) -> None:
        real_rename(old, new)
        done.append((old, new))

    monkeypatch.setattr(shotname.rename, "rename_file", patched)
    return done
