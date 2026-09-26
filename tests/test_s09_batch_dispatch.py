"""S-09 — Batch dispatch and request construction.

Covers AC-065, AC-066, AC-067, AC-068, AC-069.

Everything that is true of a request *before it leaves*: which endpoint it goes
to, the halved estimate, the custom_id rule that also dedupes identical bytes,
and chunking to stay under the provider's per-job limits. What happens to a job
after it exists is S-10.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from shotname.batch import chunk_batch_requests
from shotname.cost import BATCH_DISCOUNT, estimate_cost
from shotname.hashing import content_hash
from shotname.model import (
    MAX_BATCH_PAYLOAD_BYTES,
    MAX_BATCH_REQUESTS,
    ModelRequest,
    RequestInput,
    build_model_requests,
)
from shotname.prompt import BUILTIN_PROMPT
from shotname.settings import DEFAULT_MODEL, Endpoint, RunSettings
from tests.conftest import Inject
from tests.support.cli import output_of, plan_records, run_cli
from tests.support.corpus import build_bulk_corpus, build_corpus, build_duplicate_pair
from tests.support.fakes import FakeOcr, FakeTransport, OverrideProbe


def _request(index: int, *, payload: int = 1024) -> ModelRequest:
    return ModelRequest(
        custom_id=f"{index:064x}",
        model=DEFAULT_MODEL,
        prompt="describe this capture",
        image_bytes=b"\x00" * payload,
        image_media_type="image/png",
        ocr_text="slack",
    )


# --------------------------------------------------------------------------- #
# AC-065
# --------------------------------------------------------------------------- #


def test_ac065_full_cloud_run_uses_the_batch_api(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-065: a run without --sample dispatches through the Batch API."""
    corpus = build_corpus(corpus_root)
    transport = FakeTransport()
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--wait")

    assert result.exit_code == 0
    assert transport.endpoint_log
    assert set(transport.endpoint_log) == {Endpoint.BATCH}


def test_ac065_sample_run_uses_the_synchronous_endpoint(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-065: --sample stays synchronous so prompt iteration is not a wait."""
    corpus = build_corpus(corpus_root)
    transport = FakeTransport()
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--sample", "2")

    assert result.exit_code == 0
    assert transport.endpoint_log
    assert set(transport.endpoint_log) == {Endpoint.SYNC}


def test_ac065_settings_endpoint_reflects_the_dispatch_decision() -> None:
    """AC-065: the decision is a property of the run settings, not a side effect."""
    root = Path(".")
    assert RunSettings(root=root).endpoint is Endpoint.BATCH
    assert RunSettings(root=root, sample=30).endpoint is Endpoint.SYNC


# --------------------------------------------------------------------------- #
# AC-066
# --------------------------------------------------------------------------- #


def test_ac066_sync_flag_forces_the_synchronous_endpoint_for_a_full_run(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-066: --sync opts a full run out of the Batch API."""
    corpus = build_corpus(corpus_root)
    transport = FakeTransport()
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--sync")

    assert result.exit_code == 0
    assert set(transport.endpoint_log) == {Endpoint.SYNC}


def test_ac066_sync_and_local_together_are_accepted(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-066: --sync --local is redundant, not a conflict."""
    corpus = build_corpus(corpus_root)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--sync", "--local")

    assert result.exit_code == 0
    assert "conflict" not in output_of(result).lower()


# --------------------------------------------------------------------------- #
# AC-067
# --------------------------------------------------------------------------- #


def test_ac067_batch_estimate_is_half_the_synchronous_estimate() -> None:
    """AC-067: the estimate reflects the Batch API's 50% discount."""
    root = Path(".")
    batch = estimate_cost(file_count=4000, settings=RunSettings(root=root))
    synchronous = estimate_cost(
        file_count=4000, settings=RunSettings(root=root, sync=True)
    )

    assert BATCH_DISCOUNT == 0.5
    assert synchronous.total_usd > 0
    assert batch.total_usd == pytest.approx(synchronous.total_usd * BATCH_DISCOUNT)


def test_ac067_the_discount_is_not_applied_on_the_sample_path() -> None:
    """AC-067: --sample is synchronous, so it is priced at the full rate."""
    root = Path(".")
    sample = estimate_cost(file_count=30, settings=RunSettings(root=root, sample=30))
    synchronous = estimate_cost(
        file_count=30, settings=RunSettings(root=root, sync=True)
    )

    assert sample.total_usd == pytest.approx(synchronous.total_usd)


# --------------------------------------------------------------------------- #
# AC-068
# --------------------------------------------------------------------------- #


def test_ac068_custom_id_is_the_content_hash(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-068: every batch request is keyed by the hash of the file it names."""
    paths = build_bulk_corpus(corpus_root, 4)
    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--wait")

    assert result.exit_code == 0
    assert set(transport.custom_ids) == {content_hash(path) for path in paths}


def test_ac068_identical_bytes_produce_exactly_one_request(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-068: two copies of the same capture are one unit of work."""
    first, second = build_duplicate_pair(corpus_root)
    assert content_hash(first) == content_hash(second)

    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    assert len(transport.all_requests) == 1
    assert len(plan_records()) == 2


def test_ac068_request_builder_dedupes_by_content_hash(tmp_path: Path) -> None:
    """AC-068: the deduplication lives in request construction."""
    shared = "a3f9c1" + "0" * 26
    inputs = [
        RequestInput(
            path=tmp_path / "one.png",
            content_hash=shared,
            image_bytes=b"\x89PNG",
            ocr_text="slack",
        ),
        RequestInput(
            path=tmp_path / "two.png",
            content_hash=shared,
            image_bytes=b"\x89PNG",
            ocr_text="slack",
        ),
    ]

    requests = build_model_requests(
        inputs, settings=RunSettings(root=tmp_path), prompt=BUILTIN_PROMPT
    )

    assert [request.custom_id for request in requests] == [shared]


# --------------------------------------------------------------------------- #
# AC-069
# --------------------------------------------------------------------------- #


def test_ac069_provider_limits_are_the_real_ones() -> None:
    """AC-069: the caps chunking respects are the Batch API's documented limits."""
    assert MAX_BATCH_REQUESTS == 100_000
    assert MAX_BATCH_PAYLOAD_BYTES == 256 * 1024 * 1024


def test_ac069_chunking_respects_the_request_count_limit() -> None:
    """AC-069: no chunk exceeds the per-job request count."""
    requests = [_request(index) for index in range(10)]

    chunks = chunk_batch_requests(requests, max_requests=3, max_payload_bytes=10**9)

    assert [len(chunk) for chunk in chunks] == [3, 3, 3, 1]
    assert [request for chunk in chunks for request in chunk] == requests


def test_ac069_chunking_respects_the_payload_size_limit() -> None:
    """AC-069: no chunk exceeds the per-job payload size."""
    requests = [_request(index, payload=1000) for index in range(10)]
    limit = 2600

    chunks = chunk_batch_requests(requests, max_requests=10**6, max_payload_bytes=limit)

    assert len(chunks) > 1
    for chunk in chunks:
        assert sum(request.payload_size_bytes() for request in chunk) <= limit
    assert [request for chunk in chunks for request in chunk] == requests


def test_ac069_chunking_respects_both_limits_at_once() -> None:
    """AC-069: a corpus exceeding both limits is split under both."""
    requests = [_request(index, payload=1000) for index in range(20)]

    chunks = chunk_batch_requests(requests, max_requests=4, max_payload_bytes=2600)

    assert len(chunks) >= 7
    for chunk in chunks:
        assert len(chunk) <= 4
        assert sum(request.payload_size_bytes() for request in chunk) <= 2600
    assert [request for chunk in chunks for request in chunk] == requests


def test_ac069_a_corpus_over_the_limit_is_submitted_as_several_jobs(
    runner: CliRunner,
    corpus_root: Path,
    inject: Inject,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-069: chunking is actually wired into submission, not just available."""
    build_bulk_corpus(corpus_root, 5)
    monkeypatch.setattr("shotname.model.MAX_BATCH_REQUESTS", 2)
    transport = FakeTransport()
    inject(probe=OverrideProbe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--wait")

    assert result.exit_code == 0
    assert len(transport.batch_submissions) == 3
    for submission in transport.batch_submissions:
        assert len(submission) <= 2
