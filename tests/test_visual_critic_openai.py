"""Tests for OpenAIVisionCritic.

Mocks urlopen rather than making real API calls — keeps unit tests fast,
deterministic, and zero-cost. End-to-end against a live OpenAI key
should be done manually (or in an integration-marked test).
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from topos.agents.visual_critic.base import (
    Criterion,
    Critic,
    CriticInputs,
    CriticResult,
    Rubric,
    make_critic,
)
from topos.agents.visual_critic.openai_vision import (
    OpenAIVisionCritic,
    _build_payload,
    _materialise,
)


def _fake_png_bytes() -> bytes:
    """Minimal valid PNG header — enough that read_bytes returns something."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _stub_rubric() -> Rubric:
    return Rubric(
        id="stub_v1",
        judge_backend="openai_vision",
        pass_threshold=0.6,
        criteria=[Criterion(id="overall", prompt="just rate it", weight=1.0)],
    )


def _stub_envelope(json_payload: dict) -> dict:
    """Build a fake OpenAI Chat Completions response envelope."""
    return {
        "id": "chatcmpl-fake",
        "model": "gpt-5",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": json.dumps(json_payload)},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
    }


# ---------- Protocol conformance ----------

def test_openai_vision_implements_critic_protocol():
    c = OpenAIVisionCritic(api_key="fake")
    assert isinstance(c, Critic)


def test_make_critic_dispatches_openai_vision():
    """rubric.judge_backend='openai_vision' should route to OpenAIVisionCritic."""
    r = _stub_rubric()
    # Need an API key in config to construct
    with patch.dict("os.environ", {"OPENAI_API_KEY": "fake"}, clear=False):
        c = make_critic(r)
    assert isinstance(c, OpenAIVisionCritic)


# ---------- payload construction ----------

def test_build_payload_embeds_image_as_base64(tmp_path: Path):
    img = tmp_path / "test.png"
    img.write_bytes(_fake_png_bytes())
    payload = _build_payload("evaluate this", [img], model="gpt-5")
    assert payload["model"] == "gpt-5"
    assert payload["response_format"] == {"type": "json_object"}
    content = payload["messages"][0]["content"]
    # First part = text prompt; second part = image_url with data URI
    assert content[0] == {"type": "text", "text": "evaluate this"}
    assert content[1]["type"] == "image_url"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # Decode the base64 portion and verify it matches the file
    b64 = url.split(",", 1)[1]
    assert base64.b64decode(b64) == _fake_png_bytes()


def test_build_payload_handles_multiple_images(tmp_path: Path):
    imgs = []
    for i in range(3):
        p = tmp_path / f"img_{i}.png"
        p.write_bytes(_fake_png_bytes())
        imgs.append(p)
    payload = _build_payload("rate all 3", imgs, model="gpt-5")
    content = payload["messages"][0]["content"]
    # 1 text + 3 image_url parts
    assert len(content) == 4
    assert all(p["type"] == "image_url" for p in content[1:])


def test_build_payload_detects_jpeg_mime(tmp_path: Path):
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
    payload = _build_payload("eval", [img], model="gpt-5")
    url = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")


# ---------- response materialisation ----------

def test_materialise_extracts_critic_result():
    rubric = _stub_rubric()
    critique = {
        "overall_score": 0.72,
        "passed": True,
        "per_criterion": {
            "overall": {"score": 0.72, "feedback": "looks pretty good overall"},
        },
        "suggested_fixes": ["tighten the clearance"],
    }
    envelope = _stub_envelope(critique)
    result = _materialise(envelope, rubric, raw="raw envelope text")
    assert isinstance(result, CriticResult)
    assert result.passed is True
    assert result.overall_score == 0.72
    assert result.per_criterion["overall"]["score"] == 0.72
    assert result.suggested_fixes == ["tighten the clearance"]
    assert result.usage == {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75}


def test_materialise_overrides_passed_when_under_threshold():
    """If model claims passed=True but overall < pass_threshold, we override."""
    rubric = _stub_rubric()  # threshold 0.6
    critique = {
        "overall_score": 0.45,
        "passed": True,
        "per_criterion": {"overall": {"score": 0.45, "feedback": "meh"}},
        "suggested_fixes": [],
    }
    envelope = _stub_envelope(critique)
    result = _materialise(envelope, rubric, raw="x")
    assert result.passed is False  # not 0.45 >= 0.60


def test_materialise_raises_when_choices_missing():
    rubric = _stub_rubric()
    envelope = {"id": "x", "model": "gpt-5", "usage": {}}
    with pytest.raises(RuntimeError, match="no choices"):
        _materialise(envelope, rubric, raw="x")


def test_materialise_handles_markdown_fenced_json():
    """Some models wrap JSON in ```json fences despite response_format. The
    parser strips fences."""
    rubric = _stub_rubric()
    critique_str = '```json\n{"overall_score": 0.8, "passed": true, "per_criterion": {}, "suggested_fixes": []}\n```'
    envelope = {
        "choices": [{"message": {"role": "assistant", "content": critique_str}}],
        "usage": {},
    }
    result = _materialise(envelope, rubric, raw="x")
    assert result.overall_score == 0.8


# ---------- auth checks ----------

def test_evaluate_raises_without_api_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    img = tmp_path / "x.png"
    img.write_bytes(_fake_png_bytes())
    c = OpenAIVisionCritic(api_key=None)
    with pytest.raises(RuntimeError, match="needs an API key"):
        c.evaluate(CriticInputs(images=[img]), _stub_rubric())


def test_evaluate_raises_without_images(tmp_path: Path):
    c = OpenAIVisionCritic(api_key="fake")
    with pytest.raises(ValueError, match="no images supplied"):
        c.evaluate(CriticInputs(images=[]), _stub_rubric())


# ---------- end-to-end via mocked urlopen ----------

def test_evaluate_happy_path_with_mocked_http(tmp_path: Path):
    img = tmp_path / "x.png"
    img.write_bytes(_fake_png_bytes())
    rubric = _stub_rubric()
    critique = {
        "overall_score": 0.7,
        "passed": True,
        "per_criterion": {"overall": {"score": 0.7, "feedback": "decent"}},
        "suggested_fixes": [],
    }
    envelope = _stub_envelope(critique)
    body = json.dumps(envelope).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = lambda self, *args: None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        c = OpenAIVisionCritic(api_key="fake", model="gpt-5")
        result = c.evaluate(CriticInputs(images=[img]), rubric)
    assert result.passed is True
    assert result.overall_score == 0.7
    assert result.usage["total_tokens"] == 75


def test_evaluate_handles_http_error(tmp_path: Path):
    import urllib.error
    img = tmp_path / "x.png"
    img.write_bytes(_fake_png_bytes())
    rubric = _stub_rubric()
    err = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=429, msg="Too Many Requests", hdrs=None, fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=err):
        # Use 10ms base wait instead of production 30s so retry exhaustion
        # completes in ~30ms total instead of 30+60+120=210s. The retry-shape
        # contract under test is identical; only the per-retry sleep length
        # changes. Without this override the test was the single slowest in
        # the suite (~210s).
        c = OpenAIVisionCritic(api_key="fake", retry_base_wait_s=0.01)
        with pytest.raises(RuntimeError, match="HTTP 429"):
            c.evaluate(CriticInputs(images=[img]), rubric)
