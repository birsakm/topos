"""Pin the deterministic-tool carry-forward behaviour added to ``runner.py``.

When iter > 0 and a ToolTask's tool is marked ``deterministic=True``, the
runner skips re-execution IFF none of the tool's upstream tasks actually
re-run this iter (they all carry-forward, sticky-pass, or are otherwise
absent from ``pending``). The output is byte-identical to the prior iter
in that case, so re-running is pure waste — see runner.py:285-340.

These tests instrument ``_execute_tasks`` directly so we can observe what
runs vs. what's carried forward without spinning up real backends or tools.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.orchestrator.plan_schema import Plan
from topos.orchestrator.runner import Runner, TaskResult
from topos.orchestrator.tasks import AgentTask, ToolTask
from topos.tools import registry as tool_registry


# ---------- fixture: a stub deterministic + stochastic pair ----------

@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a clean registry slate. We restore after."""
    snapshot = dict(tool_registry._REGISTRY)
    tool_registry.clear()
    try:
        yield
    finally:
        tool_registry._REGISTRY.clear()
        tool_registry._REGISTRY.update(snapshot)


def _register_pair():
    """Register a deterministic and a stochastic tool with no-op funcs."""
    @tool_registry.tool(
        "_test_det",
        description="deterministic test tool",
        input_schema={"type": "object"},
        deterministic=True,
    )
    def _det(**kw): return {"success": True}

    @tool_registry.tool(
        "_test_stoch",
        description="stochastic test tool",
        input_schema={"type": "object"},
        deterministic=False,
    )
    def _stoch(**kw): return {"success": True}


def _mk_runner(tmp_path: Path) -> Runner:
    from topos.workspace import Workspace
    ws = Workspace.create("p", "rigid", base=tmp_path)
    runner = Runner.__new__(Runner)
    runner.ws = ws
    runner.plan = Plan(project="p", tasks=[])
    runner.backends = {}
    runner.resume = False
    runner._cost_accumulator = 0.0
    runner.max_parallel = 1
    return runner


def _stub(runner: Runner, ran: list[str]):
    """Replace _run_agent and _run_tool with stubs that record execution."""
    def fake(task, *args, **kw):
        ran.append(task.id)
        return TaskResult(
            id=task.id,
            kind="tool" if isinstance(task, ToolTask) else "agent",
            success=True, duration_s=0.0, cost_usd=0.0,
            iteration=kw.get("iteration", 0),
        )
    runner._run_agent = fake          # type: ignore[assignment]
    runner._run_tool = fake           # type: ignore[assignment]


# ---------- the four cases ----------

def test_deterministic_tool_skipped_when_upstream_unchanged(tmp_path):
    """iter > 0, det tool's only dep is an AgentTask that succeeded earlier
    in this run → AgentTask is carry-forwarded, det tool should also skip."""
    _register_pair()
    runner = _mk_runner(tmp_path)
    ran: list[str] = []
    _stub(runner, ran)

    agent = AgentTask(id="01_agent_design", goal="g")
    det_tool = ToolTask(id="02_tool_det", tool="_test_det", deps=["01_agent_design"])

    # iter 0: both run
    results: dict[str, TaskResult] = {}
    runner._execute_tasks([agent, det_tool], results, iteration=0)
    assert ran == ["01_agent_design", "02_tool_det"]
    assert results["02_tool_det"].iteration == 0

    # iter 1: agent carries forward, det tool should NOT re-run
    ran.clear()
    runner._execute_tasks([agent, det_tool], results, iteration=1)
    assert ran == [], f"expected nothing to run, got {ran}"
    # iteration stamp updated, note records the deterministic-skip origin
    assert results["02_tool_det"].iteration == 1
    assert "deterministic-skip" in (results["02_tool_det"].note or "")


def test_stochastic_tool_reruns_even_when_upstream_unchanged(tmp_path):
    """Same plan shape but the tool is marked deterministic=False → it MUST
    re-run every iter. Guards against accidentally flipping judge to skip."""
    _register_pair()
    runner = _mk_runner(tmp_path)
    ran: list[str] = []
    _stub(runner, ran)

    agent = AgentTask(id="01_agent_design", goal="g")
    stoch = ToolTask(id="02_tool_stoch", tool="_test_stoch", deps=["01_agent_design"])

    results: dict[str, TaskResult] = {}
    runner._execute_tasks([agent, stoch], results, iteration=0)
    assert ran == ["01_agent_design", "02_tool_stoch"]

    ran.clear()
    runner._execute_tasks([agent, stoch], results, iteration=1)
    # agent carries forward, stochastic tool re-runs
    assert ran == ["02_tool_stoch"]


def test_deterministic_tool_reruns_when_upstream_actually_executes(tmp_path):
    """If a 99_agent_fix lands in this iter and is upstream of the det tool,
    we MUST re-run — inputs changed. Pin the inverse case."""
    _register_pair()
    runner = _mk_runner(tmp_path)
    ran: list[str] = []
    _stub(runner, ran)

    # Simulate iter 0: design + det_tool already succeeded.
    results: dict[str, TaskResult] = {
        "01_agent_design": TaskResult(
            id="01_agent_design", kind="agent", success=True,
            duration_s=0.1, cost_usd=0.0, iteration=0,
        ),
        "02_tool_det": TaskResult(
            id="02_tool_det", kind="tool", success=True,
            duration_s=0.1, cost_usd=0.0, iteration=0,
        ),
    }
    # iter 1: fix agent runs, det_tool depends on it (sim: build.py rewritten).
    fix = AgentTask(id="99_agent_fix_assembly", goal="g")
    agent = AgentTask(id="01_agent_design", goal="g")
    det_tool = ToolTask(
        id="02_tool_det", tool="_test_det",
        deps=["01_agent_design", "99_agent_fix_assembly"],
    )

    runner._execute_tasks([fix, agent, det_tool], results, iteration=1)
    # fix runs fresh; design carries forward; det_tool re-runs because a dep
    # (the fix) is in pending.
    assert "99_agent_fix_assembly" in ran
    assert "02_tool_det" in ran
    assert "01_agent_design" not in ran  # carry-forward


def test_unknown_tool_name_does_not_crash_carry_forward(tmp_path):
    """If a ToolTask's `tool` name isn't in the registry (e.g. plan typo,
    not-yet-imported plugin), the dispatch path runs as before — the
    carry-forward branch should fail safe and treat it as non-deterministic."""
    runner = _mk_runner(tmp_path)
    ran: list[str] = []
    _stub(runner, ran)

    results: dict[str, TaskResult] = {
        "02_tool_x": TaskResult(
            id="02_tool_x", kind="tool", success=True,
            duration_s=0.1, cost_usd=0.0, iteration=0,
        ),
    }
    ghost = ToolTask(id="02_tool_x", tool="not_registered", deps=[])

    # iter 1: must not raise; tool re-runs (conservative fallback).
    runner._execute_tasks([ghost], results, iteration=1)
    assert ran == ["02_tool_x"]
