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

#: Per-file text budget the estimate assumes: the prompt plus the OCR transcript.
TEXT_TOKENS_PER_FILE = 500

#: A name suggestion is a three-field JSON object, so the answer is tiny.
OUTPUT_TOKENS_PER_FILE = 60

#: A local run spends nothing, whatever the model id happens to be priced at.
_FREE = (0.0, 0.0)


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
        return (
            f"estimated cost ${self.total_usd:.4f} for {self.file_count} files "
            f"using {self.model} at {self.resolution.value} resolution "
            f"via the {self.endpoint.value} endpoint "
            f"({self.input_tokens} input + {self.output_tokens} output tokens)"
        )


def _prices(*, model: str, local: bool) -> tuple[float, float]:
    """USD per million input and output tokens for `model`.

    An unknown model id is priced at the default model's rate rather than at
    zero: a wrong estimate is recoverable, a silent "this run is free" is not.
    """
    if local:
        return _FREE
    return PRICE_PER_MTOK.get(model, PRICE_PER_MTOK[DEFAULT_MODEL])


def _usd(
    *,
    input_tokens: int,
    output_tokens: int,
    prices: tuple[float, float],
    discount: float,
) -> float:
    input_price, output_price = prices
    per_token = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    return per_token * discount


def _discount(endpoint: Endpoint) -> float:
    return BATCH_DISCOUNT if endpoint is Endpoint.BATCH else 1.0


def estimate_cost(*, file_count: int, settings: RunSettings) -> CostEstimate:
    """Price `file_count` files under `settings`, halved on the batch path."""
    input_tokens = file_count * (
        IMAGE_TOKENS_PER_TIER[settings.resolution] + TEXT_TOKENS_PER_FILE
    )
    output_tokens = file_count * OUTPUT_TOKENS_PER_FILE
    endpoint = settings.endpoint
    return CostEstimate(
        file_count=file_count,
        model=settings.effective_model,
        resolution=settings.resolution,
        endpoint=endpoint,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_usd=_usd(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prices=_prices(model=settings.effective_model, local=settings.local),
            discount=_discount(endpoint),
        ),
    )


def realised_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    model: str,
    endpoint: Endpoint,
    local: bool = False,
) -> float:
    """What the tokens a run actually consumed cost (AC-077)."""
    return _usd(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        prices=_prices(model=model, local=local),
        discount=_discount(endpoint),
    )
