"""Runner-level test for SubgraphTask runtime expansion (ADR-0008).

Stubs agent and tool execution so we can drive the dispatcher end-to-end at
unit-test speed (no LLM calls, no Blender), proving that:

* a SubgraphTask spawns children deterministically from a design.json the
  parent agent wrote
* downstream deps wait for the subgraph (all-children-complete semantics)
* the saved ``plan.expanded.json`` snapshot reflects the post-expansion DAG
* a 5-drawer design produces 5 dynamic part-agents (the canonical
  scale-test from the plan file)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from topos.orchestrator import expand
from topos.orchestrator.plan_schema import Plan, load_plan
from topos.orchestrator.results import TaskResult
from topos.orchestrator.runner import Runner
from topos.orchestrator.tasks import AgentTask, SubgraphTask, ToolTask
from topos.workspace import Workspace


def _mk_runner(tmp_path: Path, plan: Plan, *, max_parallel: int = 4) -> Runner:
    ws = Workspace.create("p", "articulated", base=tmp_path)
    runner = Runner.__new__(Runner)
    runner.ws = ws
    runner.plan = plan
    runner.backends = {}
    runner.resume = False
    runner._cost_accumulator = 0.0
    runner.max_parallel = max_parallel
    runner._subgraph_children = {}
    return runner


def _stub_agent(runner: Runner, design_doc: dict | None = None):
    """Patch _run_agent. If the task id is the design agent and ``design_doc``
    is given, write it to src/design.json so the subgraph expander reads it
    on the next dispatch pass."""
    def fake(task: AgentTask, *, iteration: int) -> TaskResult:
        if "design" in task.id and design_doc is not None:
            (runner.ws.src_dir).mkdir(parents=True, exist_ok=True)
            (runner.ws.src_dir / "design.json").write_text(
                json.dumps(design_doc), encoding="utf-8"
            )
        return TaskResult(
            id=task.id, kind="agent", success=True, duration_s=0.01,
            cost_usd=0.0, iteration=iteration,
        )
    runner._run_agent = fake


def _stub_tool(runner: Runner):
    def fake(task: ToolTask, *, iteration: int) -> TaskResult:
        return TaskResult(
            id=task.id, kind="tool", success=True, duration_s=0.01,
            cost_usd=0.0, iteration=iteration, output={"tool": task.tool},
        )
    runner._run_tool = fake


def _plan_with_subgraph(tmp_path: Path) -> Plan:
    plan_dict = {
        "project": "p",
        "iter_policy": {"max_global_iters": 1},
        "tasks": [
            {"id": "01_design", "kind": "agent", "goal": "design"},
            {
                "id": "02_subgraph_parts",
                "kind": "subgraph",
                "expand_from": "src/design.json",
                "expansion_kind": "articulated_parts",
                "deps": ["01_design"],
            },
            {
                "id": "13_build",
                "kind": "agent",
                "goal": "build",
                "deps": ["02_subgraph_parts"],
            },
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_dict))
    return load_plan(plan_path)


def test_subgraph_spawns_three_part_agents(tmp_path: Path):
    """3 parts in design.json → 3 part-agents in expanded DAG."""
    plan = _plan_with_subgraph(tmp_path)
    runner = _mk_runner(tmp_path, plan)
    design = {"parts": [
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
        {"name": "Drawer", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
        {"name": "Handle", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ]}
    _stub_agent(runner, design_doc=design)
    _stub_tool(runner)

    tasks = plan.materialised()
    results: dict[str, TaskResult] = {}
    runner._execute_tasks(tasks, results, iteration=0)

    # subgraph completes once children resolve
    assert "02_subgraph_parts" in results
    assert results["02_subgraph_parts"].success is True
    # 3 dynamic agent_part_* under the subgraph namespace
    agent_part_ids = sorted(
        rid for rid in results if "__01_agent_part_" in rid or "__02_agent_part_" in rid or "__03_agent_part_" in rid
    )
    assert len(agent_part_ids) == 3
    # downstream build ran after subgraph completed
    assert "13_build" in results
    assert results["13_build"].success is True


def test_subgraph_five_drawers_dynamic(tmp_path: Path):
    """User-facing claim: a 5-drawer intent should produce 5 dynamic drawer
    agents even though plan.json has no per-drawer slots."""
    plan = _plan_with_subgraph(tmp_path)
    runner = _mk_runner(tmp_path, plan)
    design = {"parts": [
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
        *(
            {"name": f"Drawer{i}", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]}
            for i in range(1, 6)
        ),
    ]}
    _stub_agent(runner, design_doc=design)
    _stub_tool(runner)

    tasks = plan.materialised()
    results: dict[str, TaskResult] = {}
    runner._execute_tasks(tasks, results, iteration=0)

    drawer_agents = [
        rid for rid in results if "_agent_part_drawer" in rid
    ]
    assert len(drawer_agents) == 5, f"expected 5 drawer agents, got {drawer_agents}"
    # Each drawer's texture and judge_part also dispatched
    assert sum("_tool_texture_drawer" in rid for rid in results) == 5
    assert sum("_tool_judge_part_drawer" in rid for rid in results) == 5
    # Subgraph and downstream both succeeded
    assert results["02_subgraph_parts"].success is True
    assert results["13_build"].success is True


def test_plan_expanded_snapshot_written(tmp_path: Path):
    plan = _plan_with_subgraph(tmp_path)
    runner = _mk_runner(tmp_path, plan)
    _stub_agent(runner, design_doc={"parts": [
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
        {"name": "Drawer", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ]})
    _stub_tool(runner)
    runner._execute_tasks(plan.materialised(), {}, iteration=0)

    snapshot_path = runner.ws.root / "plan.expanded.json"
    assert snapshot_path.is_file()
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    ids = {t["id"] for t in snap["tasks"]}
    # static tasks present
    assert {"01_design", "02_subgraph_parts", "13_build"} <= ids
    # dynamic children present
    assert any("__01_agent_part_frame" in tid for tid in ids)
    assert any("__02_agent_part_drawer" in tid for tid in ids)


def test_subgraph_completes_after_synchronous_skip_cascade(tmp_path: Path):
    """Regression: optimus_prime_v4 deadlocked because a verify_parts failure
    caused render + judge_parts to be skipped SYNCHRONOUSLY in the for-loop
    (no worker pool round-trip), so _maybe_complete_subgraphs (which only
    fires after wait()) never marked the subgraph complete, and the
    downstream tasks (build, render_multiview, glb, urdf, judge) raised
    'runner deadlock: 5 task(s) pending with unsatisfiable deps'.

    Fix: call _maybe_complete_subgraphs in the no-workers branch before the
    deadlock check, so a fully-skipped subgraph still completes."""
    plan = _plan_with_subgraph(tmp_path)
    runner = _mk_runner(tmp_path, plan)
    _stub_agent(runner, design_doc={"parts": [
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
        {"name": "Drawer", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ]})

    def flaky_tool(task: ToolTask, *, iteration: int) -> TaskResult:
        # verify_parts fails → render + all judge_parts cascade-skip
        # synchronously in the dispatch for-loop.
        ok = "verify_parts" not in task.id
        return TaskResult(
            id=task.id, kind="tool", success=ok, duration_s=0.0,
            cost_usd=0.0, iteration=iteration,
        )
    runner._run_tool = flaky_tool

    results: dict[str, TaskResult] = {}
    # Must NOT raise — used to deadlock.
    runner._execute_tasks(plan.materialised(), results, iteration=0)

    # Subgraph completed (with failure status), and downstream cascade-skipped
    # cleanly instead of stranding.
    assert "02_subgraph_parts" in results
    assert results["02_subgraph_parts"].success is False
    assert "13_build" in results
    assert results["13_build"].success is False
    assert results["13_build"].note and "upstream failed" in results["13_build"].note


def test_subgraph_re_evaluates_when_children_status_changes(tmp_path: Path):
    """Regression: optimus_prime_v4 had 11/11 children TaskResult.success=True
    after fix iters but the subgraph kept iter-0's success=False (some
    children were initially cascade-skipped, fix-loop later re-ran them to
    success, but _maybe_complete_subgraphs deleted the subgraph from its
    bookkeeping after first computation and never re-checked). Fix: keep
    _subgraph_children populated forever, idempotently re-evaluate every
    round, mutate results only when the success state actually changed."""
    plan = _plan_with_subgraph(tmp_path)
    runner = _mk_runner(tmp_path, plan)

    # Simulate the v4 timeline: first call sees a child as failed
    # (sticky-pass / skip cascade), later call sees it as succeeded.
    call_count = {"n": 0}

    def stateful_tool(task: ToolTask, *, iteration: int) -> TaskResult:
        # First time this tool runs (iter 0 verify): fail.
        # Subsequent times (after fix iter): succeed.
        if "verify_parts" in task.id:
            call_count["n"] += 1
            ok = call_count["n"] > 1
            return TaskResult(
                id=task.id, kind="tool", success=ok, duration_s=0.0,
                cost_usd=0.0, iteration=iteration,
            )
        return TaskResult(
            id=task.id, kind="tool", success=True, duration_s=0.0,
            cost_usd=0.0, iteration=iteration,
        )
    runner._run_tool = stateful_tool
    _stub_agent(runner, design_doc={"parts": [
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ]})

    results: dict[str, TaskResult] = {}
    # First execution: verify fails, cascade-skip render+judge, subgraph fails.
    runner._execute_tasks(plan.materialised(), results, iteration=0)
    assert results["02_subgraph_parts"].success is False

    # Now simulate fix-loop pushing verify back through with the stateful_tool
    # (next call returns success). Re-running the children via _execute_tasks
    # (the iter>0 path the real fix-loop takes via combined task list).
    # Mark prior failed results stale so they re-dispatch.
    for key in [
        "02_subgraph_parts__zz_tool_verify_parts",
        "02_subgraph_parts__zz_tool_render_parts",
        "02_subgraph_parts__01_tool_judge_part_frame",
    ]:
        results.pop(key, None)
    # Resume-like: re-run with dynamic tasks visible.
    runner._execute_tasks(
        plan.materialised() + runner._dynamic_tasks,
        results, iteration=1,
    )
    # Now all children should be success=True AND the subgraph re-evaluated.
    assert results["02_subgraph_parts"].success is True, (
        "subgraph must re-evaluate to success=True after children flip"
    )


def test_subgraph_re_evaluation_is_noop_when_unchanged(tmp_path: Path):
    """Idempotency: re-running _maybe_complete_subgraphs when nothing
    changed must NOT overwrite the prior result (avoid churning iter
    stamps and plan.expanded.json snapshots on every wait round)."""
    plan = _plan_with_subgraph(tmp_path)
    runner = _mk_runner(tmp_path, plan)
    _stub_agent(runner, design_doc={"parts": [
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ]})
    _stub_tool(runner)
    results: dict[str, TaskResult] = {}
    runner._execute_tasks(plan.materialised(), results, iteration=0)
    sg_before = results["02_subgraph_parts"]
    assert sg_before.success is True

    # Re-run with same state — should not flip or churn.
    runner._execute_tasks(plan.materialised() + runner._dynamic_tasks,
                          results, iteration=1)
    sg_after = results["02_subgraph_parts"]
    assert sg_after.success is True
    # Same object identity (no recomputation) since status didn't change.
    assert sg_after is sg_before


def test_subgraph_child_failure_propagates(tmp_path: Path):
    """If a single child fails, the subgraph reports failure and downstream
    sees an upstream-failed result, not a phantom success."""
    plan = _plan_with_subgraph(tmp_path)
    runner = _mk_runner(tmp_path, plan)
    _stub_agent(runner, design_doc={"parts": [
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
        {"name": "Drawer", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ]})

    def flaky_tool(task: ToolTask, *, iteration: int) -> TaskResult:
        ok = "judge_part_frame" not in task.id  # frame judge_part fails
        return TaskResult(
            id=task.id, kind="tool", success=ok, duration_s=0.0,
            cost_usd=0.0, iteration=iteration,
        )
    runner._run_tool = flaky_tool

    results: dict[str, TaskResult] = {}
    runner._execute_tasks(plan.materialised(), results, iteration=0)

    sg_result = results["02_subgraph_parts"]
    assert sg_result.success is False
    assert "failed_children" in sg_result.output
    assert any("judge_part_frame" in cid for cid in sg_result.output["failed_children"])
    # downstream marked skipped, not run
    build = results["13_build"]
    assert build.success is False
    assert build.note and "upstream failed" in build.note
