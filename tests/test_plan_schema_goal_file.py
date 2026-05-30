"""Unit tests for goal_file resolution in plan_schema.load_plan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from topos.orchestrator.plan_schema import load_plan


def _write_plan(tmp_path: Path, tasks: list[dict], *, project: str = "t") -> Path:
    plan = {"project": project, "iter_policy": {"max_global_iters": 1}, "tasks": tasks}
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    return p


def test_inline_goal_passes_through(tmp_path: Path):
    p = _write_plan(tmp_path, [{
        "id": "a", "kind": "agent", "goal": "do the thing", "backend": "claude",
    }])
    plan = load_plan(p)
    assert plan.tasks[0].goal == "do the thing"


def test_goal_file_relative_path(tmp_path: Path):
    (tmp_path / "prompt.md").write_text("hello from a relative file")
    p = _write_plan(tmp_path, [{
        "id": "a", "kind": "agent", "goal_file": "prompt.md", "backend": "claude",
    }])
    plan = load_plan(p)
    assert plan.tasks[0].goal == "hello from a relative file"


def test_goal_file_topos_scheme_existing_prompt(tmp_path: Path):
    """The shipped prompts under topos/prompts/ should be resolvable via topos:..."""
    p = _write_plan(tmp_path, [{
        "id": "a", "kind": "agent",
        "goal_file": "topos:articulated/builder.md",
        "backend": "claude",
    }])
    plan = load_plan(p)
    # builder.md mentions "Blender entry point" near the top
    assert "blender entry point" in plan.tasks[0].goal.lower()
    assert len(plan.tasks[0].goal) > 100


def test_goal_file_topos_scheme_missing_raises(tmp_path: Path):
    p = _write_plan(tmp_path, [{
        "id": "a", "kind": "agent",
        "goal_file": "topos:nope/does_not_exist.md",
        "backend": "claude",
    }])
    with pytest.raises(FileNotFoundError, match="does_not_exist"):
        load_plan(p)


def test_goal_and_goal_file_mutually_exclusive(tmp_path: Path):
    (tmp_path / "x.md").write_text("x")
    p = _write_plan(tmp_path, [{
        "id": "a", "kind": "agent",
        "goal": "inline",
        "goal_file": "x.md",
        "backend": "claude",
    }])
    with pytest.raises(ValueError, match="mutually exclusive"):
        load_plan(p)


def test_missing_all_three_raises(tmp_path: Path):
    p = _write_plan(tmp_path, [{
        "id": "a", "kind": "agent", "backend": "claude",
    }])
    with pytest.raises(ValueError, match="must set 'goal', 'goal_file', or 'goal_template'"):
        load_plan(p)


def test_goal_template_renders_with_params(tmp_path: Path):
    (tmp_path / "t.md.j2").write_text("Hello {{ name }}. Extras: {{ extras }}")
    (tmp_path / "extras.md").write_text("be bold")
    p = _write_plan(tmp_path, [{
        "id": "a", "kind": "agent",
        "goal_template": "t.md.j2",
        "goal_params": {"name": "Alice", "extras_file": "extras.md"},
        "backend": "claude",
    }])
    plan = load_plan(p)
    assert plan.tasks[0].goal == "Hello Alice. Extras: be bold"


def test_goal_template_topos_scheme(tmp_path: Path):
    """Renders a shipped articulated/part_geom template with required params."""
    (tmp_path / "extras.md").write_text("- be detailed")
    p = _write_plan(tmp_path, [{
        "id": "a", "kind": "agent",
        "goal_template": "topos:articulated/part_geom.md.j2",
        "goal_params": {"part_name": "Frame", "lower_name": "frame",
                         "extras_file": "extras.md"},
        "backend": "claude",
    }])
    plan = load_plan(p)
    goal = plan.tasks[0].goal
    assert "src/parts/frame.py" in goal
    assert "build_frame()" in goal
    assert "be detailed" in goal  # the extras content got injected
