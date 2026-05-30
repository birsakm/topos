"""Vision critic using the OpenAI Chat Completions API directly (HTTP).

Single-shot: POST images + prompt → JSON-formatted critique → CriticResult.
Uses stdlib ``urllib`` to avoid pulling the OpenAI SDK as a dependency
(matching the pattern of ``topos.agents.image_gen.gemini.GeminiBackend``).

Auth:
1. Env var ``OPENAI_API_KEY`` (preferred)
2. Topos config ``visual_critic.openai_vision.api_key``
3. Raises RuntimeError if neither is set.

Pricing: OpenAI's response carries token counts (prompt_tokens, completion_tokens)
but not a USD cost. We don't compute cost — token usage is surfaced in the
``CriticResult.usage`` field for the orchestrator to multiply by a price
table if desired.
"""

from __future__ import annotations

import base64
import json
import os
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


_ENDPOINT = "https://api.openai.com/v1/chat/completions"


@dataclass
class OpenAIVisionCritic:
    """Vision critic backed by OpenAI Chat Completions (vision-capable model).

    Defaults to ``gpt-5`` — override via rubric extras (`model:`) or via
    config ``visual_critic.openai_vision.model``.
    """
    api_key: str | None = None
    model: str = "gpt-5"
    timeout_s: int = 180
    base_url: str = _ENDPOINT
    # Transient-error retry (429 rate limit, 5xx server faults). Default
    # base wait 30s doubles per retry — 30/60/120 = ~3.5 min ceiling.
    max_retries: int = 3
    retry_base_wait_s: float = 30.0

    @classmethod
    def from_config(cls, config: dict | None = None) -> "OpenAIVisionCritic":
        effective = cfg.load_effective_config()
        section = (effective.get("visual_critic") or {}).get("openai_vision") or {}
        merged = {**section, **(config or {})}
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or merged.get("api_key")
            or None
        )
        return cls(
            api_key=api_key,
            model=merged.get("model", "gpt-5"),
            timeout_s=int(merged.get("timeout_s", 180)),
            base_url=merged.get("base_url", _ENDPOINT),
            max_retries=int(merged.get("max_retries", 3)),
            retry_base_wait_s=float(merged.get("retry_base_wait_s", 30.0)),
        )

    def evaluate(self, inputs: CriticInputs, rubric: Rubric) -> CriticResult:
        if not inputs.images:
            raise ValueError("OpenAIVisionCritic.evaluate: no images supplied")
        if not self.api_key:
            raise RuntimeError(
                "OpenAIVisionCritic needs an API key. Set OPENAI_API_KEY in env "
                "or configure visual_critic.openai_vision.api_key via "
                "`topos config set visual_critic.openai_vision.api_key <key>`."
            )

        prompt_text = build_critic_prompt(
            rubric, role_hint=(inputs.metadata or {}).get("role_hint"),
        )
        payload = _build_payload(prompt_text, inputs.images, self.model)
        response_body = post_json_with_retries(
            url=self.base_url,
            body=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout_s=self.timeout_s,
            max_retries=self.max_retries,
            retry_base_wait_s=self.retry_base_wait_s,
            label="openai_vision",
        )

        try:
            envelope = json.loads(response_body)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"OpenAIVisionCritic: response was not JSON: {e}; "
                f"head: {response_body[:200]!r}"
            )

        return _materialise(envelope, rubric, raw=response_body.decode("utf-8", errors="replace"))


def _build_payload(prompt: str, image_paths: list[Path], model: str) -> dict:
    """Construct the Chat Completions request body for a vision call."""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img_path in image_paths:
        data = img_path.read_bytes()
        ext = img_path.suffix.lower().lstrip(".") or "png"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        b64 = base64.b64encode(data).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        # Force JSON output — model still produces text inside choices[0].message.content
        # but we ask it to be a valid JSON object.
        "response_format": {"type": "json_object"},
    }


def _materialise(envelope: dict, rubric: Rubric, raw: str) -> CriticResult:
    """Extract the JSON critique from the OpenAI Chat Completions envelope.

    Shape:  envelope.choices[0].message.content is a string holding the
    JSON object we asked for via response_format=json_object.
    """
    choices = envelope.get("choices") or []
    if not choices:
        raise RuntimeError(
            f"OpenAIVisionCritic: no choices in response. keys={list(envelope)}"
        )
    msg = (choices[0] or {}).get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str):
        raise RuntimeError(
            f"OpenAIVisionCritic: choices[0].message.content was not a string: "
            f"{type(content).__name__}"
        )
    parsed = try_load(content)
    if parsed is None:
        raise RuntimeError(
            f"OpenAIVisionCritic: could not parse JSON from content. "
            f"head: {content[:300]!r}"
        )

    passed, overall, per_criterion, fixes = materialise_score(parsed, rubric)
    usage = envelope.get("usage") or {}
    return CriticResult(
        passed=passed,
        overall_score=overall,
        per_criterion=per_criterion,
        suggested_fixes=fixes,
        raw_response=raw,
        cost_usd=0.0,           # OpenAI doesn't return $ cost; usage carries tokens
        usage=usage,
    )
