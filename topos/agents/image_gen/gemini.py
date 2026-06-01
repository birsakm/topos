"""Gemini image-generation backend.

Default model is "Nano Banana 2" (``gemini-3.1-flash-image-preview``);
"Nano Banana Pro" (``gemini-3-pro-image-preview``) is a higher-quality
opt-in. Uses the REST API directly via stdlib ``urllib`` so we don't add
a heavy SDK dep. The API key is read from
``config.image_gen.gemini.api_key`` (set via
``topos config set image_gen.gemini.api_key <key>``) or env override
``TOPOS__IMAGE_GEN__GEMINI__API_KEY``. Get a key at
https://aistudio.google.com/app/apikey.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ... import config as cfg
from ...backends._pricing import gemini_image_cost_usd
from .base import ImageGenResult


_ENDPOINT_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


@dataclass
class GeminiBackend:
    name: str = "gemini"
    api_key: str | None = None
    model: str = "gemini-3.1-flash-image-preview"
    timeout_s: int = 180         # Nano Banana 2 can take 60-120s per image
    # Server-side variance on the preview model is large (observed 18s–180s+
    # within a single session). Without retry, ~1 in 5 calls timed out. With
    # exponential backoff this drops to near-zero at trivial extra cost.
    max_retries: int = 2         # 2 retries = up to 3 attempts total
    retry_base_wait_s: float = 5.0   # 5s → 10s between retries

    @classmethod
    def from_config(cls) -> "GeminiBackend":
        effective = cfg.load_effective_config()
        gconf = (effective.get("image_gen") or {}).get("gemini") or {}
        return cls(
            api_key=gconf.get("api_key") or None,
            model=gconf.get("model") or "gemini-3.1-flash-image-preview",
            timeout_s=int(gconf.get("timeout_s") or 180),
            max_retries=int(gconf.get("max_retries") if gconf.get("max_retries") is not None else 2),
            retry_base_wait_s=float(gconf.get("retry_base_wait_s") or 5.0),
        )

    @staticmethod
    def _image_part(src: Path | bytes) -> dict:
        if isinstance(src, Path):
            data = src.read_bytes()
            mime = "image/png" if src.suffix.lower() == ".png" else "image/jpeg"
        else:
            data = src
            mime = "image/png"
        return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}}

    def _build_request(
        self,
        prompt: str,
        *,
        condition_image: Path | bytes | None,
        reference_images: list[Path] | None = None,
    ) -> dict:
        """Construct the JSON payload for generateContent."""
        parts: list[dict] = [{"text": prompt}]
        if condition_image is not None:
            parts.append(self._image_part(condition_image))
        for ref in reference_images or []:
            if isinstance(ref, Path) and ref.is_file():
                parts.append(self._image_part(ref))
        return {"contents": [{"role": "user", "parts": parts}]}

    def _extract_png(self, response_json: dict) -> bytes:
        """Pull the first inline image data out of the response. Raises on no image."""
        candidates = response_json.get("candidates") or []
        for cand in candidates:
            content = cand.get("content") or {}
            for part in content.get("parts") or []:
                inline = part.get("inline_data") or part.get("inlineData")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"])
        # No image — surface what text the model returned, for debugging
        text_parts: list[str] = []
        for cand in candidates:
            for part in (cand.get("content") or {}).get("parts") or []:
                if isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
        raise RuntimeError(
            f"Gemini response contained no image data. "
            f"Text parts: {text_parts[:1]!r}. Raw keys: {list(response_json)}"
        )

    def generate(
        self,
        prompt: str,
        *,
        condition_image: Path | bytes | None = None,
        reference_images: list[Path] | None = None,
        size: int = 1024,            # gemini doesn't expose explicit size; informational only
        timeout_s: int | None = None,
    ) -> ImageGenResult:
        # Return a failed result rather than raising — texture failures must
        # not crash the build, only log + skip. The caller decides whether to
        # surface or swallow the error.
        if not self.api_key:
            return ImageGenResult(
                success=False, png_bytes=b"", model=self.model,
                error=(
                    "GeminiBackend: image_gen.gemini.api_key not configured. "
                    "Set via `topos config set image_gen.gemini.api_key <key>` "
                    "(get a key at https://aistudio.google.com/app/apikey)."
                ),
            )
        url = f"{_ENDPOINT_BASE}/{self.model}:generateContent?key={self.api_key}"
        payload = self._build_request(prompt, condition_image=condition_image, reference_images=reference_images)
        data = json.dumps(payload).encode("utf-8")
        effective_timeout = timeout_s or self.timeout_s

        # Retry on network timeout / 5xx / 429. The preview model has bursty
        # latency — a single 180s timeout is a coin flip; with 2 retries we
        # get effectively-guaranteed success at trivial extra cost (failed
        # attempts are server-side queue waits, not billed image generations).
        attempt = 0
        last_error = ""
        wait_s = self.retry_base_wait_s
        start_overall = time.monotonic()
        while True:
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            attempt_start = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                    body = resp.read()
                # HTTP 200 — but parse + image-extraction is INSIDE the retry
                # loop on purpose. The preview model intermittently returns a
                # 200 with NO image part (empty/text-only response — observed in
                # the wild: "no image data, Text parts: []"). That's a transient
                # server flake, identical in nature to a 5xx, so retry it rather
                # than failing the call (which, under image-default, would
                # otherwise leave the part flat for no good reason).
                response_json = json.loads(body)
                png = self._extract_png(response_json)
                return ImageGenResult(
                    success=True,
                    png_bytes=png,
                    mime_type="image/png",
                    duration_s=time.monotonic() - start_overall,
                    cost_usd=gemini_image_cost_usd(self.model, n_images=1),
                    model=self.model,
                    raw_meta={"response_size_bytes": len(body)},
                )
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                # Retry on 429 (rate limit) and 5xx (server error); fail-fast on 4xx.
                retryable = e.code == 429 or 500 <= e.code < 600
                last_error = f"HTTP {e.code}: {err_body[:500]}"
                if not retryable or attempt >= self.max_retries:
                    return ImageGenResult(
                        success=False, png_bytes=b"", model=self.model,
                        duration_s=time.monotonic() - start_overall,
                        error=last_error,
                    )
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = f"network error: {e}"
                if attempt >= self.max_retries:
                    return ImageGenResult(
                        success=False, png_bytes=b"", model=self.model,
                        duration_s=time.monotonic() - start_overall,
                        error=last_error,
                    )
            except (json.JSONDecodeError, RuntimeError) as e:
                # Empty / no-image / unparseable 200 — transient, retry like a 5xx.
                last_error = f"response parse failed: {e}"
                if attempt >= self.max_retries:
                    return ImageGenResult(
                        success=False, png_bytes=b"", model=self.model,
                        duration_s=time.monotonic() - start_overall,
                        error=last_error,
                    )
            attempt += 1
            print(
                f"[gemini image_gen] attempt {attempt}/{self.max_retries + 1} "
                f"failed after {time.monotonic() - attempt_start:.1f}s "
                f"({last_error[:80]}); retrying in {wait_s:.0f}s"
            )
            time.sleep(wait_s)
            wait_s *= 2
