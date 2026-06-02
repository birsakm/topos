"""Per-model USD pricing for backends/critics that don't return cost natively.

Gemini API only returns token counts — never a USD cost — so the
``GeminiCLIBackend`` and ``GeminiVisionCritic`` need to multiply by a
price table to surface ``cost_usd``. Anthropic returns USD already, so
``ClaudeCLIBackend`` reads ``total_cost_usd`` directly and skips this
table.

Two tables, two billing models:
  - ``PRICING``        — per 1 M tokens (text + vision-on-text models)
  - ``IMAGE_PRICING``  — per single image (Nano Banana family)

Text prices are standard / short-context tier (<=200 K input). Long-context
premiums (above 200 K input) and cache-write surcharges are NOT modeled here
— Gemini doesn't expose cache-write as a separate billing event.

This is mirrored from infinigen/eval/utils/model_api_price.py so the
two projects can be priced consistently. Update both when prices drift.
"""

from __future__ import annotations


PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-pro":                {"input": 1.25, "output": 10.00, "cached_input": 0.125},
    "gemini-2.5-flash":              {"input": 0.30, "output":  2.50, "cached_input": 0.075},
    "gemini-3-flash-preview":        {"input": 0.50, "output":  3.00, "cached_input": 0.05},
    # gemini-3.5-flash (global region). Verified 2026-06-02 against Google's
    # published pricing ($1.50/$9.00 per 1M, cached $0.15). Non-global regions
    # are $1.65/$9.90 — use the global rate as the canonical estimate.
    "gemini-3.5-flash":              {"input": 1.50, "output":  9.00, "cached_input": 0.15},
    "gemini-3-pro-preview":          {"input": 2.00, "output": 12.00, "cached_input": 0.20},
    "gemini-3.1-pro-preview":        {"input": 2.00, "output": 12.00, "cached_input": 0.20},
    "gemini-3.1-flash-lite-preview": {"input": 0.25, "output":  1.50, "cached_input": 0.025},
}


# Image-generation models bill per image, not per token. Rates are USD/image
# at the default 1024x1024 size. Verified against Google's published pricing
# 2026-05; update when Nano Banana / Imagen rates change.
IMAGE_PRICING: dict[str, float] = {
    # Nano Banana 2 — default for ``generate_texture_image`` tool.
    "gemini-3.1-flash-image-preview": 0.039,
    # Nano Banana Pro — higher quality at ~3.5x the cost.
    "gemini-3-pro-image-preview":     0.139,
    # Legacy Nano Banana 1 — kept so retrofit pricing on older trajectories
    # still resolves a non-zero number.
    "gemini-2.5-flash-image-preview": 0.039,
}


def gemini_cost_usd(
    model: str | None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> float:
    """Compute USD for one Gemini API call.

    ``input_tokens`` is the UNCACHED portion only; ``cached_input_tokens``
    is the prompt-cache-hit count (billed at a discounted rate). The
    sum matches what Gemini reports as ``total_input_tokens``.

    Returns 0.0 when the model isn't in the pricing table — pricing
    table absence is non-fatal so an unknown / preview model doesn't
    break the run; the framework just under-reports cost.
    """
    if not model:
        return 0.0
    p = PRICING.get(model)
    if p is None:
        return 0.0
    M = 1_000_000
    uncached = max(0, (input_tokens or 0) - (cached_input_tokens or 0))
    cost = uncached * p["input"] / M
    cost += (cached_input_tokens or 0) * p.get("cached_input", p["input"]) / M
    cost += (output_tokens or 0) * p["output"] / M
    return cost


def gemini_image_cost_usd(model: str | None, *, n_images: int = 1) -> float:
    """USD for one (or N) Gemini image-generation calls.

    Per-image flat pricing — Nano Banana models bill per generated image
    regardless of prompt length. Returns 0.0 for unknown models or
    non-positive ``n_images``, mirroring the text helper's no-raise policy.
    """
    if not model or n_images <= 0:
        return 0.0
    rate = IMAGE_PRICING.get(model)
    if rate is None:
        return 0.0
    return rate * n_images
