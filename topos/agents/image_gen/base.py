"""ImageGenBackend protocol + result dataclass.

A backend takes a text prompt (and optionally a condition image) and
returns PNG bytes. Backends are pluggable per the same pattern as
AgentBackend / Judge: a Protocol class plus a factory ``make_backend(name)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class ImageGenResult:
    """One image-gen call's return."""
    success: bool
    png_bytes: bytes
    mime_type: str = "image/png"
    duration_s: float = 0.0
    cost_usd: float = 0.0           # provider-reported cost where available
    model: str = ""                 # which model produced this
    raw_meta: dict = field(default_factory=dict)
    error: str | None = None


@runtime_checkable
class ImageGenBackend(Protocol):
    """A backend that turns text (and optional condition image) into a PNG."""
    name: str

    def generate(
        self,
        prompt: str,
        *,
        condition_image: Path | bytes | None = None,
        reference_images: list[Path] | None = None,
        size: int = 1024,
        timeout_s: int | None = None,
    ) -> ImageGenResult:
        """Produce a PNG image from the prompt.

        condition_image: optional path or raw bytes of a guidance image
            (e.g. a sketch / silhouette). Backends that don't support
            conditioning should ignore this and proceed text-only.
        size: target square resolution in pixels. Backends may not honor
            exactly; treat as a hint.
        """
        ...


def make_backend(name: str | None = None) -> ImageGenBackend:
    """Factory: pick an ImageGenBackend implementation by name. Defaults to
    the configured ``image_gen.default``.

    ``stub`` is gated: it returns RGB noise (used by tests / CI to exercise
    the pipeline without spending tokens) and must NOT be picked for real
    generation. When a real backend's API key is configured, requesting
    ``"stub"`` raises — observed 2026-05-13 with gemini-3-flash agent
    self-rationalizing ``--backend stub`` "to avoid needing an API key"
    even though the key was already present. To explicitly opt in for
    testing, set ``TOPOS_ALLOW_STUB_IMAGE_GEN=1`` in env.
    """
    import os
    from ... import config as cfg
    effective = cfg.load_effective_config()
    image_gen_conf = effective.get("image_gen") or {}
    name = name or image_gen_conf.get("default", "gemini")

    if name == "gemini":
        from .gemini import GeminiBackend
        return GeminiBackend.from_config()
    if name == "openai":
        from .openai import OpenAIImageBackend
        return OpenAIImageBackend.from_config()
    if name == "stub":
        if not os.environ.get("TOPOS_ALLOW_STUB_IMAGE_GEN"):
            gem_conf = image_gen_conf.get("gemini") or {}
            openai_conf = image_gen_conf.get("openai") or {}
            real_key_available = bool(
                os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or gem_conf.get("api_key")
                or openai_conf.get("api_key")
            )
            if real_key_available:
                raise ValueError(
                    "image_gen backend 'stub' is blocked when a real backend's "
                    "API key is configured — it returns RGB noise, not a real "
                    "image. Drop `--backend stub` (the default is the real "
                    "Gemini Nano Banana). For testing, set "
                    "TOPOS_ALLOW_STUB_IMAGE_GEN=1 in the environment."
                )
        from .stub import StubBackend
        return StubBackend()

    raise ValueError(f"unknown image_gen backend: {name!r}")
