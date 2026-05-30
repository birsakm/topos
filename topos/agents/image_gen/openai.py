"""OpenAI image-generation backend (``gpt-image-1``).

A non-Gemini ``ImageGenBackend`` so texture generation isn't locked to one
provider. Two paths:

  - no condition image  → ``POST /v1/images/generations`` (JSON)
  - condition image set → ``POST /v1/images/edits`` (multipart) so the UV-layout
    condition (and any reference images) guide the result, matching what the
    Gemini backend does for the uv_atlas texture flow.

gpt-image-1 returns the image as base64 in ``data[0].b64_json``.

Auth (first hit wins): ``OPENAI_API_KEY`` env → ``image_gen.openai.api_key`` →
``visual_critic.openai_vision.api_key`` (OpenAI keys aren't feature-scoped).
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import uuid
from dataclasses import dataclass
from pathlib import Path

from ... import config as cfg
from ..visual_critic.critic_utils import post_json_with_retries
from .base import ImageGenResult

_GENERATIONS_URL = "https://api.openai.com/v1/images/generations"
_EDITS_URL = "https://api.openai.com/v1/images/edits"

# gpt-image-1 only accepts these sizes (+ "auto"); we texture square, so snap to 1024².
_OPENAI_SIZE = "1024x1024"


def _as_bytes(src: "Path | bytes") -> bytes:
    return src.read_bytes() if isinstance(src, Path) else src


def _multipart(fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> tuple[str, bytes]:
    """Build a multipart/form-data body. Returns (content_type, body)."""
    boundary = f"----topos{uuid.uuid4().hex}"
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode() + b"\r\n"
    for name, filename, data in files:
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        out += b"Content-Type: image/png\r\n\r\n"
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", bytes(out)


@dataclass
class OpenAIImageBackend:
    name: str = "openai"
    api_key: str | None = None
    model: str = "gpt-image-1"
    timeout_s: int = 180
    max_retries: int = 2
    retry_base_wait_s: float = 5.0

    @classmethod
    def from_config(cls) -> "OpenAIImageBackend":
        effective = cfg.load_effective_config()
        oconf = (effective.get("image_gen") or {}).get("openai") or {}
        vc_openai = (effective.get("visual_critic") or {}).get("openai_vision") or {}
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or oconf.get("api_key")
            or vc_openai.get("api_key")
        )
        return cls(
            api_key=api_key or None,
            model=oconf.get("model") or "gpt-image-1",
            timeout_s=int(oconf.get("timeout_s", 180)),
            max_retries=int(oconf.get("max_retries", 2)),
            retry_base_wait_s=float(oconf.get("retry_base_wait_s", 5.0)),
        )

    def generate(
        self,
        prompt: str,
        *,
        condition_image: "Path | bytes | None" = None,
        reference_images: "list[Path] | None" = None,
        size: int = 1024,
        timeout_s: int | None = None,
    ) -> ImageGenResult:
        if not self.api_key:
            return ImageGenResult(
                success=False, png_bytes=b"", model=self.model,
                error=(
                    "OpenAIImageBackend: no API key. Set OPENAI_API_KEY in env, or "
                    "image_gen.openai.api_key, or visual_critic.openai_vision.api_key."
                ),
            )
        timeout = timeout_s or self.timeout_s
        auth = {"Authorization": f"Bearer {self.api_key}"}
        start = time.monotonic()
        try:
            if condition_image is not None:
                fields = {"model": self.model, "prompt": prompt, "size": _OPENAI_SIZE}
                files = [("image[]", "condition.png", _as_bytes(condition_image))]
                for i, ref in enumerate(reference_images or []):
                    if isinstance(ref, Path) and ref.is_file():
                        files.append(("image[]", f"ref{i}.png", ref.read_bytes()))
                content_type, body = _multipart(fields, files)
                url = _EDITS_URL
                headers = {**auth, "Content-Type": content_type}
            else:
                body = json.dumps({
                    "model": self.model, "prompt": prompt, "size": _OPENAI_SIZE, "n": 1,
                }).encode("utf-8")
                url = _GENERATIONS_URL
                headers = {**auth, "Content-Type": "application/json"}

            resp = post_json_with_retries(
                url=url, body=body, headers=headers, timeout_s=timeout,
                max_retries=self.max_retries, retry_base_wait_s=self.retry_base_wait_s,
                label="image_gen.openai",
            )
        except (RuntimeError, urllib.error.URLError, TimeoutError, OSError) as e:
            return ImageGenResult(
                success=False, png_bytes=b"", model=self.model,
                duration_s=time.monotonic() - start,
                error=f"OpenAIImageBackend request failed: {e}",
            )

        try:
            envelope = json.loads(resp)
            data = envelope.get("data") or []
            b64 = data[0]["b64_json"] if data else None
            if not b64:
                raise ValueError(f"no b64_json in response; keys={list(envelope)}")
            png = base64.b64decode(b64)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            return ImageGenResult(
                success=False, png_bytes=b"", model=self.model,
                duration_s=time.monotonic() - start,
                error=f"OpenAIImageBackend: could not parse image from response: {e}",
            )

        return ImageGenResult(
            success=True,
            png_bytes=png,
            duration_s=time.monotonic() - start,
            cost_usd=0.0,  # OpenAI image cost isn't reported in-response / no price table yet
            model=self.model,
            raw_meta={"conditioned": condition_image is not None},
        )
