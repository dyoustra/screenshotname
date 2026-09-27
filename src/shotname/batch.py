"""Batch job lifecycle: chunk, submit, record, collect.

Batch dispatch decouples "I ran the command" from "the work finished", which is
where the tool's most expensive possible bug lives: an impatient second
invocation that re-submits pays for the whole backlog twice. `JobStore` is what
makes re-invocation safe — jobs are recorded durably, keyed by the corpus and the
run parameters they were submitted for, so the identical command finds them
(AC-070, AC-072, AC-073, AC-075).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .model import ModelRequest
from .settings import RunSettings, state_dir_path

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
    return state_dir_path(state_dir) / JOB_STORE_FILENAME


def corpus_key(settings: RunSettings) -> str:
    """Identifies "this corpus with these run parameters" for job lookup.

    Only the parameters that change what was *asked of the model* are in the key.
    `--apply` and `--wait` are deliberately absent: `shotname DIR --apply` must
    find the jobs that `shotname DIR --apply` submitted a day earlier even though
    the second invocation is the one that collects them (AC-072, AC-073).
    """
    parts = (
        str(Path(settings.root).expanduser().resolve()),
        settings.effective_model,
        settings.resolution.value,
        str(settings.prompt_template or ""),
        f"all_images={settings.all_images}",
        f"recursive={settings.recursive}",
        f"materialize={settings.materialize}",
    )
    return hashlib.blake2b("\0".join(parts).encode("utf-8"), digest_size=16).hexdigest()


def chunk_batch_requests(
    requests: Sequence[ModelRequest], *, max_requests: int, max_payload_bytes: int
) -> list[list[ModelRequest]]:
    """Split into submittable jobs under both provider limits, order preserved."""
    chunks: list[list[ModelRequest]] = []
    current: list[ModelRequest] = []
    size = 0
    for request in requests:
        payload = request.payload_size_bytes()
        over_count = len(current) >= max_requests
        over_payload = bool(current) and size + payload > max_payload_bytes
        if over_count or over_payload:
            chunks.append(current)
            current, size = [], 0
        current.append(request)
        size += payload
    if current:
        chunks.append(current)
    return chunks


class JobStore:
    """Durable record of submitted jobs."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, job: JobRecord) -> None:
        """Append `job` durably. Called before the run could possibly forget it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "job_id": job.job_id,
                "corpus_key": job.corpus_key,
                "custom_ids": list(job.custom_ids),
                "submitted_at": job.submitted_at,
            }
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            # A job id that is not on disk is a job that will be paid for twice
            # (AC-072), so this fsync is as load-bearing as the journal's.
            os.fsync(handle.fileno())

    def all_jobs(self) -> list[JobRecord]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        jobs: list[JobRecord] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            jobs.append(
                JobRecord(
                    job_id=raw["job_id"],
                    corpus_key=raw["corpus_key"],
                    custom_ids=tuple(raw["custom_ids"]),
                    submitted_at=raw["submitted_at"],
                )
            )
        return jobs

    def jobs_for(self, key: str) -> list[JobRecord]:
        """The jobs a re-invocation of the identical command should collect."""
        return [job for job in self.all_jobs() if job.corpus_key == key]
