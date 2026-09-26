"""Instrumented stand-ins for the three seams `shotname` reaches the world through.

`shotname.deps.Deps` bundles exactly three collaborators — the filesystem probe,
the OCR engine, and the model transport — and `shotname.deps.default_deps` is the
one place they are constructed. Tests replace that function, so every fake in
this module implements a protocol that production code also implements for real.

The transport is deliberately a single protocol covering the synchronous
endpoint, the Batch API, and Ollama. That is what makes AC-057 ("both backends
behind the same interface") checkable and what lets AC-065 assert *which*
endpoint a request reached.
"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from shotname.deps import Deps
from shotname.discovery import SF_DATALESS, FileFacts, FsProbe, RealFsProbe
from shotname.errors import RateLimitedError
from shotname.model import (
    BatchEntryStatus,
    BatchJobState,
    BatchResultEntry,
    ModelRequest,
    ModelResponse,
    NameSuggestion,
    Transport,
)
from shotname.ocr import Ocr, OcrLine, OcrResult
from shotname.settings import Endpoint

DEFAULT_OCR_TEXT = "Slack\n#incidents\nStripe outage thread\npayments are failing"

STOP_REASON_END_TURN = "end_turn"
STOP_REASON_REFUSAL = "refusal"


class SimulatedKill(BaseException):
    """Stands in for SIGKILL.

    Deriving from `BaseException` rather than `Exception` is the point: a
    well-behaved `shotname` run must not swallow it in a broad `except
    Exception`, so whatever durability state survives on disk is the same state
    that would survive a real kill -9.
    """


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #


def ocr_result(text: str, *, confidence: float = 0.95) -> OcrResult:
    """Build an `OcrResult` whose lines run top-to-bottom down the image.

    The bounding boxes matter: the app-first rule (AC-019) depends on title-bar
    text — the topmost line — outranking body text, so the first line of `text`
    is placed at the top of the frame.
    """
    parts = text.splitlines() or [text]
    lines = tuple(
        OcrLine(
            text=part,
            confidence=confidence,
            bbox=(0.0, max(0.0, 0.95 - 0.08 * index), 1.0, 0.05),
        )
        for index, part in enumerate(parts)
    )
    return OcrResult(lines=lines)


@dataclass
class FakeOcr:
    """Records every recognition so resume criteria can assert zero re-OCR."""

    results: dict[Path, OcrResult] = field(default_factory=dict)
    default_text: str = DEFAULT_OCR_TEXT
    calls: list[Path] = field(default_factory=list)

    def recognize(self, path: Path) -> OcrResult:
        self.calls.append(path)
        canned = self.results.get(path)
        if canned is not None:
            return canned
        return ocr_result(self.default_text)


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #


def suggestion_text(
    app: str | None = "Slack",
    subject: str = "stripe outage thread",
    confidence: float = 0.9,
) -> str:
    return NameSuggestion(app=app, subject=subject, confidence=confidence).to_json_text()


def _default_text_for(request: ModelRequest) -> str:
    return suggestion_text()


def _default_stop_reason_for(request: ModelRequest) -> str:
    return STOP_REASON_END_TURN


def _default_entry_status_for(custom_id: str) -> BatchEntryStatus:
    return BatchEntryStatus.SUCCEEDED


def interrupt_at(target: int) -> Callable[[int, ModelRequest], None]:
    """An `on_send` hook that raises `KeyboardInterrupt`: an injected SIGINT."""

    def hook(index: int, request: ModelRequest) -> None:
        if index == target:
            raise KeyboardInterrupt

    return hook


def kill_at(target: int) -> Callable[[int, ModelRequest], None]:
    """An `on_send` hook that raises `SimulatedKill`: an injected SIGKILL."""

    def hook(index: int, request: ModelRequest) -> None:
        if index == target:
            raise SimulatedKill(f"killed before request {target}")

    return hook


def rate_limit_at(
    target: int, *, retry_after: float = 0.05
) -> Callable[[int, ModelRequest], None]:
    """An `on_send` hook that answers one request with HTTP 429."""

    def hook(index: int, request: ModelRequest) -> None:
        if index == target:
            raise RateLimitedError(retry_after=retry_after)

    return hook


def interrupt_poll_at(target: int) -> Callable[[int, str], None]:
    """An `on_poll` hook that raises `KeyboardInterrupt` while waiting on a job."""

    def hook(poll_index: int, job_id: str) -> None:
        if poll_index == target:
            raise KeyboardInterrupt

    return hook


class FakeTransport:
    """Fakes both the synchronous endpoint and the Batch API.

    The hook parameters are how individual criteria inject their scenario:
    `on_send` can raise `RateLimitedError` (AC-051), `KeyboardInterrupt`
    (AC-053) or `SimulatedKill` (AC-042); `entry_status_for` marks a batch
    entry errored or expired (AC-074); `poll_states` keeps a job in progress
    (AC-072); `on_poll` interrupts a poll loop (AC-075).
    """

    def __init__(
        self,
        *,
        text_for: Callable[[ModelRequest], str] | None = None,
        stop_reason_for: Callable[[ModelRequest], str] | None = None,
        entry_status_for: Callable[[str], BatchEntryStatus] | None = None,
        on_send: Callable[[int, ModelRequest], None] | None = None,
        on_poll: Callable[[int, str], None] | None = None,
        poll_states: Sequence[BatchJobState] | None = None,
        delay: float = 0.0,
    ) -> None:
        self._text_for = text_for if text_for is not None else _default_text_for
        self._stop_reason_for = (
            stop_reason_for if stop_reason_for is not None else _default_stop_reason_for
        )
        self._entry_status_for = (
            entry_status_for
            if entry_status_for is not None
            else _default_entry_status_for
        )
        self._on_send = on_send
        self._on_poll = on_poll
        self._poll_states = None if poll_states is None else list(poll_states)
        self._delay = delay
        self._jobs: dict[str, list[ModelRequest]] = {}
        self._poll_counts: dict[str, int] = {}

        self.endpoint_log: list[Endpoint] = []
        self.sync_requests: list[ModelRequest] = []
        self.batch_submissions: list[list[ModelRequest]] = []
        self.polls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    # -- observation ------------------------------------------------------- #

    @property
    def all_requests(self) -> list[ModelRequest]:
        batched = [
            request for submission in self.batch_submissions for request in submission
        ]
        return [*self.sync_requests, *batched]

    @property
    def custom_ids(self) -> list[str]:
        return [request.custom_id for request in self.all_requests]

    def requests_for(self, custom_id: str) -> list[ModelRequest]:
        return [r for r in self.all_requests if r.custom_id == custom_id]

    @property
    def job_ids(self) -> list[str]:
        return list(self._jobs)

    # -- Transport protocol ------------------------------------------------ #

    async def send(self, request: ModelRequest) -> ModelResponse:
        self.endpoint_log.append(Endpoint.SYNC)
        index = len(self.sync_requests)
        self.sync_requests.append(request)
        if self._on_send is not None:
            self._on_send(index, request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
        finally:
            self.in_flight -= 1
        return self._response(request)

    async def submit_batch(self, requests: Sequence[ModelRequest]) -> str:
        self.endpoint_log.append(Endpoint.BATCH)
        job_id = f"msgbatch_{len(self._jobs):03d}"
        self._jobs[job_id] = list(requests)
        self.batch_submissions.append(list(requests))
        return job_id

    async def job_state(self, job_id: str) -> BatchJobState:
        self.polls.append(job_id)
        seen = self._poll_counts.get(job_id, 0)
        self._poll_counts[job_id] = seen + 1
        if self._on_poll is not None:
            self._on_poll(seen, job_id)
        if self._poll_states is None:
            return BatchJobState.ENDED
        return self._poll_states[min(seen, len(self._poll_states) - 1)]

    async def job_results(self, job_id: str) -> list[BatchResultEntry]:
        entries: list[BatchResultEntry] = []
        for request in self._jobs.get(job_id, []):
            status = self._entry_status_for(request.custom_id)
            succeeded = status is BatchEntryStatus.SUCCEEDED
            entries.append(
                BatchResultEntry(
                    custom_id=request.custom_id,
                    status=status,
                    response=self._response(request) if succeeded else None,
                    error=None if succeeded else str(status),
                )
            )
        return entries

    # -- internals --------------------------------------------------------- #

    def _response(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            custom_id=request.custom_id,
            text=self._text_for(request),
            stop_reason=self._stop_reason_for(request),
            input_tokens=1200,
            output_tokens=40,
        )


# --------------------------------------------------------------------------- #
# Filesystem probes
# --------------------------------------------------------------------------- #


@dataclass
class OverrideProbe:
    """A real probe with the macOS-only attributes forced per path.

    `kMDItemIsScreenCapture` and `SF_DATALESS` cannot be set on an ordinary
    temporary file, so the criteria that depend on them (AC-002, AC-006,
    AC-007) drive this wrapper instead. `sniffed` exists so AC-006 can assert
    that a dataless file was never opened.
    """

    base: FsProbe = field(default_factory=RealFsProbe)
    screen_capture: set[Path] = field(default_factory=set)
    not_screen_capture: set[Path] = field(default_factory=set)
    dataless: set[Path] = field(default_factory=set)
    walk_error: OSError | None = None
    walked: list[Path] = field(default_factory=list)
    sniffed: list[Path] = field(default_factory=list)

    def walk(self, root: Path, *, recursive: bool) -> Iterator[Path]:
        if self.walk_error is not None:
            raise self.walk_error
        for path in self.base.walk(root, recursive=recursive):
            self.walked.append(path)
            yield path

    def facts(self, path: Path) -> FileFacts:
        base = self.base.facts(path)
        flags = base.st_flags | SF_DATALESS if path in self.dataless else base.st_flags
        if path in self.screen_capture:
            is_screen_capture = True
        elif path in self.not_screen_capture:
            is_screen_capture = False
        else:
            is_screen_capture = base.is_screen_capture
        return FileFacts(
            path=base.path,
            size=base.size,
            st_flags=flags,
            mtime=base.mtime,
            birthtime=base.birthtime,
            inode=base.inode,
            is_symlink=base.is_symlink,
            is_dir=base.is_dir,
            is_screen_capture=is_screen_capture,
        )

    def sniff_format(self, path: Path) -> str | None:
        self.sniffed.append(path)
        return self.base.sniff_format(path)


@dataclass
class SyntheticProbe:
    """Yields `count` plausible screenshot paths without touching the disk.

    AC-009 compares peak memory for a 500-file corpus against a 50,000-file
    one; materialising 50,000 real files would make the test about the
    filesystem rather than about the enumerator.
    """

    count: int

    def walk(self, root: Path, *, recursive: bool) -> Iterator[Path]:
        for index in range(self.count):
            yield root / f"Screenshot 2026-01-14 at 3.42.11 PM-{index}.png"

    def facts(self, path: Path) -> FileFacts:
        return FileFacts(
            path=path,
            size=2048,
            st_flags=0,
            mtime=1_768_000_000.0,
            birthtime=1_768_000_000.0,
            inode=zlib.crc32(path.name.encode()),
            is_symlink=False,
            is_dir=False,
            is_screen_capture=True,
        )

    def sniff_format(self, path: Path) -> str | None:
        return "png"


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def make_deps(
    *,
    probe: FsProbe | None = None,
    ocr: Ocr | None = None,
    transport: Transport | None = None,
) -> Deps:
    return Deps(
        probe=RealFsProbe() if probe is None else probe,
        ocr=FakeOcr() if ocr is None else ocr,
        transport=FakeTransport() if transport is None else transport,
    )
