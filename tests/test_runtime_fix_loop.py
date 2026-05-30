"""Runtime fix-loop: when a tool surfaces ``failed_parts``, fix_loop
should build per-part fix tasks that point the agent at the broken
part file with the traceback.

This is the framework's response to Blender-API drift in part agent
output — the part agent writes code, the verify_parts gate catches the
runtime error, this fix-loop re-runs just that part agent with the
traceback as feedback. No cascade-kill of the whole pipeline."""

from __future__ import annotations

from topos.orchestrator.fix_loop import (
    build_runtime_fix_tasks,
    collect_runtime_failures,
)
from topos.orchestrator.results import TaskResult
from topos.orchestrator.tasks import AgentTask


def _verify_result_with_failures(failed_parts: list[dict]) -> TaskResult:
    """Shape-mirror of what topos.tools.blender_verifier.tool.verify_parts
    returns when one or more parts fail to build."""
    return TaskResult(
        id="11_tool_verify_parts",
        kind="tool",
        success=False,
        duration_s=2.0,
        output={
            "success": False,
            "total": 3,
            "passed": ["Nacelle", "FanHub"],
            "failed_parts": failed_parts,
        },
    )


def _clean_verify_result() -> TaskResult:
    return TaskResult(
        id="11_tool_verify_parts",
        kind="tool",
        success=True,
        duration_s=2.0,
        output={
            "success": True,
            "total": 2,
            "passed": ["Nacelle", "FanHub"],
            "failed_parts": [],
        },
    )


# --- collect_runtime_failures --------------------------------------------


def test_collect_no_failures():
    results = {"11_tool_verify_parts": _clean_verify_result()}
    assert collect_runtime_failures(results) == []


def test_collect_returns_failed_records():
    fp = {
        "name": "FanBlade",
        "lower_name": "fan_blade",
        "stage": "build_call",
        "error_class": "AttributeError",
        "error_msg": "'Mesh' object has no attribute 'use_auto_smooth'",
        "traceback": "Traceback...",
    }
    results = {"11_tool_verify_parts": _verify_result_with_failures([fp])}
    out = collect_runtime_failures(results)
    assert len(out) == 1
    assert out[0]["name"] == "FanBlade"
    assert out[0]["error_class"] == "AttributeError"


def test_collect_skips_records_without_name():
    """Defensive: a malformed failure record (no name) should be skipped
    so we don't try to dispatch a fix task to ``None``."""
    fp_bad = {"stage": "import"}
    fp_good = {"name": "Leg", "lower_name": "leg",
               "stage": "build_call", "error_class": "E", "error_msg": "m"}
    results = {"11_tool_verify_parts":
               _verify_result_with_failures([fp_bad, fp_good])}
    out = collect_runtime_failures(results)
    assert len(out) == 1
    assert out[0]["name"] == "Leg"


def test_verify_parts_output_not_misread_as_judge():
    """Regression for jet_engine_v4: verify_parts originally output
    ``passed: list[str]`` (the names that built OK). all_judge_results
    used to check ``"passed" in output`` — that classified verify as a
    judge, and latest_judge_passed returned True (non-empty list is
    truthy), so stop_condition_met short-circuited the fix-loop on the
    first iter despite 4 parts failing to build.

    The fix: all_judge_results requires ``passed`` to be a BOOL. verify's
    output is renamed to ``passed_parts`` defensively. Both protections
    must hold for the fix-loop to remain reliable."""
    from topos.orchestrator.fix_loop import all_judge_results, latest_judge_passed
    # Even if a legacy/buggy tool emits a list under "passed", it must NOT
    # be treated as a judge:
    legacy_verify = TaskResult(
        id="11_tool_verify_parts",
        kind="tool",
        success=False,
        duration_s=2.0,
        output={"passed": ["PartA", "PartB"], "failed_parts": [{"name": "PartC"}]},
    )
    results = {"11_tool_verify_parts": legacy_verify}
    assert all_judge_results(results) == []
    assert latest_judge_passed(results) is None


def test_collect_ignores_non_tool_results():
    """An agent task that happens to have a `failed_parts` key in its
    output dict should NOT be misinterpreted as a tool's runtime failure."""
    agent_r = TaskResult(
        id="03_agent_part_fan_blade",
        kind="agent",
        success=True,
        duration_s=120.0,
        output={"failed_parts": [{"name": "Spurious"}]},
    )
    results = {"03_agent_part_fan_blade": agent_r}
    assert collect_runtime_failures(results) == []


# --- build_runtime_fix_tasks ----------------------------------------------


def _fan_blade_agent_task() -> AgentTask:
    """Mirror of what plan_generator emits for a part — used by the fix-loop
    to inherit skills/tools/timeout."""
    return AgentTask(
        id="05_agent_part_fan_blade",
        goal="write parts/fan_blade.py",
        skills=["topos_part_geometry", "topos_bpy_docs"],
        allowed_tools=["Read", "Edit", "Write", "Glob", "Bash"],
        timeout_s=600,
    )


def test_build_runtime_fix_tasks_empty_when_clean():
    results = {"11_tool_verify_parts": _clean_verify_result()}
    assert build_runtime_fix_tasks(results, next_iter=1) == []


def test_build_runtime_fix_emits_one_task_per_failed_part():
    fp = {
        "name": "FanBlade", "lower_name": "fan_blade",
        "stage": "build_call",
        "error_class": "AttributeError",
        "error_msg": "'Mesh' object has no attribute 'use_auto_smooth'",
        "traceback": "Traceback (most recent call last):\n  ...",
    }
    results = {"11_tool_verify_parts": _verify_result_with_failures([fp])}
    original = [_fan_blade_agent_task()]

    tasks = build_runtime_fix_tasks(results, next_iter=1, original_tasks=original)

    assert len(tasks) == 1
    t = tasks[0]
    # Refactored 2026-05-11: runtime fix REUSES the original part agent's
    # task id so the DAG dep graph automatically wires downstream tasks
    # (verify, render, build, export, judge) to wait for the fix to land.
    # The `is_fix_rerun=True` flag is what tells the runner to skip the
    # carry-forward shortcut for this iter.
    assert t.id == "05_agent_part_fan_blade", (
        "runtime fix must reuse the origin part agent's id so downstream deps "
        "auto-resolve to the fixed code"
    )
    assert t.is_fix_rerun is True, \
        "runtime fix must mark is_fix_rerun=True so runner re-executes"
    # Inherited from the original part agent
    assert "topos_bpy_docs" in t.skills
    assert "Bash" in t.allowed_tools
    # Prompt contains the traceback so the agent can locate the bug
    assert "use_auto_smooth" in t.goal
    assert "AttributeError" in t.goal
    # Prompt names the file to edit
    assert "parts/fan_blade.py" in t.goal


def test_build_runtime_fix_dedups_by_part_name():
    """If two tools both report FanBlade failing (e.g. verify AND a
    follow-up render), only one fix task is emitted."""
    fp1 = {"name": "FanBlade", "lower_name": "fan_blade",
           "stage": "build_call", "error_class": "E", "error_msg": "boom"}
    fp2 = {"name": "FanBlade", "lower_name": "fan_blade",
           "stage": "build_call", "error_class": "E", "error_msg": "boom"}
    results = {
        "11_tool_verify_parts": _verify_result_with_failures([fp1]),
        "12_tool_render_parts": TaskResult(
            id="12_tool_render_parts", kind="tool", success=False,
            duration_s=1.0, output={"failed_parts": [fp2]},
        ),
    }
    tasks = build_runtime_fix_tasks(results, next_iter=1)
    assert len(tasks) == 1


def test_build_runtime_fix_falls_back_when_original_missing():
    """If the original part AgentTask isn't in original_tasks (e.g. plan
    was hand-authored), the fix task should still emit with a sensible
    default skill set."""
    fp = {"name": "Leg", "lower_name": "leg",
          "stage": "build_call", "error_class": "TypeError", "error_msg": "boom"}
    results = {"11_tool_verify_parts": _verify_result_with_failures([fp])}
    tasks = build_runtime_fix_tasks(results, next_iter=1, original_tasks=None)
    assert len(tasks) == 1
    t = tasks[0]
    # Without an origin to reuse the id from, fall back to the legacy
    # disambiguated id so the task still runs (it just won't be on the
    # critical DAG path until plan.json defines the part agent).
    assert t.id == "99_agent_fix_part_leg_runtime"
    assert t.is_fix_rerun is True
    # Default skill set narrow to the part-fix essentials
    assert "topos_part_geometry" in t.skills
    assert "topos_bpy_docs" in t.skills


def test_runtime_fix_inherits_origin_backend():
    """Same bug class as the judge-fix path: hardcoded backend="claude" silently
    broke gemini/codex plans. Runtime fix tasks must inherit the origin part
    agent's backend so the runner can locate it."""
    fp = {"name": "FanBlade", "lower_name": "fan_blade",
          "stage": "build_call", "error_class": "TypeError", "error_msg": "boom"}
    results = {"11_tool_verify_parts": _verify_result_with_failures([fp])}
    origin = AgentTask(
        id="05_agent_part_fan_blade", goal="x", backend="gemini",
        skills=["topos_part_geometry"], allowed_tools=["Read", "Edit", "Write"],
    )
    tasks = build_runtime_fix_tasks(results, next_iter=1, original_tasks=[origin])
    assert len(tasks) == 1
    assert tasks[0].backend == "gemini"
