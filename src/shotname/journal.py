"""The undo journal: append-only JSONL, fsynced before every rename.

Deliberately not in the cache database. Undo has to work even if the SQLite file
is corrupt or deleted, and a plain-text journal is greppable by hand.

The ordering is the durability guarantee: the record is written *and fsynced*
before `rename(2)` is issued, so a crash can leave a record for a rename that
never happened — recoverable — but never a rename with no record (AC-035, AC-036).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

JOURNAL_FILENAME = "journal.jsonl"


@dataclass(frozen=True)
class JournalRecord:
    """One rename, as it was about to be attempted."""

    run_id: str
    ts: str
    old_path: Path
    new_path: Path
    content_hash: str


def journal_path(state_dir: Path | None = None) -> Path:
    """The journal's location under the durable state directory."""
    raise NotImplementedError


class Journal:
    """Append-only reader/writer over one journal file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: JournalRecord) -> None:
        """Write and fsync `record`. Must return before `rename(2)` is issued."""
        raise NotImplementedError

    def records(self) -> list[JournalRecord]:
        """Every record ever appended, oldest first."""
        raise NotImplementedError

    def runs(self) -> list[str]:
        """Distinct run ids in first-appearance order, so `runs[-1]` is the latest."""
        raise NotImplementedError

    def records_for(self, run_id: str) -> list[JournalRecord]:
        """Just one run's records, for `undo --run-id` (AC-038)."""
        raise NotImplementedError
