"""Pre-run cost estimation and realised-cost accounting.

The estimate is printed before the first request goes out, unconditionally
(AC-032), and `--max-cost` is checked against it before anything is spent
(AC-033). There is no built-in ceiling (AC-079).

The estimate names the model and the resolution tier it was computed for, because
comparing two `--sample` runs is the whole point of those being run parameters
(AC-077).
"""

from __future__ import annotations

from dataclasses import dataclass

from .settings import DEFAULT_MODEL, Endpoint, Resolution, RunSettings

#: The Batch API's discount relative to the synchronous endpoint (AC-067).
BATCH_DISCOUNT = 0.5

#: USD per million tokens, as (input, output), per model id.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    DEFAULT_MODEL: (1.0, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
}

#: Rough per-image input tokens by tier, before the OCR text and the prompt.
IMAGE_TOKENS_PER_TIER: dict[Resolution, int] = {
    Resolution.STANDARD: 800,
    Resolution.HIGH: 1_600,
}


@dataclass(frozen=True)
class CostEstimate:
    """What a run expects to spend, and the settings that number assumes."""

    file_count: int
    model: str
    resolution: Resolution
    endpoint: Endpoint
    input_tokens: int
    output_tokens: int
    total_usd: float

    def format(self) -> str:
        """The line printed before any model request is issued (AC-032)."""
        raise NotImplementedError


def estimate_cost(*, file_count: int, settings: RunSettings) -> CostEstimate:
    """Price `file_count` files under `settings`, halved on the batch path."""
    raise NotImplementedError


def realised_cost_usd(
    *, input_tokens: int, output_tokens: int, model: str, endpoint: Endpoint
) -> float:
    """What the tokens a run actually consumed cost (AC-077)."""
    raise NotImplementedError
