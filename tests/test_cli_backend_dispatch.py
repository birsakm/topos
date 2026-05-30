"""Unit tests for ``cli._make_backends_for_plan``.

Pre-fix behavior: ``topos run`` hardcoded ``{"claude": ...}`` so a plan that
declared ``backend: gemini`` parsed fine but failed mid-DAG with a soft
``no backend registered`` TaskResult. These tests pin the new contract:
only used backends are constructed, unknown names fail fast at CLI level.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from topos.cli import _make_backends_for_plan
from topos.orchestrator.plan_schema import load_plan


def _write_plan(tmp_path: Path, tasks: list[dict]) -> Path:
    plan = {"project": "t", "iter_policy": {"max_global_iters": 1}, "tasks": tasks}
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    return p


def test_only_used_backends_are_constructed(tmp_path: Path):
    p = _write_plan(tmp_path, [
        {"id": "a", "kind": "agent", "goal": "x", "backend": "claude"},
    ])
    plan = load_plan(p)
    # If codex/gemini factories ran, they'd try to read API keys and the
    # test could spuriously fail. The lazy dispatch should leave them alone.
    with patch("topos.backends.codex_cli.CodexCLIBackend.from_config") as codex_mk, \
         patch("topos.backends.gemini_cli.GeminiCLIBackend.from_config") as gemini_mk:
        backends = _make_backends_for_plan(plan)
        codex_mk.assert_not_called()
        gemini_mk.assert_not_called()
    assert set(backends) == {"claude"}


def test_multi_backend_plan_constructs_all_used(tmp_path: Path):
    p = _write_plan(tmp_path, [
        {"id": "a", "kind": "agent", "goal": "x", "backend": "claude"},
        {"id": "b", "kind": "agent", "goal": "y", "backend": "gemini",
         "deps": ["a"]},
    ])
    plan = load_plan(p)
    with patch("topos.backends.claude_cli.ClaudeCLIBackend.from_config") as claude_mk, \
         patch("topos.backends.gemini_cli.GeminiCLIBackend.from_config") as gemini_mk:
        backends = _make_backends_for_plan(plan)
        claude_mk.assert_called_once()
        gemini_mk.assert_called_once()
    assert set(backends) == {"claude", "gemini"}


def test_unknown_backend_raises_fast(tmp_path: Path):
    # Schema accepts any str (backend: str = "claude"); the dispatch layer
    # has to be the one to reject. Failing here beats failing mid-DAG.
    p = _write_plan(tmp_path, [
        {"id": "a", "kind": "agent", "goal": "x", "backend": "anthropic_api"},
    ])
    plan = load_plan(p)
    with pytest.raises(typer.BadParameter, match="anthropic_api"):
        _make_backends_for_plan(plan)


def test_tool_tasks_dont_register_backends(tmp_path: Path):
    # ToolTask has no `backend` field; only AgentTasks should contribute.
    p = _write_plan(tmp_path, [
        {"id": "a", "kind": "tool", "tool": "export_glb", "args": {"workspace": "."}},
    ])
    plan = load_plan(p)
    backends = _make_backends_for_plan(plan)
    assert backends == {}
