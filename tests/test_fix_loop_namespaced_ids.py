"""Regression: fix_loop must match namespaced part ids from SubgraphTask
runtime expansion (ADR-0008).

Background: optimus_prime_v4 ran 11 part agents via subgraph expansion
(ids like ``02_subgraph_parts__03_agent_part_pelvis``). When verify_parts
failed on pelvis, fix_loop's _find_part_agent_task couldn't locate the
origin task (the old regex required ``^\\d+_agent_part_...``, no namespace
prefix tolerated). Fix tasks then fell back to fresh ``99_agent_fix_part_
<lower>_runtime`` ids with no deps — so downstream verify/render/judges,
which dep on the dynamic child's id, never saw the fix and never re-ran.
The result: framework reported ``99_agent_fix_part_pelvis_runtime [ok]``
but verify still ``[FAIL]`` because it was never re-dispatched.

This file pins:
  - the regex extensions accept namespaced ids
  - build_runtime_fix_tasks given namespaced origin tasks emits fix tasks
    that REUSE the namespaced id (so the downstream auto-rebind works)
"""

from __future__ import annotations

from topos.orchestrator.fix_loop import (
    _PART_AGENT_RE, _PART_JUDGE_RE, _PART_FIX_RE,
    _find_part_agent_task,
    build_runtime_fix_tasks,
)
from topos.orchestrator.results import TaskResult
from topos.orchestrator.tasks import AgentTask


def test_part_agent_regex_matches_namespaced():
    """Both flat and namespaced agent_part ids match."""
    assert _PART_AGENT_RE.match("03_agent_part_frame").group("name") == "frame"
    assert _PART_AGENT_RE.match(
        "02_subgraph_parts__03_agent_part_pelvis"
    ).group("name") == "pelvis"
    assert _PART_AGENT_RE.match(
        "deep__nested__04_agent_part_drawer_top"
    ).group("name") == "drawer_top"


def test_part_judge_regex_matches_namespaced():
    assert _PART_JUDGE_RE.match("07_tool_judge_part_handle").group("name") == "handle"
    assert _PART_JUDGE_RE.match(
        "02_subgraph_parts__07_tool_judge_part_handle"
    ).group("name") == "handle"


def test_part_fix_regex_matches_namespaced():
    assert _PART_FIX_RE.match("99_agent_fix_part_frame").group("name") == "frame"
    assert _PART_FIX_RE.match(
        "02_subgraph_parts__99_agent_fix_part_frame"
    ).group("name") == "frame"


def test_part_agent_regex_rejects_non_part_ids():
    """Sanity: not just any namespaced id matches."""
    assert _PART_AGENT_RE.match("02_subgraph_parts__zz_tool_verify_parts") is None
    assert _PART_AGENT_RE.match("01_agent_design") is None
    assert _PART_AGENT_RE.match("03_agent_build") is None


def test_find_part_agent_task_finds_namespaced():
    namespaced = AgentTask(
        id="02_subgraph_parts__03_agent_part_pelvis",
        goal="g", backend="claude",
    )
    flat = AgentTask(id="03_agent_build", goal="g")
    found = _find_part_agent_task([flat, namespaced], "pelvis")
    assert found is namespaced


def test_runtime_fix_reuses_namespaced_id():
    """The end-to-end fix: when verify_parts reports a runtime failure for a
    namespaced part agent, the fix task must REUSE that namespaced id (so
    downstream tasks that dep on it auto-rebind to the fixed version) and
    inherit its deps."""
    origin_id = "02_subgraph_parts__03_agent_part_pelvis"
    origin = AgentTask(
        id=origin_id,
        goal="orig pelvis", backend="claude",
        deps=["01_agent_design"],
        skills=["topos_part_geometry", "topos_bpy_docs"],
        allowed_tools=["Read", "Edit", "Write"],
    )
    verify_result = TaskResult(
        id="02_subgraph_parts__zz_tool_verify_parts",
        kind="tool", success=False, duration_s=2.0,
        iteration=0,
        output={
            "success": False,
            "failed_parts": [{
                "name": "Pelvis",
                "lower_name": "pelvis",
                "status": "failed",
                "stage": "build_call",
                "error_class": "TypeError",
                "error_msg": "enum FAST not found in (FLOAT, EXACT, MANIFOLD)",
                "traceback": "Traceback...",
            }],
            "passed_parts": [],
        },
    )
    results = {verify_result.id: verify_result}
    fix_tasks = build_runtime_fix_tasks(
        results, next_iter=1, original_tasks=[origin],
    )
    assert len(fix_tasks) == 1
    fix = fix_tasks[0]
    # CRITICAL: reuses the namespaced id, not a fresh 99_agent_fix_*_runtime.
    assert fix.id == origin_id
    assert fix.is_fix_rerun is True
    # Inherits origin's deps so the dep graph still resolves cleanly.
    assert fix.deps == ["01_agent_design"]
    # Inherits skills + tools (sanity).
    assert "topos_part_geometry" in fix.skills


def test_runtime_fix_falls_back_when_origin_absent():
    """Belt-and-suspenders: if for some reason the origin isn't in the
    plan we're scanning, the fallback path still emits a fix task (with
    a fresh id, no deps) so the framework doesn't silently drop the fix."""
    verify_result = TaskResult(
        id="zz_tool_verify_parts",
        kind="tool", success=False, duration_s=1.0, iteration=0,
        output={
            "success": False,
            "failed_parts": [{
                "name": "Mystery", "lower_name": "mystery",
                "stage": "build_call",
                "error_class": "ValueError", "error_msg": "..", "traceback": "..",
            }],
            "passed_parts": [],
        },
    )
    fix_tasks = build_runtime_fix_tasks(
        {verify_result.id: verify_result}, next_iter=1, original_tasks=[],
    )
    assert len(fix_tasks) == 1
    assert fix_tasks[0].id == "99_agent_fix_part_mystery_runtime"
