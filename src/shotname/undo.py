"""`shotname undo`: put a recorded run's renames back.

Undo reads the journal and nothing else — not the cache, not a plan — because the
journal is the only record that is written *before* each rename is attempted
(AC-035), and it is plain text that survives the cache database being deleted
(D-019).

Records are reversed newest-first, and anything unsafe stops a restore, leaving
the file exactly as it is: the contents changed since the rename (AC-039),
something else now occupies the original name (AC-040), or the file is no longer
where the journal left it. Every one of those is listed and makes the exit code
non-zero, because a partial restore that exits 0 cannot be told from a complete
one by a script (D-038).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import typer

from . import rename as rename_module
from .errors import EXIT_UNDO_INCOMPLETE, UsageError
from .hashing import content_hash
from .journal import Journal, JournalRecord, journal_path


class UndoStatus(Enum):
    """What became of one journal record."""

    RESTORED = "restored"
    CHANGED = "changed"
    OCCUPIED = "occupied"
    NOT_RENAMED = "not_renamed"
    MISSING = "missing"
    FAILED = "failed"


#: The statuses that mean undo did not finish, and the heading each is listed
#: under. `NOT_RENAMED` is absent on purpose: a record whose rename never
#: happened describes a file that is already at its original name (AC-036).
_PROBLEM_HEADINGS: tuple[tuple[UndoStatus, str], ...] = (
    (UndoStatus.CHANGED, "contents changed since the rename, left as they are"),
    (UndoStatus.OCCUPIED, "original name occupied by another file, left alone"),
    (UndoStatus.MISSING, "no longer where the journal left them"),
    (UndoStatus.FAILED, "could not be renamed back"),
)


@dataclass(frozen=True)
class UndoEntry:
    """One record and the verdict undo reached on it."""

    record: JournalRecord
    status: UndoStatus
    detail: str | None = None


@dataclass(frozen=True)
class UndoOutcome:
    """Everything one `undo` invocation did, and to what."""

    run_id: str | None
    entries: tuple[UndoEntry, ...]

    def with_status(self, status: UndoStatus) -> tuple[UndoEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status is status)

    @property
    def restored_count(self) -> int:
        return len(self.with_status(UndoStatus.RESTORED))

    @property
    def incomplete(self) -> bool:
        return any(self.with_status(status) for status, _ in _PROBLEM_HEADINGS)


def _restore_one(record: JournalRecord) -> UndoEntry:
    """Put one file back, or report why it was left alone."""
    if not os.path.lexists(record.new_path):
        if os.path.lexists(record.old_path):
            # The journal knows about a rename that never happened: the process
            # died between the fsync and rename(2) (AC-036). Nothing to undo.
            return UndoEntry(record, UndoStatus.NOT_RENAMED)
        return UndoEntry(record, UndoStatus.MISSING)

    try:
        digest = content_hash(record.new_path)
    except OSError as unreadable:
        return UndoEntry(record, UndoStatus.FAILED, str(unreadable))
    if digest != record.content_hash:
        return UndoEntry(record, UndoStatus.CHANGED)

    # Every existence test here is `lexists`: a dangling symlink at the original
    # name is still something undo must not rename over (AC-040).
    if os.path.lexists(record.old_path):
        return UndoEntry(record, UndoStatus.OCCUPIED)

    try:
        rename_module.rename_file(record.new_path, record.old_path)
    except OSError as failure:
        return UndoEntry(record, UndoStatus.FAILED, failure.strerror or str(failure))
    return UndoEntry(record, UndoStatus.RESTORED)


def undo_records(run_id: str | None, records: Sequence[JournalRecord]) -> UndoOutcome:
    """Reverse `records`, newest first.

    Newest first because a run may have renamed one file onto a name another file
    in the same run had just vacated; unwinding in reverse frees each name in the
    order it was taken.
    """
    return UndoOutcome(
        run_id=run_id,
        entries=tuple(_restore_one(record) for record in reversed(list(records))),
    )


def format_undo(outcome: UndoOutcome) -> str:
    """Render what undo did, naming every file it declined to touch."""
    headline = (
        f"restored {outcome.restored_count} of {len(outcome.entries)} "
        f"journalled renames"
    )
    if outcome.run_id is not None:
        headline += f" (run {outcome.run_id})"
    lines = [headline]

    unrenamed = outcome.with_status(UndoStatus.NOT_RENAMED)
    if unrenamed:
        lines.append(f"  already at the original name: {len(unrenamed)}")

    for status, heading in _PROBLEM_HEADINGS:
        entries = outcome.with_status(status)
        if not entries:
            continue
        lines.append(f"  {heading}:")
        for entry in entries:
            suffix = "" if entry.detail is None else f": {entry.detail}"
            lines.append(
                f"    {entry.record.new_path} "
                f"(original name {entry.record.old_path.name}){suffix}"
            )
    return "\n".join(lines)


def execute_undo(run_id: str | None, *, state_dir: Path | None = None) -> int:
    """One `shotname undo` invocation, start to finish. Returns the exit code."""
    journal = Journal(journal_path(state_dir))
    runs = journal.runs()

    if run_id is not None and run_id not in runs:
        raise UsageError(
            f"the journal at {journal.path} records no run {run_id}; "
            f"nothing was renamed"
        )
    target = run_id if run_id is not None else (runs[-1] if runs else None)
    records = journal.records_for(target) if target is not None else []

    outcome = undo_records(target, records)
    typer.echo(format_undo(outcome))
    return EXIT_UNDO_INCOMPLETE if outcome.incomplete else 0
