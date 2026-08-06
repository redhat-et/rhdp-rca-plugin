"""Token pricing table for cost estimation.

Each adapter reports native cost when the SDK exposes it. When it doesn't, the
harness falls back to multiplying observed token counts by these rates.

Update PRICING when Anthropic publishes new pricing. Rates are USD per
1,000,000 tokens.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRate:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float


PRICING: dict[str, ModelRate] = {
    "claude-opus-4-7": ModelRate(15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-6": ModelRate(15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-5": ModelRate(15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-1": ModelRate(15.00, 75.00, 1.50, 18.75),
    "claude-sonnet-4-6": ModelRate(3.00, 15.00, 0.30, 3.75),
    "claude-sonnet-4-5": ModelRate(3.00, 15.00, 0.30, 3.75),
    "claude-haiku-4-5": ModelRate(1.00, 5.00, 0.10, 1.25),
}


def cost_from_tokens(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Compute USD cost from token counts. Returns None if the model is unknown."""
    rate = PRICING.get(_normalize(model))
    if rate is None:
        return None
    return (
        input_tokens * rate.input_per_mtok
        + output_tokens * rate.output_per_mtok
        + cache_read_tokens * rate.cache_read_per_mtok
        + cache_write_tokens * rate.cache_write_per_mtok
    ) / 1_000_000


def _normalize(model: str) -> str:
    # Strip provider prefix and date suffix so both "anthropic/claude-sonnet-4.6"
    # and "claude-sonnet-4-6-20250101" still match "claude-sonnet-4-6".
    name = model.split("/")[-1].lower().replace(".", "-")
    for key in PRICING:
        if name.startswith(key):
            return key
    return name
