"""Batch job lifecycle: chunk, submit, record, collect.

Batch dispatch decouples "I ran the command" from "the work finished", which is
where the tool's most expensive possible bug lives: an impatient second
invocation that re-submits pays for the whole backlog twice. `JobStore` is what
makes re-invocation safe — jobs are recorded durably, keyed by the corpus and the
run parameters they were submitted for, so the identical command finds them
(AC-070, AC-072, AC-073, AC-075).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .model import ModelRequest
from .settings import RunSettings

JOB_STORE_FILENAME = "jobs.jsonl"


@dataclass(frozen=True)
class JobRecord:
    """One submitted job, as recorded before the run could possibly forget it."""

    job_id: str
    corpus_key: str
    custom_ids: tuple[str, ...]
    submitted_at: str


def job_store_path(state_dir: Path | None = None) -> Path:
    """Where submitted job ids live, under the durable state directory."""
    raise NotImplementedError


def corpus_key(settings: RunSettings) -> str:
    """Identifies "this corpus with these run parameters" for job lookup."""
    raise NotImplementedError


def chunk_batch_requests(
    requests: Sequence[ModelRequest], *, max_requests: int, max_payload_bytes: int
) -> list[list[ModelRequest]]:
    """Split into submittable jobs under both provider limits, order preserved."""
    raise NotImplementedError


class JobStore:
    """Durable record of submitted jobs."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, job: JobRecord) -> None:
        raise NotImplementedError

    def all_jobs(self) -> list[JobRecord]:
        raise NotImplementedError

    def jobs_for(self, key: str) -> list[JobRecord]:
        """The jobs a re-invocation of the identical command should collect."""
        raise NotImplementedError
