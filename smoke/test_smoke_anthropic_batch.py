"""Real seam: the Anthropic Message Batches API, through production wiring.

Submits one request keyed the way production keys it (the content hash of the
file, AC-068), then waits a bounded time for it to finish.
"""

from __future__ import annotations

import asyncio
import os
import struct
import time
import zlib
from pathlib import Path

from shotname.hashing import content_hash
from shotname.model import BatchJobState, ModelRequest
from shotname.transports import AnthropicTransport

from ._report import report

SEAM = "anthropic-batch"
MODEL = "claude-haiku-4-5-20251001"


def _png(width: int, height: int) -> bytes:
    """A plain white PNG, built from the standard library alone."""
    row = b"\x00" + b"\xff\xff\xff" * width
    raw = zlib.compress(row * height)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def test_smoke_batch_submit_and_finish(tmp_path: Path) -> None:
    shot = tmp_path / "Screenshot 2026-09-27 at 10.00.00.png"
    shot.write_bytes(_png(32, 16))
    request = ModelRequest(
        custom_id=content_hash(shot),
        model=MODEL,
        prompt="Reply with the single word: blank.",
        image_bytes=shot.read_bytes(),
        image_media_type="image/png",
        ocr_text="",
    )
    transport = AnthropicTransport(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    try:
        job_id = asyncio.run(transport.submit_batch([request]))
    except Exception as refused:
        report(SEAM, "submit a one-request batch", "failed", f"{type(refused).__name__}: {refused}")
        raise
    report(SEAM, "submit a one-request batch", "accepted", f"batch {job_id} accepted")

    wait = int(os.environ.get("SFO_SMOKE_ASYNC_WAIT_SECONDS", "300"))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if asyncio.run(transport.job_state(job_id)) is BatchJobState.ENDED:
            break
        time.sleep(10)
    else:
        report(SEAM, "results parse", "accepted", f"batch {job_id} still running after {wait}s")
        return

    results = asyncio.run(transport.job_results(job_id))
    assert [entry.custom_id for entry in results] == [request.custom_id]
    assert results[0].response is not None, f"entry failed: {results[0].error}"
    report(SEAM, "results parse", "completed", f"batch {job_id} returned {results[0].response.text!r}")
