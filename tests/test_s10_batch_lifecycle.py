"""S-10 — Batch job lifecycle: submit, record, collect, resume.

Covers AC-070, AC-071, AC-072, AC-073, AC-074, AC-075.

Everything that is true of a job *after it exists*. AC-072 holds the most
expensive bug in the tool: batch dispatch decouples "I ran the command" from
"the work finished", so an impatient second invocation that re-submits pays for
the whole backlog twice. Recorded job ids are what make re-invocation safe, and
every test here re-invokes the identical command.

Two contract notes the tests depend on:

* the first poll happens immediately, and `--poll-interval` controls only the
  gap *between* polls, so a `--wait` run against an already-ended job is fast;
* `shotname.batch.JobStore` is the durable record, keyed so that jobs submitted
  for one corpus and one set of run parameters are the ones a re-invocation
  finds.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from shotname.batch import JobRecord, JobStore, job_store_path
from shotname.cache import Cache, cache_path, model_cache_key
from shotname.errors import EXIT_INTERRUPTED
from shotname.hashing import content_hash
from shotname.model import BatchEntryStatus, BatchJobState
from shotname.plan import Action
from shotname.prompt import BUILTIN_PROMPT_VERSION
from shotname.settings import DEFAULT_MODEL, Resolution
from tests.conftest import Inject
from tests.support.cli import (
    output_of,
    plan_records,
    proposed_names,
    records_by_name,
    run_cli,
)
from tests.support.corpus import build_bulk_corpus
from tests.support.fakes import (
    FakeOcr,
    FakeTransport,
    OverrideProbe,
    interrupt_poll_at,
)
from tests.support.fsutil import snapshot_dir


def _recorded_jobs() -> list[JobRecord]:
    return JobStore(job_store_path()).all_jobs()


def _cached(digest: str) -> bool:
    cache = Cache(cache_path())
    try:
        return (
            cache.get_suggestion(
                model_cache_key(
                    content_hash=digest,
                    model=DEFAULT_MODEL,
                    resolution=Resolution.STANDARD,
                    prompt_template_version=BUILTIN_PROMPT_VERSION,
                )
            )
            is not None
        )
    finally:
        cache.close()


# --------------------------------------------------------------------------- #
# AC-070
# --------------------------------------------------------------------------- #


def test_ac070_submitting_without_wait_records_jobs_and_renames_nothing(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-070: submit, print the ids and the collecting command, exit zero."""
    build_bulk_corpus(corpus_root, 4)
    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)
    before = snapshot_dir(corpus_root)

    result = run_cli(runner, corpus_root, "--apply")
    output = output_of(result)

    assert result.exit_code == 0
    assert transport.batch_submissions

    jobs = _recorded_jobs()
    assert {job.job_id for job in jobs} == set(transport.job_ids)
    for job in jobs:
        assert job.job_id in output

    assert str(corpus_root) in output, "the collecting command was not printed"
    assert "--apply" in output
    assert snapshot_dir(corpus_root) == before


# --------------------------------------------------------------------------- #
# AC-071
# --------------------------------------------------------------------------- #


def test_ac071_wait_polls_to_a_terminal_state_then_renames_in_the_same_run(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-071: --wait finishes the whole job inside one invocation."""
    paths = build_bulk_corpus(corpus_root, 3)
    transport = FakeTransport(
        poll_states=[
            BatchJobState.IN_PROGRESS,
            BatchJobState.IN_PROGRESS,
            BatchJobState.ENDED,
        ]
    )
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(
        runner, corpus_root, "--apply", "--wait", "--poll-interval", "0.01"
    )

    assert result.exit_code == 0
    assert len(transport.polls) >= 3, "the run did not wait for a terminal state"

    records = plan_records()
    assert any(record.action is Action.RENAME for record in records)
    for path in paths:
        assert not path.exists()
    for proposed in proposed_names(records).values():
        assert (corpus_root / proposed).exists()


# --------------------------------------------------------------------------- #
# AC-072
# --------------------------------------------------------------------------- #


def test_ac072_re_invocation_while_in_progress_submits_nothing(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-072: the impatient second run reports progress and spends nothing."""
    build_bulk_corpus(corpus_root, 4)
    transport = FakeTransport(poll_states=[BatchJobState.IN_PROGRESS])
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    first = run_cli(runner, corpus_root, "--apply")
    assert first.exit_code == 0
    submissions_after_first = len(transport.batch_submissions)
    assert submissions_after_first >= 1

    second = run_cli(runner, corpus_root, "--apply")
    output = output_of(second)

    assert second.exit_code == 0
    assert len(transport.batch_submissions) == submissions_after_first, (
        "the second invocation paid for the backlog again"
    )
    assert transport.polls, "the second invocation did not check on the job"
    for job in _recorded_jobs():
        assert job.job_id in output


# --------------------------------------------------------------------------- #
# AC-073
# --------------------------------------------------------------------------- #


def test_ac073_re_invocation_after_completion_collects_and_renames(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-073: results are ingested into the cache and no new request goes out."""
    paths = build_bulk_corpus(corpus_root, 3)
    digests = [content_hash(path) for path in paths]
    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    first = run_cli(runner, corpus_root, "--apply")
    assert first.exit_code == 0
    submissions_after_first = len(transport.batch_submissions)

    second = run_cli(runner, corpus_root, "--apply")

    assert second.exit_code == 0
    assert len(transport.batch_submissions) == submissions_after_first
    assert not transport.sync_requests

    for digest in digests:
        assert _cached(digest), "a collected result was not ingested into the cache"
    for path in paths:
        assert not path.exists()
    for proposed in proposed_names(plan_records()).values():
        assert (corpus_root / proposed).exists()


# --------------------------------------------------------------------------- #
# AC-074
# --------------------------------------------------------------------------- #


def test_ac074_errored_batch_entry_becomes_an_error_action(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-074: one bad entry does not spoil the successful entries beside it."""
    paths = build_bulk_corpus(corpus_root, 3)
    failing = content_hash(paths[0])

    def entry_status_for(custom_id: str) -> BatchEntryStatus:
        if custom_id == failing:
            return BatchEntryStatus.ERRORED
        return BatchEntryStatus.SUCCEEDED

    inject(
        probe=OverrideProbe(),
        ocr=FakeOcr(),
        transport=FakeTransport(entry_status_for=entry_status_for),
    )

    result = run_cli(runner, corpus_root, "--apply", "--wait")
    records = records_by_name(plan_records())

    assert result.exit_code == 0
    assert records[paths[0].name].action is Action.ERROR
    assert paths[0].exists(), "an errored entry was renamed anyway"
    for path in paths[1:]:
        assert records[path.name].action is Action.RENAME
        assert not path.exists()


def test_ac074_expired_batch_entry_becomes_an_error_action(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-074: expiry is handled the same way as an error."""
    paths = build_bulk_corpus(corpus_root, 2)
    expiring = content_hash(paths[0])

    def entry_status_for(custom_id: str) -> BatchEntryStatus:
        if custom_id == expiring:
            return BatchEntryStatus.EXPIRED
        return BatchEntryStatus.SUCCEEDED

    inject(
        probe=OverrideProbe(),
        ocr=FakeOcr(),
        transport=FakeTransport(entry_status_for=entry_status_for),
    )

    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    assert records_by_name(plan_records())[paths[0].name].action is Action.ERROR
    assert paths[0].exists()


# --------------------------------------------------------------------------- #
# AC-075
# --------------------------------------------------------------------------- #


def test_ac075_sigint_while_polling_leaves_the_jobs_alone(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-075: an interrupted wait must never cancel or forget a submitted job."""
    paths = build_bulk_corpus(corpus_root, 3)
    # The first poll is interrupted; by the time the later invocation polls
    # again, the job has ended.
    transport = FakeTransport(
        poll_states=[BatchJobState.IN_PROGRESS, BatchJobState.ENDED],
        on_poll=interrupt_poll_at(0),
    )
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    interrupted = run_cli(
        runner, corpus_root, "--apply", "--wait", "--poll-interval", "0.01"
    )

    assert interrupted.exit_code == EXIT_INTERRUPTED
    jobs_after_interrupt = {job.job_id for job in _recorded_jobs()}
    assert jobs_after_interrupt, "the submitted job was forgotten on interrupt"
    assert jobs_after_interrupt == set(transport.job_ids)

    submissions_before = len(transport.batch_submissions)
    later = run_cli(runner, corpus_root, "--apply", "--wait", "--poll-interval", "0.01")

    assert later.exit_code == 0
    assert len(transport.batch_submissions) == submissions_before, (
        "the interrupted job was re-submitted instead of collected"
    )
    for path in paths:
        assert not path.exists()
