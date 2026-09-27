"""The two production transports, cloud and local, behind one protocol.

`model.Transport` covers the synchronous endpoint, the Batch API, and Ollama in a
single interface, which is the whole of AC-057: selecting a backend is a
constructor call in `deps.default_deps` and nothing downstream knows which one it
got. The local backend has no batch endpoint, so it answers `submit_batch` with a
usage error and `settings.endpoint` never asks it to (AC-057, D-056).

Neither transport opens a connection when it is constructed. That matters for
AC-050, where the run must fail on a missing key before it does anything else,
and for AC-055, where the only socket a `--local` run is allowed to open is one
to the configured Ollama host.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import anthropic
import httpx

from .errors import (
    BackendUnavailableError,
    RateLimitedError,
    UsageError,
)
from .model import (
    BatchEntryStatus,
    BatchJobState,
    BatchResultEntry,
    ModelRequest,
    ModelResponse,
)

#: Enough for the JSON object the prompt asks for, and no more.
MAX_OUTPUT_TOKENS = 512

#: A single naming call is small; a hung one should not wedge a 4000-file run.
REQUEST_TIMEOUT_SECONDS = 120.0

#: Ollama loads the vision model on the first request, which is slow and fine.
LOCAL_TIMEOUT_SECONDS = 600.0

_JOB_STATES: dict[str, BatchJobState] = {
    "in_progress": BatchJobState.IN_PROGRESS,
    "canceling": BatchJobState.CANCELING,
    "ended": BatchJobState.ENDED,
}

_ENTRY_STATUSES: dict[str, BatchEntryStatus] = {
    "succeeded": BatchEntryStatus.SUCCEEDED,
    "errored": BatchEntryStatus.ERRORED,
    "expired": BatchEntryStatus.EXPIRED,
    "canceled": BatchEntryStatus.CANCELED,
}

#: Retry-after in seconds when the provider rate-limits without saying for how long.
FALLBACK_RETRY_AFTER = 5.0


def _user_content(request: ModelRequest) -> list[dict[str, Any]]:
    """The image and the OCR transcript, in the order the prompt describes them."""
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": request.image_media_type,
                "data": request.image_base64(),
            },
        },
        {
            "type": "text",
            "text": f"{request.prompt}\n\nOCR text, topmost line first:\n{request.ocr_text}",
        },
    ]


def _retry_after(error: anthropic.RateLimitError) -> float:
    header = error.response.headers.get("retry-after")
    if header is None:
        return FALLBACK_RETRY_AFTER
    try:
        return float(header)
    except ValueError:
        return FALLBACK_RETRY_AFTER


def _text_of(message: anthropic.types.Message) -> str:
    return "".join(
        block.text
        for block in message.content
        if isinstance(block, anthropic.types.TextBlock)
    )


def _response_from_message(
    custom_id: str, message: anthropic.types.Message
) -> ModelResponse:
    return ModelResponse(
        custom_id=custom_id,
        text=_text_of(message),
        stop_reason=str(message.stop_reason),
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
    )


class AnthropicTransport:
    """The cloud backend: `messages.create` for sync, `messages.batches` for batch.

    The client is built on first use rather than in `__init__` so that
    constructing the transport cannot fail — the missing-key check belongs to
    preflight, where it can exit 2 with a message instead of a traceback
    (AC-050).
    """

    def __init__(self, *, api_key: str | None) -> None:
        self._api_key = api_key
        self._client: anthropic.AsyncAnthropic | None = None

    def _connection(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key, timeout=REQUEST_TIMEOUT_SECONDS
            )
        return self._client

    def _params(self, request: ModelRequest) -> dict[str, Any]:
        return {
            "model": request.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "messages": [{"role": "user", "content": _user_content(request)}],
        }

    async def send(self, request: ModelRequest) -> ModelResponse:
        try:
            message = await self._connection().messages.create(**self._params(request))
        except anthropic.RateLimitError as limited:
            # A 429 is a wait instruction, not a failure; the caller backs off
            # and retries the same file (AC-051, D-031).
            raise RateLimitedError(retry_after=_retry_after(limited)) from limited
        except anthropic.APIConnectionError as unreachable:
            raise BackendUnavailableError(
                f"cannot reach the Anthropic API: {unreachable}"
            ) from unreachable
        return _response_from_message(request.custom_id, message)

    async def submit_batch(self, requests: Sequence[ModelRequest]) -> str:
        entries = [
            {"custom_id": request.custom_id, "params": self._params(request)}
            for request in requests
        ]
        try:
            job = await self._connection().messages.batches.create(requests=entries)  # type: ignore[arg-type]
        except anthropic.APIConnectionError as unreachable:
            raise BackendUnavailableError(
                f"cannot reach the Anthropic API: {unreachable}"
            ) from unreachable
        return job.id

    async def job_state(self, job_id: str) -> BatchJobState:
        job = await self._connection().messages.batches.retrieve(job_id)
        return _JOB_STATES.get(job.processing_status, BatchJobState.IN_PROGRESS)

    async def job_results(self, job_id: str) -> list[BatchResultEntry]:
        entries: list[BatchResultEntry] = []
        async for entry in await self._connection().messages.batches.results(job_id):
            result = entry.result
            status = _ENTRY_STATUSES.get(result.type, BatchEntryStatus.ERRORED)
            if status is BatchEntryStatus.SUCCEEDED and result.type == "succeeded":
                entries.append(
                    BatchResultEntry(
                        custom_id=entry.custom_id,
                        status=status,
                        response=_response_from_message(
                            entry.custom_id, result.message
                        ),
                        error=None,
                    )
                )
                continue
            entries.append(
                BatchResultEntry(
                    custom_id=entry.custom_id,
                    status=status,
                    response=None,
                    error=result.type,
                )
            )
        return entries


class OllamaTransport:
    """The local backend: one `POST /api/generate` per capture, and no batch path.

    The host is carried in the error messages because an unreachable local model
    is the failure this backend actually has, and "connection refused" without a
    host is not an answer (AC-056).
    """

    def __init__(self, *, host: str, model: str) -> None:
        self.host = host.rstrip("/")
        self.model = model

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt": (
                f"{request.prompt}\n\nOCR text, topmost line first:\n{request.ocr_text}"
            ),
            "images": [request.image_base64()],
            "stream": False,
            "format": "json",
        }

    async def send(self, request: ModelRequest) -> ModelResponse:
        try:
            async with httpx.AsyncClient(
                base_url=self.host, timeout=LOCAL_TIMEOUT_SECONDS
            ) as client:
                reply = await client.post("/api/generate", json=self._payload(request))
                reply.raise_for_status()
                body = reply.json()
        except (httpx.HTTPError, json.JSONDecodeError) as failure:
            raise BackendUnavailableError(
                f"cannot reach the Ollama host at {self.host}: {failure}"
            ) from failure

        usage_in = int(body.get("prompt_eval_count", 0))
        usage_out = int(body.get("eval_count", 0))
        return ModelResponse(
            custom_id=request.custom_id,
            text=str(body.get("response", "")),
            stop_reason=str(body.get("done_reason", "stop")),
            input_tokens=usage_in,
            output_tokens=usage_out,
        )

    def _no_batch(self) -> UsageError:
        return UsageError(
            "the local backend has no batch endpoint; it always dispatches "
            "synchronously (AC-057)"
        )

    async def submit_batch(self, requests: Sequence[ModelRequest]) -> str:
        raise self._no_batch()

    async def job_state(self, job_id: str) -> BatchJobState:
        raise self._no_batch()

    async def job_results(self, job_id: str) -> list[BatchResultEntry]:
        raise self._no_batch()
