"""Trajectory-analysis synthesis is provider-agnostic.

These verify the routing + per-provider response parsing without any real HTTP:
the point is that `topos analyze` is NOT Gemini-locked — picking openai /
anthropic routes to the right endpoint shape and parses its envelope.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from topos.analysis import synthesize as syn


def _cfg(d):
    return patch("topos.config.load_effective_config", return_value=d)


def _http(envelope: dict):
    return patch(
        "topos.analysis.synthesize.post_json_with_retries",
        return_value=json.dumps(envelope).encode("utf-8"),
    )


# ---------- backend + model resolution (precedence) ----------

def test_default_backend_is_gemini():
    with _cfg({}):
        assert syn._resolve_backend_and_model(None, None) == ("gemini", "gemini-3-flash-preview")


def test_backend_from_config():
    with _cfg({"analysis": {"backend": "openai"}}):
        assert syn._resolve_backend_and_model(None, None) == ("openai", "gpt-5")


def test_explicit_arg_beats_config():
    with _cfg({"analysis": {"backend": "openai", "model": "x"}}):
        # explicit backend arg wins, and its default model is used (not config's)
        assert syn._resolve_backend_and_model("anthropic", None) == ("anthropic", "claude-sonnet-4-6")


def test_unknown_backend_raises():
    with _cfg({}):
        with pytest.raises(ValueError):
            syn._resolve_backend_and_model("mistral", None)


# ---------- per-provider response parsing ----------

def test_gemini_parse():
    env = {"candidates": [{"content": {"parts": [{"text": "G-report"}]}}]}
    with _http(env):
        assert syn._call_gemini("p", model="m", api_key="k") == "G-report"


def test_openai_parse():
    env = {"choices": [{"message": {"content": "O-report"}}]}
    with _http(env):
        assert syn._call_openai("p", model="m", api_key="k") == "O-report"


def test_anthropic_parse():
    env = {"content": [{"type": "text", "text": "A-report"}]}
    with _http(env):
        assert syn._call_anthropic("p", model="m", api_key="k") == "A-report"


def test_dispatch_routes_by_backend():
    env = {"choices": [{"message": {"content": "O"}}]}
    with _http(env):
        assert syn._synthesize("p", backend="openai", model="m", api_key="k") == "O"


# ---------- key resolution ----------

def test_openai_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with _cfg({}):
        assert syn._resolve_api_key("openai") == "sk-test"


def test_anthropic_key_from_config(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with _cfg({"analysis": {"anthropic": {"api_key": "ant-key"}}}):
        assert syn._resolve_api_key("anthropic") == "ant-key"


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with _cfg({}):
        with pytest.raises(RuntimeError):
            syn._resolve_api_key("openai")
