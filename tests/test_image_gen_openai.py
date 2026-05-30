"""OpenAI image backend — image generation isn't Gemini-locked.

HTTP is mocked: the point is that `image_gen.default=openai` routes to a working
backend that (a) uses /images/generations for text->image, (b) uses multipart
/images/edits when a condition image is supplied (so the UV-atlas texture flow
still gets conditioning), and (c) parses the b64_json envelope into PNG bytes.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

from topos.agents.image_gen.base import ImageGenBackend, make_backend
from topos.agents.image_gen.openai import OpenAIImageBackend

_FAKE_PNG = b"\x89PNG\r\n\x1a\n fake bytes"
_B64 = base64.b64encode(_FAKE_PNG).decode()


def _resp_bytes():
    return json.dumps({"data": [{"b64_json": _B64}]}).encode("utf-8")


def test_protocol_conformance():
    assert isinstance(OpenAIImageBackend(api_key="k"), ImageGenBackend)


def test_no_key_returns_error_not_raise():
    r = OpenAIImageBackend(api_key=None).generate("a toolbox")
    assert r.success is False
    assert "no API key" in (r.error or "")


def test_text_to_image_uses_generations_json():
    captured = {}

    def fake(**kw):
        captured.update(kw)
        return _resp_bytes()

    with patch("topos.agents.image_gen.openai.post_json_with_retries", side_effect=fake):
        r = OpenAIImageBackend(api_key="k").generate("a red toolbox")

    assert r.success and r.png_bytes == _FAKE_PNG
    assert captured["url"].endswith("/images/generations")
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["headers"]["Authorization"] == "Bearer k"
    assert r.raw_meta["conditioned"] is False


def test_condition_image_uses_edits_multipart(tmp_path):
    cond = tmp_path / "cond.png"
    cond.write_bytes(b"\x89PNG cond")
    captured = {}

    def fake(**kw):
        captured.update(kw)
        return _resp_bytes()

    with patch("topos.agents.image_gen.openai.post_json_with_retries", side_effect=fake):
        r = OpenAIImageBackend(api_key="k").generate("paint it", condition_image=cond)

    assert r.success and r.png_bytes == _FAKE_PNG
    assert captured["url"].endswith("/images/edits")
    assert captured["headers"]["Content-Type"].startswith("multipart/form-data")
    # the condition image must actually be embedded in the multipart body
    assert b"condition.png" in captured["body"]
    assert b"\x89PNG cond" in captured["body"]
    assert r.raw_meta["conditioned"] is True


def test_bad_response_is_clean_error():
    with patch("topos.agents.image_gen.openai.post_json_with_retries",
               return_value=json.dumps({"data": []}).encode("utf-8")):
        r = OpenAIImageBackend(api_key="k").generate("p")
    assert r.success is False
    assert "parse" in (r.error or "")


def test_factory_dispatches_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("topos.config.load_effective_config",
               return_value={"image_gen": {"default": "openai"}}):
        backend = make_backend()
    assert isinstance(backend, OpenAIImageBackend)
    assert backend.api_key == "sk-test"
