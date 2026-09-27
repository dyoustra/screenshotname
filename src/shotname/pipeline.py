"""One run, end to end: preflight, enumerate, prepare, dispatch, rename, report.

The shape of a run is fixed by three constraints that pull against each other:

* names cannot be assigned file by file, because collision resolution compares
  every proposal against every other one (AC-023), so dispatch has to finish
  before any rename begins;
* the cost estimate has to be printed before the first request leaves (AC-032);
* and the whole thing has to be resumable, which means the unit of "already
  done" is a cache entry keyed by content hash rather than a completed rename
  (AC-042, AC-054).

So a run is four phases — prepare, dispatch, propose, apply — and an interrupt in
any of them leaves the phases before it durably recorded.
"""

from __future__ import annotations

import asyncio
import errno
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import typer

from . import model as model_module
from . import rename as rename_module
from .abstain import abstain_reason, image_entropy
from .batch import JobRecord, JobStore, chunk_batch_requests, corpus_key, job_store_path
from .cache import Cache, cache_path, model_cache_key
from .collisions import Proposal, resolve_collisions
from .cost import estimate_cost, realised_cost_usd
from .deps import Deps
from .discovery import (
    Candidate,
    discover_candidates,
    format_discovery_summary,
    summarize,
)
from .errors import (
    API_KEY_HINT,
    EXIT_INTERRUPTED,
    BackendUnavailableError,
    PermissionDeniedError,
    RateLimitedError,
    SchemaInvalidError,
    UsageError,
    full_disk_access_message,
)
from .hashing import content_hash
from .imaging import encode_for_model
from .journal import Journal, JournalRecord, journal_path
from .model import (
    REFUSAL_STOP_REASONS,
    BatchEntryStatus,
    BatchJobState,
    BatchResultEntry,
    ModelRequest,
    ModelResponse,
    NameSuggestion,
    RequestInput,
    Transport,
    build_model_requests,
    is_refusal,
)
from .naming import build_filename, date_prefix, normalize_extension
from .plan import Action, PlanRecord, Reason, plan_path, read_plan, write_plan
from .prompt import load_prompt, prompt_version
from .report import build_report, format_report
from .sampling import select_sample
from .settings import Endpoint, RunEnvironment, RunSettings, resolve_run_environment
from .slug import compose_slug

#: Attempts at one file before its 429s are treated as a real failure (AC-051).
MAX_RATE_LIMIT_ATTEMPTS = 6

#: Floor for the exponential backoff; the retry-after header overrides it upward.
INITIAL_BACKOFF_SECONDS = 0.25

#: One request, and one retry if the first answer failed validation (AC-047).
SCHEMA_ATTEMPTS = 2


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
# Per-file state
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Outcome:
    """What the model said about one capture, however it was reached."""

    suggestion: NameSuggestion | None = None
    abstained: Reason | None = None
    errored: Reason | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _Unit:
    """One included file, hashed and OCRed, on its way to a proposed name."""

    candidate: Candidate
    content_hash: str
    ocr_text: str
    ocr_char_count: int
    image_bytes: bytes
    outcome: _Outcome | None = None


@dataclass
class _BatchStatus:
    """Where a batch-dispatched run got to within this invocation."""

    jobs: list[JobRecord] = field(default_factory=list)
    collected: bool = False
    interrupted: bool = False


# --------------------------------------------------------------------------- #
# Preflight and enumeration
# --------------------------------------------------------------------------- #


def _preflight(settings: RunSettings, environment: RunEnvironment) -> None:
    """Reject an unrunnable invocation before it touches the filesystem (AC-050)."""
    if settings.local:
        return
    if environment.api_key is None:
        raise UsageError(API_KEY_HINT)


def _discover(settings: RunSettings, deps: Deps) -> list[Candidate]:
    """Enumerate, turning a TCC denial into a message rather than a traceback."""
    try:
        return list(discover_candidates(settings, probe=deps.probe))
    except OSError as failure:
        if isinstance(failure, PermissionError) or failure.errno in {
            errno.EPERM,
            errno.EACCES,
        }:
            raise PermissionDeniedError(
                full_disk_access_message(settings.root)
            ) from failure
        raise UsageError(f"cannot read {settings.root}: {failure}") from failure


# --------------------------------------------------------------------------- #
# Phase 1: prepare
# --------------------------------------------------------------------------- #


def _prepare(
    candidates: Sequence[Candidate],
    *,
    settings: RunSettings,
    deps: Deps,
    cache: Cache,
    version: str,
) -> tuple[list[_Unit], dict[Path, PlanRecord]]:
    """Hash, OCR, and pre-judge each candidate, reusing everything cached.

    Nothing here issues a model request, which is what lets the cost estimate be
    printed with the real number of files still to pay for (AC-032).
    """
    units: list[_Unit] = []
    unreadable: dict[Path, PlanRecord] = {}
    for candidate in candidates:
        try:
            units.append(
                _prepare_one(
                    candidate,
                    settings=settings,
                    deps=deps,
                    cache=cache,
                    version=version,
                )
            )
        except OSError as failure:
            # The file was readable when it was enumerated and is not now: a
            # racing move or delete. One file's disappearance is not the run's
            # problem, so it is recorded and the run carries on.
            unreadable[candidate.path] = PlanRecord(
                path=candidate.path,
                content_hash=None,
                action=Action.ERROR,
                proposed_name=None,
                reason=f"unreadable: {failure.strerror or failure}",
                ocr_char_count=0,
                est_cost_usd=0.0,
            )
    return units, unreadable


def _prepare_one(
    candidate: Candidate,
    *,
    settings: RunSettings,
    deps: Deps,
    cache: Cache,
    version: str,
) -> _Unit:
    digest = content_hash(candidate.path)
    # `--sample` is the prompt-iteration loop, and two sample runs are only
    # comparable if the second one really does the work the first one did
    # (AC-060). It still populates the cache: the work has been paid for.
    reuse = settings.sample is None

    recognized = cache.get_ocr(digest) if reuse else None
    if recognized is None:
        recognized = deps.ocr.recognize(candidate.path)
        cache.put_ocr(digest, recognized)

    declined = abstain_reason(
        ocr_char_count=recognized.char_count,
        entropy=image_entropy(candidate.path),
        settings=settings,
    )
    outcome: _Outcome | None = None
    image_bytes = b""
    if declined is not None:
        outcome = _Outcome(abstained=declined)
    else:
        cached = (
            cache.get_suggestion(
                model_cache_key(
                    content_hash=digest,
                    model=settings.effective_model,
                    resolution=settings.resolution,
                    prompt_template_version=version,
                )
            )
            if reuse
            else None
        )
        if cached is not None:
            outcome = _Outcome(suggestion=cached)
        else:
            image_bytes = encode_for_model(candidate.path, settings.resolution)

    return _Unit(
        candidate=candidate,
        content_hash=digest,
        ocr_text=recognized.text,
        ocr_char_count=recognized.char_count,
        image_bytes=image_bytes,
        outcome=outcome,
    )


# --------------------------------------------------------------------------- #
# Phase 2: dispatch
# --------------------------------------------------------------------------- #


def _requests_for(
    units: Sequence[_Unit], *, settings: RunSettings, prompt: str
) -> list[ModelRequest]:
    return build_model_requests(
        [
            RequestInput(
                path=unit.candidate.path,
                content_hash=unit.content_hash,
                image_bytes=unit.image_bytes,
                ocr_text=unit.ocr_text,
            )
            for unit in units
        ],
        settings=settings,
        prompt=prompt,
    )


async def _send_with_backoff(
    transport: Transport, request: ModelRequest
) -> ModelResponse:
    """Send `request`, honouring retry-after on 429 with an exponential floor.

    A rate limit is a wait instruction rather than a failure, so the file is
    retried instead of errored (AC-051, D-031).
    """
    attempts = 0
    delay = INITIAL_BACKOFF_SECONDS
    while True:
        try:
            return await transport.send(request)
        except RateLimitedError as limited:
            attempts += 1
            if attempts >= MAX_RATE_LIMIT_ATTEMPTS:
                raise
            await asyncio.sleep(max(limited.retry_after, delay))
            delay *= 2


def _interpret(
    response: ModelResponse, *, input_tokens: int, output_tokens: int
) -> _Outcome | None:
    """One response, read as a verdict — or None, meaning "worth one retry".

    The order matters. A provider-flagged refusal is a refusal whatever the body
    says; otherwise a body that validates is an answer; only a body that fails
    validation is examined for refusal language (AC-046, AC-047).
    """
    declined = _Outcome(
        abstained=Reason.MODEL_DECLINED,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    if response.stop_reason in REFUSAL_STOP_REASONS:
        return declined
    try:
        suggestion = NameSuggestion.from_json_text(response.text)
    except SchemaInvalidError:
        return declined if is_refusal(response) else None
    return _Outcome(
        suggestion=suggestion,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


async def _resolve(transport: Transport, request: ModelRequest) -> _Outcome:
    """One file's model verdict, including its one validation retry (AC-047)."""
    input_tokens = 0
    output_tokens = 0
    for _ in range(SCHEMA_ATTEMPTS):
        response = await _send_with_backoff(transport, request)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        outcome = _interpret(
            response, input_tokens=input_tokens, output_tokens=output_tokens
        )
        if outcome is not None:
            return outcome
    return _Outcome(
        errored=Reason.SCHEMA_INVALID,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _remember(
    content_hash: str,
    outcome: _Outcome,
    *,
    cache: Cache,
    settings: RunSettings,
    version: str,
) -> None:
    """Cache one suggestion the moment it arrives.

    Per answer rather than per run: a process killed mid-dispatch keeps every
    answer it had already paid for, which is what makes a resume free rather
    than merely correct (AC-042, AC-054).
    """
    if outcome.suggestion is None:
        return
    cache.put_suggestion(
        model_cache_key(
            content_hash=content_hash,
            model=settings.effective_model,
            resolution=settings.resolution,
            prompt_template_version=version,
        ),
        outcome.suggestion,
    )


def _attach(outcomes: dict[str, _Outcome], units: Sequence[_Unit]) -> None:
    """Give every file its verdict, including files sharing one content hash."""
    for unit in units:
        outcome = outcomes.get(unit.content_hash)
        if outcome is not None:
            unit.outcome = outcome


async def _dispatch_sync(
    units: Sequence[_Unit],
    *,
    transport: Transport,
    settings: RunSettings,
    prompt: str,
    cache: Cache,
    version: str,
) -> bool:
    """Run every pending request through a bounded pool. True if interrupted.

    The semaphore is the whole of AC-052, and the `interrupted` check inside it is
    the whole of AC-053's first half: a worker that has not started yet never
    starts, while one already awaiting an answer is left to finish.
    """
    requests = _requests_for(units, settings=settings, prompt=prompt)
    semaphore = asyncio.Semaphore(max(1, settings.concurrency))
    outcomes: dict[str, _Outcome] = {}
    interrupted = False
    unavailable: BackendUnavailableError | None = None

    async def worker(request: ModelRequest) -> None:
        nonlocal interrupted, unavailable
        async with semaphore:
            if interrupted:
                return
            try:
                outcome = await _resolve(transport, request)
                _remember(
                    request.custom_id,
                    outcome,
                    cache=cache,
                    settings=settings,
                    version=version,
                )
                outcomes[request.custom_id] = outcome
            except KeyboardInterrupt:
                interrupted = True
            except BackendUnavailableError as failure:
                # A backend that cannot be reached will not be reachable for the
                # next file either, so stop rather than fail 4000 files one by
                # one (AC-056).
                unavailable = failure
                interrupted = True

    await asyncio.gather(*(worker(request) for request in requests))
    _attach(outcomes, units)
    if unavailable is not None:
        raise unavailable
    return interrupted


def _outcome_from_entry(entry: BatchResultEntry) -> _Outcome:
    """One finished batch entry as a verdict. No retry: a retry is a new job."""
    if entry.status is not BatchEntryStatus.SUCCEEDED or entry.response is None:
        return _Outcome(errored=Reason.BATCH_ENTRY_FAILED)
    outcome = _interpret(
        entry.response,
        input_tokens=entry.response.input_tokens,
        output_tokens=entry.response.output_tokens,
    )
    if outcome is None:
        return _Outcome(
            errored=Reason.SCHEMA_INVALID,
            input_tokens=entry.response.input_tokens,
            output_tokens=entry.response.output_tokens,
        )
    return outcome


async def _await_jobs(
    transport: Transport, jobs: Sequence[JobRecord], *, settings: RunSettings
) -> list[JobRecord]:
    """Poll each job and return the ones that have not ended.

    The first poll is immediate and `--poll-interval` governs only the gap
    between polls, so collecting an already-finished job costs no wait. Without
    `--wait` a job is polled exactly once: the answer is a progress report, not
    something to block on, because a batch may legitimately take a day (D-054).
    """
    pending: list[JobRecord] = []
    for job in jobs:
        while True:
            state = await transport.job_state(job.job_id)
            if state is BatchJobState.ENDED:
                break
            if not settings.wait:
                pending.append(job)
                break
            await asyncio.sleep(settings.poll_interval)
    return pending


async def _dispatch_batch(
    units: Sequence[_Unit],
    *,
    transport: Transport,
    settings: RunSettings,
    prompt: str,
    cache: Cache,
    version: str,
    state: Path,
) -> _BatchStatus:
    """Submit what is not already submitted, then collect what has finished."""
    requests = _requests_for(units, settings=settings, prompt=prompt)
    store = JobStore(job_store_path(state))
    key = corpus_key(settings)
    wanted = {request.custom_id for request in requests}

    recorded = store.jobs_for(key)
    already = {custom_id for job in recorded for custom_id in job.custom_ids}
    fresh = [request for request in requests if request.custom_id not in already]

    if fresh:
        chunks = chunk_batch_requests(
            fresh,
            max_requests=model_module.MAX_BATCH_REQUESTS,
            max_payload_bytes=model_module.MAX_BATCH_PAYLOAD_BYTES,
        )
        for chunk in chunks:
            job_id = await transport.submit_batch(chunk)
            # Recorded before anything else can happen: an unrecorded job id is
            # a backlog that gets paid for twice (AC-072).
            store.record(
                JobRecord(
                    job_id=job_id,
                    corpus_key=key,
                    custom_ids=tuple(request.custom_id for request in chunk),
                    submitted_at=_timestamp(),
                )
            )

    relevant = [job for job in store.jobs_for(key) if wanted & set(job.custom_ids)]

    if fresh and not settings.wait:
        # The invocation that submits never collects, even if the job has already
        # ended: it prints the ids and the command that picks them up (AC-070).
        return _BatchStatus(jobs=relevant)

    try:
        pending = await _await_jobs(transport, relevant, settings=settings)
    except KeyboardInterrupt:
        # The jobs stay recorded and uncancelled, so a later invocation collects
        # rather than re-submits (AC-075).
        return _BatchStatus(jobs=relevant, interrupted=True)

    if pending:
        return _BatchStatus(jobs=relevant)

    outcomes: dict[str, _Outcome] = {}
    try:
        for job in relevant:
            for entry in await transport.job_results(job.job_id):
                outcome = _outcome_from_entry(entry)
                _remember(
                    entry.custom_id,
                    outcome,
                    cache=cache,
                    settings=settings,
                    version=version,
                )
                outcomes[entry.custom_id] = outcome
    except KeyboardInterrupt:
        return _BatchStatus(jobs=relevant, interrupted=True)

    _attach(outcomes, units)
    return _BatchStatus(jobs=relevant, collected=True)


# --------------------------------------------------------------------------- #
# Phase 3: propose
# --------------------------------------------------------------------------- #


def _proposed_name(unit: _Unit, suggestion: NameSuggestion) -> str | None:
    """The filename this suggestion implies, or None if it implies nothing."""
    slug = compose_slug(app=suggestion.app, subject=suggestion.subject)
    if not slug:
        return None
    name = unit.candidate.path.name
    return build_filename(
        date=date_prefix(name, birthtime=unit.candidate.facts.birthtime),
        slug=slug,
        extension=normalize_extension(name),
    )


def _existing_names(directory: Path, renaming: set[Path]) -> list[str]:
    """The entries in `directory` this run is not renaming, and so must not touch."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    return [entry.name for entry in entries if entry not in renaming]


def _resolve_names(proposed: Sequence[tuple[_Unit, str]]) -> dict[Path, str]:
    """Final unique names, resolved per directory (AC-023 – AC-026)."""
    by_directory: dict[Path, list[Proposal]] = {}
    for unit, name in proposed:
        by_directory.setdefault(unit.candidate.path.parent, []).append(
            Proposal(
                path=unit.candidate.path,
                content_hash=unit.content_hash,
                proposed_name=name,
            )
        )
    resolved: dict[Path, str] = {}
    for directory, proposals in by_directory.items():
        renaming = {proposal.path for proposal in proposals}
        resolved.update(
            resolve_collisions(
                proposals, existing_names=_existing_names(directory, renaming)
            )
        )
    return resolved


def _cost_of(outcome: _Outcome, settings: RunSettings) -> float:
    return realised_cost_usd(
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        model=settings.effective_model,
        endpoint=settings.endpoint,
        local=settings.local,
    )


def _record_for(
    unit: _Unit, *, names: dict[Path, str], settings: RunSettings
) -> PlanRecord:
    """One unit's plan record, given the names collision resolution settled on."""

    def verdict(
        action: Action,
        *,
        proposed_name: str | None = None,
        reason: str | None = None,
        cost: float = 0.0,
    ) -> PlanRecord:
        return PlanRecord(
            path=unit.candidate.path,
            content_hash=unit.content_hash,
            action=action,
            proposed_name=proposed_name,
            reason=reason,
            ocr_char_count=unit.ocr_char_count,
            est_cost_usd=cost,
        )

    outcome = unit.outcome
    if outcome is None:
        # Never dispatched: the run was interrupted, or its batch has not come
        # back yet. Not an error and not a verdict.
        return verdict(Action.SKIP)

    cost = _cost_of(outcome, settings)
    if outcome.abstained is not None:
        return verdict(Action.ABSTAIN, reason=outcome.abstained.value, cost=cost)
    if outcome.errored is not None or outcome.suggestion is None:
        reason = (outcome.errored or Reason.SCHEMA_INVALID).value
        return verdict(Action.ERROR, reason=reason, cost=cost)

    final = names.get(unit.candidate.path)
    if final is None:
        # The model answered, but with nothing that slugifies into a name.
        return verdict(Action.ERROR, reason=Reason.SCHEMA_INVALID.value, cost=cost)
    if final == unit.candidate.path.name:
        return verdict(Action.SKIP, reason=Reason.ALREADY_RENAMED.value, cost=cost)
    return verdict(Action.RENAME, proposed_name=final, cost=cost)


def _build_records(
    candidates: Sequence[Candidate],
    *,
    units: Sequence[_Unit],
    unreadable: dict[Path, PlanRecord],
    not_sampled: set[Path],
    settings: RunSettings,
) -> list[PlanRecord]:
    """One record per inspected file, in enumeration order (AC-029)."""
    proposed: list[tuple[_Unit, str]] = []
    for unit in units:
        suggestion = unit.outcome.suggestion if unit.outcome is not None else None
        if suggestion is None:
            continue
        name = _proposed_name(unit, suggestion)
        if name is not None:
            proposed.append((unit, name))
    names = _resolve_names(proposed)

    by_path = {unit.candidate.path: unit for unit in units}
    records: list[PlanRecord] = []
    for candidate in candidates:
        if candidate.path in unreadable:
            records.append(unreadable[candidate.path])
            continue
        if not candidate.included:
            records.append(
                PlanRecord(
                    path=candidate.path,
                    content_hash=None,
                    action=Action.SKIP,
                    proposed_name=None,
                    reason=None
                    if candidate.excluded is None
                    else candidate.excluded.value,
                    ocr_char_count=0,
                    est_cost_usd=0.0,
                )
            )
            continue
        if candidate.path in not_sampled:
            records.append(
                PlanRecord(
                    path=candidate.path,
                    content_hash=None,
                    action=Action.SKIP,
                    proposed_name=None,
                    reason=Reason.NOT_SAMPLED.value,
                    ocr_char_count=0,
                    est_cost_usd=0.0,
                )
            )
            continue
        records.append(
            _record_for(by_path[candidate.path], names=names, settings=settings)
        )
    return records


# --------------------------------------------------------------------------- #
# Phase 4: apply
# --------------------------------------------------------------------------- #


def _apply(
    records: Sequence[PlanRecord], *, state: Path
) -> tuple[list[PlanRecord], bool]:
    """Journal then rename, in that order, until done or interrupted.

    The journal write and its fsync happen before `rename(2)` for every single
    file, which is what makes undo trustworthy: a crash can leave a record for a
    rename that never happened, never a rename with no record (AC-035, D-020).
    """
    journal = Journal(journal_path(state))
    run_id = uuid.uuid4().hex
    applied: list[PlanRecord] = []
    interrupted = False

    for record in records:
        if record.action is not Action.RENAME or record.proposed_name is None:
            applied.append(record)
            continue
        if interrupted:
            applied.append(
                replace(record, action=Action.SKIP, proposed_name=None, reason=None)
            )
            continue
        destination = record.path.parent / record.proposed_name
        try:
            journal.append(
                JournalRecord(
                    run_id=run_id,
                    ts=_timestamp(),
                    old_path=record.path,
                    new_path=destination,
                    content_hash=record.content_hash or "",
                )
            )
            rename_module.rename_file(record.path, destination)
        except KeyboardInterrupt:
            # Whatever was already renamed stays renamed and stays journalled;
            # this file was not, and the record for it is reconcilable (AC-053).
            interrupted = True
            applied.append(
                replace(record, action=Action.SKIP, proposed_name=None, reason=None)
            )
            continue
        except OSError as failure:
            applied.append(
                replace(
                    record,
                    action=Action.ERROR,
                    proposed_name=None,
                    reason=f"{Reason.RENAME_FAILED.value}: {failure.strerror or failure}",
                )
            )
            continue
        applied.append(record)

    return applied, interrupted


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _total_cost(records: Iterable[PlanRecord]) -> float:
    """The realised cost, counted once per distinct content hash (AC-068)."""
    counted: set[str] = set()
    total = 0.0
    for record in records:
        if record.content_hash is not None:
            if record.content_hash in counted:
                continue
            counted.add(record.content_hash)
        total += record.est_cost_usd
    return total


def _collect_command(settings: RunSettings) -> str:
    """The identical command, spelled out, because re-running it is the resume."""
    parts = ["shotname", "run", str(settings.root)]
    if settings.apply:
        parts.append("--apply")
    if settings.all_images:
        parts.append("--all-images")
    if settings.recursive:
        parts.append("--recursive")
    if settings.materialize:
        parts.append("--materialize")
    if settings.model != RunSettings(root=settings.root).model:
        parts.extend(["--model", settings.model])
    parts.extend(["--resolution", settings.resolution.value])
    if settings.prompt_template is not None:
        parts.extend(["--prompt-template", str(settings.prompt_template)])
    return " ".join(parts)


def _announce_pending(status: _BatchStatus, settings: RunSettings) -> None:
    typer.echo(f"{len(status.jobs)} batch job(s) in flight:")
    for job in status.jobs:
        typer.echo(f"  {job.job_id}")
    typer.echo("Nothing was renamed. Collect the results by re-running:")
    typer.echo(f"  {_collect_command(settings)}")


def _report(records: Sequence[PlanRecord], *, settings: RunSettings) -> None:
    typer.echo(
        format_report(
            build_report(
                records,
                settings=settings,
                total_cost_usd=_total_cost(records),
                seed=settings.seed,
            )
        )
    )


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def _replay(settings: RunSettings, state: Path) -> int:
    """Execute exactly the renames a saved plan records, and nothing else (AC-034)."""
    assert settings.plan is not None
    records = read_plan(settings.plan)
    renames = [
        record
        for record in records
        if record.action is Action.RENAME and record.proposed_name is not None
    ]
    if not settings.renames:
        typer.echo(
            f"{settings.plan} records {len(renames)} renames; pass --apply to run them"
        )
        _report(records, settings=settings)
        return 0
    applied, interrupted = _apply(renames, state=state)
    _report(applied, settings=settings)
    if interrupted:
        typer.echo("interrupted: stopped after the renames already journalled")
        return EXIT_INTERRUPTED
    return 0


def execute_run(settings: RunSettings, deps: Deps) -> int:
    """One `shotname run` invocation, start to finish. Returns the exit code."""
    if settings.print_prompt:
        typer.echo(load_prompt(settings.prompt_template))
        return 0

    environment = resolve_run_environment(settings)
    _preflight(settings, environment)
    state = environment.state_dir

    if settings.plan is not None:
        return _replay(settings, state)

    candidates = _discover(settings, deps)
    typer.echo(format_discovery_summary(summarize(candidates)))

    included = [candidate for candidate in candidates if candidate.included]
    selected = included
    not_sampled: set[Path] = set()
    if settings.sample is not None:
        selected = select_sample(included, settings.sample, seed=settings.seed)
        chosen = {candidate.path for candidate in selected}
        not_sampled = {
            candidate.path for candidate in included if candidate.path not in chosen
        }

    prompt = load_prompt(settings.prompt_template)
    version = prompt_version(settings.prompt_template)

    cache = Cache(cache_path(state))
    try:
        units, unreadable = _prepare(
            selected, settings=settings, deps=deps, cache=cache, version=version
        )
        pending = [unit for unit in units if unit.outcome is None]

        estimate = estimate_cost(file_count=len(pending), settings=settings)
        typer.echo(estimate.format())
        if (
            settings.max_cost_usd is not None
            and estimate.total_usd > settings.max_cost_usd
        ):
            raise UsageError(
                f"estimated ${estimate.total_usd:.4f} exceeds --max-cost "
                f"${settings.max_cost_usd:.4f}; nothing was sent and nothing was renamed"
            )

        interrupted = False
        status: _BatchStatus | None = None
        if pending:
            if settings.endpoint is Endpoint.SYNC:
                interrupted = asyncio.run(
                    _dispatch_sync(
                        pending,
                        transport=deps.transport,
                        settings=settings,
                        prompt=prompt,
                        cache=cache,
                        version=version,
                    )
                )
            else:
                status = asyncio.run(
                    _dispatch_batch(
                        pending,
                        transport=deps.transport,
                        settings=settings,
                        prompt=prompt,
                        cache=cache,
                        version=version,
                        state=state,
                    )
                )
                interrupted = status.interrupted
    finally:
        cache.close()

    records = _build_records(
        candidates,
        units=units,
        unreadable=unreadable,
        not_sampled=not_sampled,
        settings=settings,
    )

    awaiting_batch = status is not None and not status.collected
    if settings.renames and not awaiting_batch:
        records, rename_interrupted = _apply(records, state=state)
        interrupted = interrupted or rename_interrupted

    write_plan(records, plan_path(state))

    if awaiting_batch and status is not None:
        _announce_pending(status, settings)
    _report(records, settings=settings)

    if interrupted:
        typer.echo(
            "interrupted: stopped dispatching new work; "
            "every rename that started was journalled and completed"
        )
        return EXIT_INTERRUPTED
    return 0
