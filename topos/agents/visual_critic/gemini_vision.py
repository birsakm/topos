"""Vision critic using the Gemini generateContent API directly (HTTP).

Single-shot: POST images + prompt → JSON-formatted critique → CriticResult.
Uses stdlib ``urllib`` to match the dependency surface of
``topos.agents.image_gen.gemini.GeminiBackend`` (no SDK dep).

Auth:
1. Env var ``GEMINI_API_KEY`` (preferred)
2. Topos config ``visual_critic.gemini_vision.api_key``
3. Fallback: ``image_gen.gemini.api_key`` — Google API keys aren't
   feature-scoped, so users who already configured a key for textures
   shouldn't need to set it again for the critic. To override the
   fallback, set ``visual_critic.gemini_vision.api_key`` explicitly.
4. Raises RuntimeError if none are set.

Pricing: Gemini's response carries token counts in ``usageMetadata`` but
not a USD cost. We surface ``usage`` (token counts) and leave
``cost_usd=0.0`` for the orchestrator to multiply by a price table if
desired.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ... import config as cfg
from .._json_extract import try_load
from .base import CriticInputs, CriticResult, Rubric
from .critic_utils import (
    build_critic_prompt,
    materialise_score,
    post_json_with_retries,
)


_ENDPOINT_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


@dataclass
class GeminiVisionCritic:
    """Vision critic backed by Gemini ``generateContent`` (multimodal model).

    Defaults to ``gemini-3-flash`` — the cheaper flash-tier model, fast
    enough for routine multi-view critique. Override to ``gemini-3-pro``
    via rubric extras (``model:``) or config
    ``visual_critic.gemini_vision.model`` when fine-grained geometry
    critique justifies the higher cost.
    """
    api_key: str | None = None
    model: str = "gemini-3-flash"
    timeout_s: int = 180
    endpoint_base: str = _ENDPOINT_BASE
    # Transient-error retry knobs. Gemini's free quota is small enough that
    # bursty pipelines (16+ per-part judges) trip 429 routinely; retry with
    # backoff is the standard fix.
    max_retries: int = 3
    retry_base_wait_s: float = 30.0
    # Google Search grounding — when True, the model can (but isn't
    # required to) issue searches before answering, so it can independently
    # look up what the target object should look like (e.g. "PW1100G
    # turbofan cutaway") instead of relying purely on its training prior +
    # the rendered images. The model decides per-call whether to actually
    # search — so the cost (~$0.035 per grounded call) is opt-in by the
    # model itself when the role_hint warrants it. Mutually exclusive with
    # structured JSON output (response_mime_type) at the API level — the
    # prompt still asks for JSON-only output, and _json_extract.try_load()
    # handles markdown-wrapped responses. Default OFF — flip on per project
    # via `topos config set visual_critic.gemini_vision.use_google_search true`.
    use_google_search: bool = False

    @classmethod
    def from_config(cls, config: dict | None = None) -> "GeminiVisionCritic":
        effective = cfg.load_effective_config()
        critic_section = (effective.get("visual_critic") or {}).get("gemini_vision") or {}
        merged = {**critic_section, **(config or {})}
        # API-key resolution: env > critic section > image_gen fallback
        ig_section = (effective.get("image_gen") or {}).get("gemini") or {}
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or merged.get("api_key")
            or ig_section.get("api_key")
            or None
        )
        return cls(
            api_key=api_key,
            model=merged.get("model", "gemini-3-flash"),
            timeout_s=int(merged.get("timeout_s", 180)),
            endpoint_base=merged.get("endpoint_base", _ENDPOINT_BASE),
            max_retries=int(merged.get("max_retries", 3)),
            retry_base_wait_s=float(merged.get("retry_base_wait_s", 30.0)),
            use_google_search=bool(merged.get("use_google_search", False)),
        )

    def evaluate(self, inputs: CriticInputs, rubric: Rubric) -> CriticResult:
        if not inputs.images:
            raise ValueError("GeminiVisionCritic.evaluate: no images supplied")
        if not self.api_key:
            raise RuntimeError(
                "GeminiVisionCritic needs an API key. Set GEMINI_API_KEY in env, "
                "or configure visual_critic.gemini_vision.api_key, or reuse "
                "image_gen.gemini.api_key. Get a key at "
                "https://aistudio.google.com/app/apikey."
            )

        refs = inputs.reference_images or []
        prompt_text = build_critic_prompt(
            rubric, role_hint=(inputs.metadata or {}).get("role_hint"),
            n_reference=len(refs),
        )
        # reference target image(s) FIRST, then the rendered output to grade —
        # the prompt says the leading n_reference attached images are the target.
        payload = _build_payload(
            prompt_text, [*refs, *inputs.images],
            use_google_search=self.use_google_search,
        )
        start = time.monotonic()
        response_body = post_json_with_retries(
            url=f"{self.endpoint_base}/{self.model}:generateContent?key={self.api_key}",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout_s=self.timeout_s,
            max_retries=self.max_retries,
            retry_base_wait_s=self.retry_base_wait_s,
            label="gemini_vision",
        )
        duration_s = time.monotonic() - start

        try:
            envelope = json.loads(response_body)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"GeminiVisionCritic: response was not JSON: {e}; "
                f"head: {response_body[:200]!r}"
            )

        return _materialise(
            envelope, rubric,
            raw=response_body.decode("utf-8", errors="replace"),
            duration_s=duration_s,
            model=self.model,
        )


def _build_payload(
    prompt: str, image_paths: list[Path], *,
    use_google_search: bool = False,
) -> dict:
    """Construct the generateContent request body for a vision call.

    Gemini wants images as inline_data parts alongside the text part within
    a single ``user`` content message. ``response_mime_type`` forces the
    model's output to be a valid JSON string, but it's mutually exclusive
    with the ``google_search`` grounding tool, so when grounding is on we
    drop response_mime_type and rely on the prompt asking for JSON-only
    output (parsed by ``_json_extract.try_load`` which tolerates fences).
    """
    parts: list[dict] = [{"text": prompt}]
    for img_path in image_paths:
        data = img_path.read_bytes()
        ext = img_path.suffix.lower().lstrip(".") or "png"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(data).decode("ascii"),
            }
        })
    payload: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.2},
    }
    if use_google_search:
        payload["tools"] = [{"google_search": {}}]
    else:
        payload["generationConfig"]["response_mime_type"] = "application/json"
    return payload


def _extract_text(envelope: dict) -> str:
    """Pull the assistant text from the first candidate's parts.

    Gemini response shape::

        { "candidates": [
            { "content": { "parts": [ {"text": "..."} ] },
              "finishReason": "STOP", ... } ],
          "usageMetadata": { ... } }
    """
    candidates = envelope.get("candidates") or []
    if not candidates:
        raise RuntimeError(
            f"GeminiVisionCritic: no candidates in response. "
            f"keys={list(envelope)}; "
            f"promptFeedback={envelope.get('promptFeedback')}"
        )
    cand0 = candidates[0] or {}
    for part in (cand0.get("content") or {}).get("parts") or []:
        if isinstance(part.get("text"), str):
            return part["text"]
    raise RuntimeError(
        f"GeminiVisionCritic: first candidate had no text part. "
        f"finishReason={cand0.get('finishReason')!r}; "
        f"parts={(cand0.get('content') or {}).get('parts')!r}"
    )


def _materialise(
    envelope: dict, rubric: Rubric, *,
    raw: str, duration_s: float, model: str,
) -> CriticResult:
    """Extract the JSON critique from a Gemini generateContent envelope."""
    text = _extract_text(envelope)
    parsed = try_load(text)
    if parsed is None:
        raise RuntimeError(
            f"GeminiVisionCritic: could not parse JSON from response text. "
            f"head: {text[:300]!r}"
        )

    passed, overall, per_criterion, fixes = materialise_score(parsed, rubric)
    usage_meta = envelope.get("usageMetadata") or {}
    # Gemini's usageMetadata shape: promptTokenCount, candidatesTokenCount,
    # totalTokenCount, cachedContentTokenCount (when caching active).
    input_tokens = usage_meta.get("promptTokenCount") or 0
    output_tokens = usage_meta.get("candidatesTokenCount") or 0
    cached_tokens = usage_meta.get("cachedContentTokenCount") or 0
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": usage_meta.get("totalTokenCount"),
        "cached_input_tokens": cached_tokens,
        "duration_s": duration_s,
        "model": model,
    }
    from ...backends._pricing import gemini_cost_usd
    return CriticResult(
        passed=passed,
        overall_score=overall,
        per_criterion=per_criterion,
        suggested_fixes=fixes,
        raw_response=raw,
        cost_usd=gemini_cost_usd(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
        ),
        usage=usage,
    )
