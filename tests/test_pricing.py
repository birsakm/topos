"""Unit tests for ``topos.backends._pricing`` — text + image gemini pricing."""

from __future__ import annotations

import pytest

from topos.backends._pricing import (
    IMAGE_PRICING,
    PRICING,
    gemini_cost_usd,
    gemini_image_cost_usd,
)


def test_flash_simple_input_output():
    # 100K uncached input × $0.50/M + 1K output × $3.00/M = 0.05 + 0.003 = 0.053
    c = gemini_cost_usd(
        "gemini-3-flash-preview",
        input_tokens=100_000,
        output_tokens=1_000,
    )
    assert c == pytest.approx(0.053, rel=1e-4)


def test_flash_with_cached_input():
    # 100K total input, 80K cached → 20K uncached × $0.50/M + 80K cached × $0.05/M
    # + 1K output × $3.00/M = 0.010 + 0.004 + 0.003 = 0.017
    c = gemini_cost_usd(
        "gemini-3-flash-preview",
        input_tokens=100_000,
        cached_input_tokens=80_000,
        output_tokens=1_000,
    )
    assert c == pytest.approx(0.017, rel=1e-4)


def test_pro_pricing_is_4x_flash():
    # gemini-3.1-pro-preview is 4x flash on input ($2 vs $0.50) and output ($12 vs $3)
    flash = gemini_cost_usd("gemini-3-flash-preview", input_tokens=10_000, output_tokens=1_000)
    pro = gemini_cost_usd("gemini-3.1-pro-preview", input_tokens=10_000, output_tokens=1_000)
    assert pro == pytest.approx(flash * 4, rel=1e-4)


def test_unknown_model_returns_zero_not_raise():
    """Pricing-table absence must be non-fatal so a preview model doesn't
    crash the run; the framework just under-reports cost."""
    assert gemini_cost_usd("gemini-99-future") == 0.0


def test_none_model_returns_zero():
    assert gemini_cost_usd(None) == 0.0


def test_cached_cant_exceed_input():
    # Defensive: if a backend reports cached > input (shouldn't happen but
    # don't want a negative cost), uncached portion clamps to 0.
    c = gemini_cost_usd(
        "gemini-3-flash-preview",
        input_tokens=1_000,
        cached_input_tokens=5_000,  # implausible
        output_tokens=0,
    )
    # All 5K cached × $0.05/M = 0.00025
    assert c == pytest.approx(5_000 * 0.05 / 1_000_000, rel=1e-4)


def test_all_listed_models_have_required_keys():
    for model, p in PRICING.items():
        assert "input" in p, f"{model} missing input rate"
        assert "output" in p, f"{model} missing output rate"


# ---------- gemini_image_cost_usd ----------


def test_image_nano_banana_2_single():
    """Default Nano Banana 2 = $0.039 per image."""
    assert gemini_image_cost_usd("gemini-3.1-flash-image-preview") == pytest.approx(0.039)


def test_image_nano_banana_pro_costs_more():
    """Pro tier billed higher than the flash variant."""
    flash = gemini_image_cost_usd("gemini-3.1-flash-image-preview")
    pro = gemini_image_cost_usd("gemini-3-pro-image-preview")
    assert pro > flash


def test_image_n_images_scales_linearly():
    one = gemini_image_cost_usd("gemini-3.1-flash-image-preview", n_images=1)
    five = gemini_image_cost_usd("gemini-3.1-flash-image-preview", n_images=5)
    assert five == pytest.approx(one * 5)


def test_image_unknown_model_returns_zero():
    """Same no-raise policy as the text helper."""
    assert gemini_image_cost_usd("imagen-99-future") == 0.0


def test_image_none_model_returns_zero():
    assert gemini_image_cost_usd(None) == 0.0


def test_image_zero_or_negative_n_returns_zero():
    """Defensive: caller passing n_images=0 (failed gen, nothing to bill)
    must not produce a phantom charge."""
    assert gemini_image_cost_usd("gemini-3.1-flash-image-preview", n_images=0) == 0.0
    assert gemini_image_cost_usd("gemini-3.1-flash-image-preview", n_images=-1) == 0.0


def test_image_pricing_table_only_has_image_models():
    """Sanity: don't accidentally cross-pollinate text models into IMAGE_PRICING."""
    for model in IMAGE_PRICING:
        assert "image" in model, (
            f"{model!r} doesn't look like an image-gen model — is it in the wrong table?"
        )
