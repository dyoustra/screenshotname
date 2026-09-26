"""S-04 — Backend interface, cost accounting, and failure modes.

Covers AC-049 through AC-057.

This slice is about *reaching a model* and *failing to*, so almost every test
drives the CLI end to end with the transport replaced. Two exceptions keep the
real Ollama transport in place: AC-055 and AC-056 are assertions about genuine
outbound sockets, and a fake transport would assert nothing.

SIGINT is delivered by raising `KeyboardInterrupt` from inside the transport
rather than by signalling the process: a `CliRunner` invocation shares its
process with the test session, and an injected interrupt lands at a
deterministic point in the run instead of a racy one.
"""

from __future__ import annotations

import errno
import socket
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from shotname.errors import EXIT_INTERRUPTED, EXIT_PERMISSION, EXIT_USAGE
from shotname.journal import Journal, journal_path
from shotname.plan import Action
from shotname.settings import API_KEY_ENV_VAR, DEFAULT_CONCURRENCY, Endpoint
from tests.conftest import Inject
from tests.support.cli import actions_by_name, output_of, plan_records, run_cli
from tests.support.corpus import build_bulk_corpus, build_corpus
from tests.support.fakes import (
    FakeOcr,
    FakeTransport,
    OverrideProbe,
    interrupt_at,
    rate_limit_at,
)
from tests.support.faults import fail_nth_rename
from tests.support.fsutil import snapshot_dir


def _free_port() -> int:
    """A port nothing is listening on, so a connection attempt is refused."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    return port


# --------------------------------------------------------------------------- #
# AC-049
# --------------------------------------------------------------------------- #


def test_ac049_eperm_exits_3_with_the_full_disk_access_path(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-049: TCC denial is a message, not a traceback."""
    probe = OverrideProbe(
        walk_error=PermissionError(errno.EPERM, "Operation not permitted")
    )
    inject(probe=probe, ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root)
    output = output_of(result)

    assert EXIT_PERMISSION == 3
    assert result.exit_code == EXIT_PERMISSION
    assert "Full Disk Access" in output
    assert "System Settings" in output
    assert "Traceback" not in output


# --------------------------------------------------------------------------- #
# AC-050
# --------------------------------------------------------------------------- #


def test_ac050_missing_api_key_exits_2_before_enumerating(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-050: no key and no --local fails preflight, naming the variable."""
    build_corpus(corpus_root)
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    probe = OverrideProbe()
    inject(probe=probe, ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root)

    assert EXIT_USAGE == 2
    assert result.exit_code == EXIT_USAGE
    assert API_KEY_ENV_VAR in output_of(result)
    assert probe.walked == [], "files were enumerated before the key was checked"


def test_ac050_local_backend_needs_no_api_key(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-050: the check is conditional on the cloud backend being selected."""
    corpus = build_corpus(corpus_root)
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--local")

    assert result.exit_code == 0
    assert API_KEY_ENV_VAR not in output_of(result)


# --------------------------------------------------------------------------- #
# AC-051
# --------------------------------------------------------------------------- #


def test_ac051_http_429_is_retried_with_backoff_honouring_retry_after(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-051: a 429-then-200 sequence completes with no file in error."""
    retry_after = 0.05
    corpus = build_corpus(corpus_root)
    transport = FakeTransport(on_send=rate_limit_at(0, retry_after=retry_after))
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=transport)

    started = time.monotonic()
    result = run_cli(runner, corpus_root, "--sync")
    elapsed = time.monotonic() - started

    actions = actions_by_name(plan_records())

    assert result.exit_code == 0
    assert Action.ERROR not in actions.values()
    assert elapsed >= retry_after, "retry-after was not waited out"

    attempts = Counter(request.custom_id for request in transport.sync_requests)
    assert max(attempts.values()) == 2, "the rate-limited request was not retried once"


# --------------------------------------------------------------------------- #
# AC-052
# --------------------------------------------------------------------------- #


def test_ac052_concurrency_flag_bounds_in_flight_requests(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-052: --concurrency 3 means never four at once."""
    build_bulk_corpus(corpus_root, 12)
    transport = FakeTransport(delay=0.01)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--sync", "--concurrency", "3")

    assert result.exit_code == 0
    assert len(transport.sync_requests) == 12
    assert transport.max_in_flight <= 3


def test_ac052_requests_actually_overlap(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-052: the limit bounds a real pool, not a sequential loop."""
    build_bulk_corpus(corpus_root, 12)
    transport = FakeTransport(delay=0.02)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    run_cli(runner, corpus_root, "--sync", "--concurrency", "4")

    assert transport.max_in_flight >= 2


def test_ac052_default_concurrency_is_8(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-052: the default limit is 8 in-flight requests."""
    assert DEFAULT_CONCURRENCY == 8

    build_bulk_corpus(corpus_root, 20)
    transport = FakeTransport(delay=0.01)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    run_cli(runner, corpus_root, "--sync")

    assert transport.max_in_flight <= DEFAULT_CONCURRENCY


# --------------------------------------------------------------------------- #
# AC-053
# --------------------------------------------------------------------------- #


def test_ac053_sigint_stops_dispatching_new_work_and_exits_130(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-053: an interrupt mid-dispatch halts dispatch, summarises, exits 130."""
    build_bulk_corpus(corpus_root, 10)
    transport = FakeTransport(on_send=interrupt_at(3))
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--apply", "--sync")
    output = output_of(result)

    assert EXIT_INTERRUPTED == 130
    assert result.exit_code == EXIT_INTERRUPTED
    assert 4 <= len(transport.sync_requests) < 10, (
        "dispatch either stopped too early or kept going past the interrupt"
    )
    assert "interrupt" in output.lower()


def test_ac053_sigint_while_renaming_journals_the_renames_that_completed(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-053: in-flight renames complete and are journalled before exiting."""
    build_bulk_corpus(corpus_root, 6)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    renamed = fail_nth_rename(monkeypatch, after=2, error=KeyboardInterrupt())

    result = run_cli(runner, corpus_root, "--apply", "--sync")

    assert result.exit_code == EXIT_INTERRUPTED
    assert len(renamed) == 2

    journalled = {record.new_path for record in Journal(journal_path()).records()}
    for _, new_path in renamed:
        assert new_path in journalled
        assert new_path.exists()


# --------------------------------------------------------------------------- #
# AC-054
# --------------------------------------------------------------------------- #


def test_ac054_run_resumes_after_sigint_without_reprocessing(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-054: the identical command picks up where it stopped."""
    paths = build_bulk_corpus(corpus_root, 6)
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=FakeTransport())
    renamed = fail_nth_rename(monkeypatch, after=2, error=KeyboardInterrupt())

    first = run_cli(runner, corpus_root, "--apply", "--sync")
    assert first.exit_code == EXIT_INTERRUPTED
    done = {new_path for _, new_path in renamed}
    assert len(done) == 2

    monkeypatch.undo()
    second_ocr = FakeOcr()
    inject(probe=OverrideProbe(), ocr=second_ocr, transport=FakeTransport())
    second = run_cli(runner, corpus_root, "--apply", "--sync")

    assert second.exit_code == 0
    assert not (set(second_ocr.calls) & done), "a completed file was re-OCRed"
    for path in done:
        assert path.exists()
    for path in paths:
        assert not path.exists(), f"{path.name} was never renamed"


# --------------------------------------------------------------------------- #
# AC-055
# --------------------------------------------------------------------------- #


def test_ac055_local_run_opens_no_socket_other_than_the_ollama_host(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-055: --local means nothing leaves the machine for anywhere else."""
    corpus = build_corpus(corpus_root)
    port = _free_port()
    attempted: list[Any] = []

    def recording_connect(self: socket.socket, address: Any) -> None:
        attempted.append(address)
        raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")

    monkeypatch.setattr(socket.socket, "connect", recording_connect)
    # OCR is stubbed; the transport is deliberately the real Ollama one, so the
    # sockets observed here are the ones production would open.
    inject(probe=corpus.probe(), ocr=FakeOcr())

    run_cli(
        runner,
        corpus_root,
        "--local",
        "--ollama-host",
        f"http://127.0.0.1:{port}",
    )

    endpoints = {addr[:2] for addr in attempted if isinstance(addr, tuple)}
    assert endpoints <= {("127.0.0.1", port)}, f"unexpected outbound socket: {endpoints}"


# --------------------------------------------------------------------------- #
# AC-056
# --------------------------------------------------------------------------- #


def test_ac056_unreachable_ollama_host_fails_naming_the_host(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-056: an unreachable local backend renames nothing and says where."""
    corpus = build_corpus(corpus_root)
    port = _free_port()
    host = f"http://127.0.0.1:{port}"
    before = snapshot_dir(corpus.root)

    inject(probe=corpus.probe(), ocr=FakeOcr())

    result = run_cli(runner, corpus_root, "--apply", "--local", "--ollama-host", host)
    output = output_of(result)

    assert result.exit_code != 0
    assert f"127.0.0.1:{port}" in output
    assert snapshot_dir(corpus.root) == before


# --------------------------------------------------------------------------- #
# AC-057
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend_flags", [(), ("--local",)])
def test_ac057_the_same_pipeline_passes_against_both_backends(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    backend_flags: tuple[str, ...],
) -> None:
    """AC-057: only the backend selection flag differs between these runs."""
    corpus = build_corpus(corpus_root)
    transport = FakeTransport()
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--wait", *backend_flags)
    records = plan_records()

    assert result.exit_code == 0
    assert {record.path.name for record in records} >= {
        path.name for path in corpus.included
    }
    assert Action.RENAME in {record.action for record in records}


def test_ac057_local_backend_always_dispatches_synchronously(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-057: the local backend never reaches for the Batch API."""
    corpus = build_corpus(corpus_root)
    transport = FakeTransport()
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--local")

    assert result.exit_code == 0
    assert transport.endpoint_log
    assert set(transport.endpoint_log) == {Endpoint.SYNC}
