"""Sticky-pass policy unit tests.

Once a per-part judge passes, the part is "frozen" for the rest of the
run. Re-judging would waste compute AND risk a judge-sampling-noise
false-fail (e.g. cab_a4_perpart Handle 0.745 → 0.662 → 0.360 across
iters with no mesh change between iter1 and iter2).
"""

from __future__ import annotations

import pytest

from topos.orchestrator.fix_loop import (
    _PART_FIX_RE,
    _PART_JUDGE_RE,
    all_judge_results,
    assembly_judge_passed,
    build_fix_tasks,
    frozen_parts,
    latest_judge_passed,
)
from topos.orchestrator.results import TaskResult


def _judge(tid: str, *, passed: bool, score: float = 0.7) -> TaskResult:
    return TaskResult(
        id=tid, kind="tool", success=True, duration_s=1.0,
        output={
            "passed": passed,
            "overall_score": score,
            "per_criterion": {},
            "suggested_fixes": [] if passed else [f"fix something in {tid}"],
        },
    )


# ---------- regex sanity ----------

def test_part_judge_regex_matches_canonical_form():
    m = _PART_JUDGE_RE.match("06_tool_judge_part_frame")
    assert m and m.group("name") == "frame"


def test_part_judge_regex_rejects_assembly_judge():
    assert _PART_JUDGE_RE.match("14_tool_judge") is None


def test_part_fix_regex_matches_canonical_form():
    m = _PART_FIX_RE.match("99_agent_fix_part_drawer")
    assert m and m.group("name") == "drawer"


def test_part_fix_regex_rejects_assembly_fix():
    assert _PART_FIX_RE.match("99_agent_fix") is None


# ---------- frozen_parts ----------

def test_frozen_parts_empty_when_no_judges_passed():
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=False, score=0.3),
        "07_tool_judge_part_drawer": _judge("07_tool_judge_part_drawer", passed=False, score=0.4),
    }
    assert frozen_parts(results) == set()


def test_frozen_parts_picks_up_passed_judges():
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=True, score=0.75),
        "07_tool_judge_part_drawer": _judge("07_tool_judge_part_drawer", passed=False, score=0.3),
        "08_tool_judge_part_handle": _judge("08_tool_judge_part_handle", passed=True, score=0.8),
    }
    assert frozen_parts(results) == {"frame", "handle"}


def test_frozen_parts_ignores_assembly_judge():
    """Only per-part judges can freeze a part. Assembly judge never freezes anything."""
    results = {
        "14_tool_judge": _judge("14_tool_judge", passed=True, score=0.8),
    }
    assert frozen_parts(results) == set()


# ---------- build_fix_tasks integrates frozen status implicitly ----------

def test_build_fix_tasks_emits_one_per_failing_judge():
    """Failing part judge yields a per-part fix; failing assembly yields the assembly fix.

    Refactored 2026-05-11: with ``original_tasks`` provided, fix tasks REUSE
    the origin agent ids (so downstream DAG deps auto-resolve to the fix).
    Without ``original_tasks`` (this test), fall back to the legacy
    ``99_agent_fix*`` ids so the framework still produces tasks that can run."""
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=False, score=0.3),
        "14_tool_judge": _judge("14_tool_judge", passed=False, score=0.5),
    }
    fix_tasks = build_fix_tasks(results, next_iter=1)
    ids = {t.id for t in fix_tasks}
    assert ids == {"99_agent_fix_part_frame", "99_agent_fix"}
    # Every fix must carry the marker that tells the runner to skip
    # carry-forward — otherwise the iter would no-op the same prior result.
    for t in fix_tasks:
        assert t.is_fix_rerun is True, \
            f"{t.id} must set is_fix_rerun=True to bypass carry-forward"


def test_build_fix_tasks_reuses_origin_id_when_provided():
    """When ``original_tasks`` is given, per-part fix tasks REUSE the origin
    part agent's id and the assembly fix reuses the build agent's id. This
    is what makes the dep graph auto-correct without runner-side rewiring."""
    from topos.orchestrator.tasks import AgentTask
    origin_frame = AgentTask(
        id="03_agent_part_frame", goal="orig frame",
        deps=["01_agent_design"],
        allowed_tools=["Read", "Edit", "Write"],
        skills=["topos_part_geometry"],
    )
    origin_build = AgentTask(
        id="20_agent_build", goal="orig build",
        deps=["03_agent_part_frame"],
        allowed_tools=["Read", "Edit"],
        skills=["topos_bpy_docs"],
    )
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=False, score=0.3),
        "14_tool_judge": _judge("14_tool_judge", passed=False, score=0.5),
    }
    fix_tasks = build_fix_tasks(
        results, next_iter=1, original_tasks=[origin_frame, origin_build],
    )
    by_id = {t.id: t for t in fix_tasks}
    # Part fix reused the origin part agent's id
    assert "03_agent_part_frame" in by_id, "per-part fix must reuse origin id"
    pf = by_id["03_agent_part_frame"]
    assert pf.is_fix_rerun is True
    assert pf.deps == ["01_agent_design"], "fix inherits origin's deps"
    # Assembly fix reused the build agent's id (downstream tools dep on
    # 20_agent_build, so they auto-wait for the assembly fix to land)
    assert "20_agent_build" in by_id, "assembly fix must reuse build agent's id"
    ab = by_id["20_agent_build"]
    assert ab.is_fix_rerun is True


def test_build_fix_tasks_skips_passed_judges():
    """Passed judges produce no fix task even if other judges fail."""
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=True, score=0.8),
        "07_tool_judge_part_drawer": _judge("07_tool_judge_part_drawer", passed=False, score=0.4),
    }
    ids = {t.id for t in build_fix_tasks(results, next_iter=1)}
    assert ids == {"99_agent_fix_part_drawer"}


def test_build_fix_tasks_empty_when_all_pass():
    """If every judge passed, no fix tasks. Iteration loop should terminate."""
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=True, score=0.8),
        "14_tool_judge": _judge("14_tool_judge", passed=True, score=0.7),
    }
    assert build_fix_tasks(results, next_iter=1) == []


def test_build_fix_tasks_stops_when_assembly_passes_despite_failing_part():
    """F2: once the whole-object ASSEMBLY judge passes, the loop stops — a
    failing per-part shape critic on a minor part must NOT keep generating
    fixes (which burns iters and can regress an already-passing assembly,
    observed: 0.92 → 0.84 in a later per-part-chasing iter)."""
    results = {
        "08_tool_judge": _judge("08_tool_judge", passed=True, score=0.80),   # assembly PASS
        "02_subgraph_parts__01_tool_judge_part_seat":
            _judge("02_subgraph_parts__01_tool_judge_part_seat", passed=False, score=0.49),
    }
    assert assembly_judge_passed(results) is True
    assert build_fix_tasks(results, next_iter=1) == []


def test_build_fix_tasks_still_fixes_parts_when_assembly_fails():
    """The gate is assembly-PASS, not assembly-presence: while the assembly is
    still failing, per-part fixes keep flowing (and the assembly fix too)."""
    results = {
        "08_tool_judge": _judge("08_tool_judge", passed=False, score=0.55),
        "02_subgraph_parts__01_tool_judge_part_seat":
            _judge("02_subgraph_parts__01_tool_judge_part_seat", passed=False, score=0.49),
    }
    assert assembly_judge_passed(results) is False
    ids = {t.id for t in build_fix_tasks(results, next_iter=1)}
    assert "99_agent_fix" in ids                    # assembly fix
    assert "99_agent_fix_part_seat" in ids          # per-part fix


# ---------- latest_judge_passed semantics ----------

def test_latest_judge_passed_requires_all():
    """Returns True only when EVERY judge passed — not just the assembly."""
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=True, score=0.8),
        "14_tool_judge": _judge("14_tool_judge", passed=True, score=0.7),
    }
    assert latest_judge_passed(results) is True


def test_latest_judge_passed_false_when_any_fails():
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=True, score=0.8),
        "07_tool_judge_part_drawer": _judge("07_tool_judge_part_drawer", passed=False, score=0.4),
    }
    assert latest_judge_passed(results) is False


def test_latest_judge_passed_none_when_no_judges():
    assert latest_judge_passed({}) is None


# ---------- fix-task backend inheritance ----------

def test_per_part_fix_inherits_origin_backend():
    """Plans authored with a non-claude backend (gemini, codex, ...) must see
    fix tasks generated with the SAME backend — else the runner can't find a
    matching registration and the fix silently no-ops as ``no backend
    registered for 'claude'``. This is the exact bug observed in
    outputs/cab_gemini_test on 2026-05-13."""
    from topos.orchestrator.tasks import AgentTask
    origin = AgentTask(
        id="03_agent_part_frame", goal="orig", backend="gemini",
        deps=["01_agent_design"],
        allowed_tools=["Read", "Edit", "Write"],
        skills=["topos_part_geometry"],
    )
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=False, score=0.3),
    }
    fix_tasks = build_fix_tasks(results, next_iter=1, original_tasks=[origin])
    assert len(fix_tasks) == 1
    assert fix_tasks[0].backend == "gemini"


def test_assembly_fix_inherits_build_backend():
    from topos.orchestrator.tasks import AgentTask
    build_origin = AgentTask(
        id="20_agent_build", goal="orig build", backend="codex",
        deps=[], allowed_tools=["Read", "Edit"],
    )
    results = {
        "14_tool_judge": _judge("14_tool_judge", passed=False, score=0.5),
    }
    fix_tasks = build_fix_tasks(results, next_iter=1, original_tasks=[build_origin])
    assert len(fix_tasks) == 1
    assert fix_tasks[0].backend == "codex"


def test_fix_falls_back_to_any_agent_when_origin_missing():
    """If no origin is found (hand-authored plan with non-conventional ids),
    pick any AgentTask's backend as a representative — plans are typically
    homogeneous in backend, so this is the right default."""
    from topos.orchestrator.tasks import AgentTask
    other_agent = AgentTask(id="x_some_other_agent", goal="x", backend="gemini")
    results = {
        "06_tool_judge_part_unknown": _judge("06_tool_judge_part_unknown", passed=False, score=0.3),
    }
    fix_tasks = build_fix_tasks(results, next_iter=1, original_tasks=[other_agent])
    assert len(fix_tasks) == 1
    assert fix_tasks[0].backend == "gemini"


def test_fix_defaults_to_claude_when_plan_has_no_agents():
    """Degenerate case: no AgentTask in original_tasks at all. The framework's
    documented default (config_defaults.yaml: backends.default: claude) applies."""
    results = {
        "06_tool_judge_part_x": _judge("06_tool_judge_part_x", passed=False, score=0.3),
    }
    fix_tasks = build_fix_tasks(results, next_iter=1, original_tasks=[])
    assert len(fix_tasks) == 1
    assert fix_tasks[0].backend == "claude"


# ---------- saturation early-stop ----------

from topos.orchestrator.fix_loop import iter_improved, judge_scores_snapshot


def test_iter_improved_true_when_a_score_moved_enough():
    prev = {"14_tool_judge": 0.50}
    cur  = {"14_tool_judge": 0.62}  # +0.12
    assert iter_improved(prev, cur, min_delta=0.05) is True


def test_iter_improved_false_when_no_score_moves_enough():
    """Cost-saturation case: nothing budged more than min_delta this iter."""
    prev = {"06_tool_judge_part_frame": 0.62, "14_tool_judge": 0.55}
    cur  = {"06_tool_judge_part_frame": 0.63, "14_tool_judge": 0.54}  # max |Δ| = 0.01
    assert iter_improved(prev, cur, min_delta=0.05) is False


def test_iter_improved_false_when_only_drops_no_gain():
    """A score that only DROPPED is NOT progress — the fix loop is re-running
    without making anything better, exactly the cost-burning case to stop.
    (Old behavior counted any |Δ| as 'movement'; that meant a 9-part object
    almost never early-stopped because something always jittered.)"""
    prev = {"08_tool_judge_part_handle": 0.75}
    cur  = {"08_tool_judge_part_handle": 0.40}  # -0.35, no upward gain
    assert iter_improved(prev, cur, min_delta=0.05) is False


def test_iter_improved_false_on_wiggle_without_real_gain():
    """Mixed jitter (one part down, one barely up) with no part improving by
    >= min_delta → stop. This is the multi-part saturation case."""
    prev = {"a": 0.50, "b": 0.60, "c": 0.55}
    cur  = {"a": 0.46, "b": 0.62, "c": 0.55}  # max gain = +0.02 (< 0.05)
    assert iter_improved(prev, cur, min_delta=0.05) is False


def test_iter_improved_true_when_one_part_really_gains_amid_drops():
    """Genuine progress on at least one part counts, even if others wobble."""
    prev = {"a": 0.40, "b": 0.70}
    cur  = {"a": 0.58, "b": 0.66}  # a +0.18 (real gain), b -0.04 (noise)
    assert iter_improved(prev, cur, min_delta=0.05) is True


def test_iter_improved_true_when_judge_set_changed():
    """Sticky-pass means some judges drop out of the snapshot in later iters.
    Treat that as 'progress possible', not stagnation."""
    prev = {"06_tool_judge_part_frame": 0.30, "14_tool_judge": 0.45}
    cur = {"14_tool_judge": 0.45}  # Frame frozen, no longer in snapshot
    assert iter_improved(prev, cur, min_delta=0.05) is True


def test_iter_improved_true_when_no_shared_keys():
    """Edge case: completely different judges (shouldn't happen but be safe)."""
    assert iter_improved({"a": 0.5}, {"b": 0.6}, min_delta=0.05) is True


def test_judge_scores_snapshot_extracts_overall_score():
    results = {
        "06_tool_judge_part_frame": _judge("06_tool_judge_part_frame", passed=True, score=0.75),
        "07_tool_judge_part_drawer": _judge("07_tool_judge_part_drawer", passed=False, score=0.35),
        "14_tool_judge": _judge("14_tool_judge", passed=True, score=0.80),
    }
    snap = judge_scores_snapshot(results)
    assert snap == {
        "06_tool_judge_part_frame": 0.75,
        "07_tool_judge_part_drawer": 0.35,
        "14_tool_judge": 0.80,
    }


def test_judge_scores_snapshot_ignores_non_judges():
    """Only tool results with 'overall_score' in output count (i.e. judges)."""
    non_judge = TaskResult(
        id="11_tool_render_multiview", kind="tool", success=True,
        duration_s=2.0, output={"images": 8}  # no overall_score
    )
    results = {
        "11_tool_render_multiview": non_judge,
        "14_tool_judge": _judge("14_tool_judge", passed=True, score=0.7),
    }
    snap = judge_scores_snapshot(results)
    assert snap == {"14_tool_judge": 0.7}
