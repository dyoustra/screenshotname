"""Stage 2: the model request, the response, and the one transport protocol.

`Transport` deliberately covers the synchronous endpoint, the Batch API, and
Ollama in a single protocol. That is what makes AC-057 ("both backends behind the
same interface") checkable, and what lets a test assert *which* endpoint a
request reached (AC-065).

`custom_id` is the content hash of the file the request names, which gives batch
reconciliation a natural key and deduplicates identical bytes for free (AC-068).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from .settings import RunSettings

#: The Batch API's documented per-job caps (AC-069).
MAX_BATCH_REQUESTS = 100_000
MAX_BATCH_PAYLOAD_BYTES = 256 * 1024 * 1024

#: Stop reasons that mean the model declined rather than answered (AC-046).
REFUSAL_STOP_REASONS = frozenset({"refusal"})


class BatchJobState(str, Enum):
    """A submitted job's lifecycle state. `ENDED` is the only terminal one."""

    IN_PROGRESS = "in_progress"
    CANCELING = "canceling"
    ENDED = "ended"


class BatchEntryStatus(str, Enum):
    """Per-entry outcome within a finished job (AC-074)."""

    SUCCEEDED = "succeeded"
    ERRORED = "errored"
    EXPIRED = "expired"
    CANCELED = "canceled"


@dataclass(frozen=True)
class NameSuggestion:
    """The model's structured answer: what application, and what it is showing."""

    app: str | None
    subject: str
    confidence: float

    def to_json_text(self) -> str:
        """Serialize to the JSON body a model response is expected to carry."""
        raise NotImplementedError

    @classmethod
    def from_json_text(cls, text: str) -> NameSuggestion:
        """Parse and validate a response body, raising `SchemaInvalidError`."""
        raise NotImplementedError


@dataclass(frozen=True)
class ModelRequest:
    """One naming request, keyed by the content hash of the image it carries."""

    custom_id: str
    model: str
    prompt: str
    image_bytes: bytes
    image_media_type: str
    ocr_text: str

    def payload_size_bytes(self) -> int:
        """This request's contribution to a job's payload budget (AC-069)."""
        raise NotImplementedError


@dataclass(frozen=True)
class ModelResponse:
    """One answer, with the token counts the realised cost is computed from."""

    custom_id: str
    text: str
    stop_reason: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class BatchResultEntry:
    """One entry of a finished job's results, successful or not."""

    custom_id: str
    status: BatchEntryStatus
    response: ModelResponse | None
    error: str | None


@dataclass(frozen=True)
class RequestInput:
    """What request construction needs about one file, before deduplication."""

    path: Path
    content_hash: str
    image_bytes: bytes
    ocr_text: str


class Transport(Protocol):
    """The network seam, covering both dispatch paths and both backends."""

    async def send(self, request: ModelRequest) -> ModelResponse: ...

    async def submit_batch(self, requests: Sequence[ModelRequest]) -> str: ...

    async def job_state(self, job_id: str) -> BatchJobState: ...

    async def job_results(self, job_id: str) -> list[BatchResultEntry]: ...


def build_model_requests(
    inputs: Sequence[RequestInput], *, settings: RunSettings, prompt: str
) -> list[ModelRequest]:
    """One request per distinct content hash, in first-seen order (AC-068)."""
    raise NotImplementedError


def is_refusal(response: ModelResponse) -> bool:
    """Whether the provider's stop reason or the response text signals a refusal."""
    raise NotImplementedError
