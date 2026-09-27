"""The undo journal: append-only JSONL, fsynced before every rename.

Deliberately not in the cache database. Undo has to work even if the SQLite file
is corrupt or deleted, and a plain-text journal is greppable by hand.

The ordering is the durability guarantee: the record is written *and fsynced*
before `rename(2)` is issued, so a crash can leave a record for a rename that
never happened — recoverable — but never a rename with no record (AC-035, AC-036).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .settings import state_dir_path

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
    return state_dir_path(state_dir) / JOURNAL_FILENAME


class Journal:
    """Append-only reader/writer over one journal file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: JournalRecord) -> None:
        """Write and fsync `record`. Must return before `rename(2)` is issued."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "run_id": record.run_id,
                "ts": record.ts,
                "old_path": str(record.old_path),
                "new_path": str(record.new_path),
                "content_hash": record.content_hash,
            },
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            # The fsync, not the write, is what makes the record survive a power
            # loss — and it has to happen before rename(2) (AC-035, D-020).
            os.fsync(handle.fileno())

    def records(self) -> list[JournalRecord]:
        """Every record ever appended, oldest first."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        records: list[JournalRecord] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            records.append(
                JournalRecord(
                    run_id=raw["run_id"],
                    ts=raw["ts"],
                    old_path=Path(raw["old_path"]),
                    new_path=Path(raw["new_path"]),
                    content_hash=raw["content_hash"],
                )
            )
        return records

    def runs(self) -> list[str]:
        """Distinct run ids in first-appearance order, so `runs[-1]` is the latest."""
        seen: dict[str, None] = {}
        for record in self.records():
            seen.setdefault(record.run_id, None)
        return list(seen)

    def records_for(self, run_id: str) -> list[JournalRecord]:
        """Just one run's records, for `undo --run-id` (AC-038)."""
        return [record for record in self.records() if record.run_id == run_id]
