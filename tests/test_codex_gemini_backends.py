"""Tests for CodexCLIBackend + GeminiCLIBackend.

Verifies command construction against the real CLI surfaces (codex-cli
0.128.0 + gemini-cli 0.41.2 as observed 2026-05-11). Does NOT execute the
binaries — just builds the cmd list and asserts shape. End-to-end calls
require API keys + live LLMs and are out of scope here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.backends.base import AgentBackend
from topos.backends.codex_cli import CodexCLIBackend
from topos.backends.gemini_cli import GeminiCLIBackend


# ---------- protocol conformance ----------

def test_codex_implements_agentbackend():
    assert isinstance(CodexCLIBackend(), AgentBackend)


def test_gemini_implements_agentbackend():
    assert isinstance(GeminiCLIBackend(), AgentBackend)


# ---------- codex command construction ----------

def test_codex_cmd_uses_exec_subcommand(tmp_path: Path):
    """codex headless mode is `codex exec <PROMPT>` — subcommand + positional prompt."""
    b = CodexCLIBackend(model="gpt-5")
    cmd = b.build_cmd("hello world", tmp_path)
    assert cmd[0] == "codex"
    assert cmd[1] == "exec"
    # Prompt MUST be the last arg (positional after all flags)
    assert cmd[-1] == "hello world"


def test_codex_cmd_includes_cwd_with_capital_C(tmp_path: Path):
    b = CodexCLIBackend()
    cmd = b.build_cmd("p", tmp_path)
    # -C (uppercase) is codex's cwd flag, not --cd
    assert "-C" in cmd
    idx = cmd.index("-C")
    assert cmd[idx + 1] == str(tmp_path)


def test_codex_cmd_includes_model_and_sandbox(tmp_path: Path):
    b = CodexCLIBackend(model="gpt-5", sandbox="workspace-write")
    cmd = b.build_cmd("p", tmp_path)
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "gpt-5"
    assert "-s" in cmd and cmd[cmd.index("-s") + 1] == "workspace-write"


def test_codex_cmd_bypass_approvals_flag(tmp_path: Path):
    b = CodexCLIBackend(bypass_approvals=True)
    cmd = b.build_cmd("p", tmp_path)
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    b2 = CodexCLIBackend(bypass_approvals=False)
    cmd2 = b2.build_cmd("p", tmp_path)
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd2


def test_codex_cmd_config_overrides_emit_dash_c(tmp_path: Path):
    b = CodexCLIBackend(config_overrides=("model_reasoning_effort=medium", "foo.bar=true"))
    cmd = b.build_cmd("p", tmp_path)
    # Each config override becomes a -c <kv> pair
    c_count = cmd.count("-c")
    assert c_count == 2
    assert "model_reasoning_effort=medium" in cmd
    assert "foo.bar=true" in cmd


# ---------- gemini command construction ----------

def test_gemini_cmd_uses_dash_p_for_prompt():
    """gemini headless: `gemini -p <PROMPT> ...`"""
    b = GeminiCLIBackend()
    cmd = b.build_cmd("hello world")
    assert cmd[0] == "gemini"
    # -p PROMPT must appear as a pair
    idx = cmd.index("-p")
    assert cmd[idx + 1] == "hello world"


def test_gemini_cmd_has_no_cwd_flag():
    """gemini-cli uses process cwd; backend's run() chdir's via subprocess.
    There should be NO --cd / -C flag in the command."""
    b = GeminiCLIBackend()
    cmd = b.build_cmd("p")
    assert "--cd" not in cmd
    assert "-C" not in cmd


def test_gemini_cmd_always_uses_stream_json():
    """Output format is fixed to stream-json — see backends/gemini_cli.py
    module docstring. It's the only shape that exposes per-event token
    counts, which gemini_cost_usd needs (gemini never returns USD natively)."""
    b = GeminiCLIBackend()
    cmd = b.build_cmd("p")
    assert "-o" in cmd
    assert cmd[cmd.index("-o") + 1] == "stream-json"


def test_gemini_cmd_approval_mode_yolo():
    b = GeminiCLIBackend(approval_mode="yolo")
    cmd = b.build_cmd("p")
    assert "--approval-mode" in cmd
    assert cmd[cmd.index("--approval-mode") + 1] == "yolo"


def test_gemini_cmd_include_directories():
    b = GeminiCLIBackend(include_directories=("/abs/dir/a", "/abs/dir/b"))
    cmd = b.build_cmd("p")
    assert "--include-directories" in cmd
    assert cmd[cmd.index("--include-directories") + 1] == "/abs/dir/a,/abs/dir/b"


# ---------- auth checks ----------

def test_codex_raises_without_openai_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    b = CodexCLIBackend(auth_mode="api_key")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        b.run(prompt="hi", workspace=tmp_path, allowed_tools=[],
              mcp_servers=[], timeout_s=1)


def test_gemini_raises_when_no_key_anywhere(tmp_path: Path, monkeypatch):
    """Backend now falls back to topos config (image_gen.gemini.api_key)
    when env is empty. To trigger RuntimeError, must also force config to
    return no key."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from topos import config as cfg
    monkeypatch.setattr(cfg, "load_effective_config", lambda: {"image_gen": {"gemini": {}}})
    b = GeminiCLIBackend(auth_mode="api_key")
    with pytest.raises(RuntimeError, match="needs an API key"):
        b.run(prompt="hi", workspace=tmp_path, allowed_tools=[],
              mcp_servers=[], timeout_s=1)


def test_gemini_accepts_either_google_or_gemini_key(tmp_path: Path, monkeypatch):
    """Auth check passes if either env name is set, OR if topos config has a key."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-not-real")
    # Force config to also have a key so we don't depend on test ordering
    from topos import config as cfg
    monkeypatch.setattr(cfg, "load_effective_config",
                         lambda: {"image_gen": {"gemini": {"api_key": "fake"}}})
    b = GeminiCLIBackend(auth_mode="api_key", cli="/does/not/exist/gemini")
    # Auth check passes; subprocess fails because binary doesn't exist
    with pytest.raises(FileNotFoundError):
        b.run(prompt="hi", workspace=tmp_path, allowed_tools=[],
              mcp_servers=[], timeout_s=1)


# ---------- from_config layering ----------

def test_codex_from_config_uses_defaults():
    b = CodexCLIBackend.from_config()
    assert b.cli == "codex"
    assert b.sandbox == "workspace-write"
    assert b.bypass_approvals is True


def test_codex_from_config_user_override():
    b = CodexCLIBackend.from_config({"model": "gpt-5-turbo", "sandbox": "read-only",
                                      "bypass_approvals": False})
    assert b.model == "gpt-5-turbo"
    assert b.sandbox == "read-only"
    assert b.bypass_approvals is False


def test_gemini_from_config_uses_defaults():
    b = GeminiCLIBackend.from_config()
    assert b.cli == "gemini"
    assert b.approval_mode == "yolo"


def test_gemini_from_config_user_override():
    b = GeminiCLIBackend.from_config({"model": "gemini-3-pro", "approval_mode": "auto_edit"})
    assert b.model == "gemini-3-pro"
    assert b.approval_mode == "auto_edit"
