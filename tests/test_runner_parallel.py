"""Parallel-dispatch behaviour of the DAG runner.

The runner now runs eligible tasks concurrently up to ``max_parallel``.
Tests verify three properties:

1. Parallelism actually parallelizes — wall-time for N independent
   tasks of duration D is < N×D when max_parallel >= 2.
2. ``max_parallel=1`` recovers strict sequential behaviour
   (regression check; same code path, just no concurrency).
3. Dependency order is still respected — a task waits for its deps.
4. Deadlock is detected if deps are unsatisfiable.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from topos.orchestrator.plan_schema import Plan
from topos.orchestrator.runner import Runner, TaskResult
from topos.orchestrator.tasks import AgentTask
from topos.workspace import Workspace


def _mk_runner(tmp_path: Path, max_parallel: int) -> Runner:
    ws = Workspace.create("p", "rigid", base=tmp_path)
    runner = Runner.__new__(Runner)
    runner.ws = ws
    runner.plan = Plan(project="p", tasks=[])
    runner.backends = {}
    runner.resume = False
    runner._cost_accumulator = 0.0
    runner.max_parallel = max_parallel
    return runner


def _fake_task(task_id: str, duration_s: float, deps=None):
    """Build a real AgentTask so isinstance dispatch inside _execute_tasks
    works as in production. The fake _run_agent stub is what introduces
    timing-only behaviour."""
    return AgentTask(
        id=task_id,
        goal=f"fake task {task_id}",
        deps=list(deps or []),
    )


def _stub_executor(runner: Runner, records: dict, duration_by_id: dict):
    """Patch ``_run_agent``/``_run_tool`` to sleep + record start/end times
    instead of calling LLMs or tools."""
    def fake_agent(task, *, iteration):
        start = time.monotonic()
        time.sleep(duration_by_id[task.id])
        end = time.monotonic()
        records[task.id] = (start, end)
        return TaskResult(
            id=task.id, kind="agent", success=True,
            duration_s=end - start, cost_usd=0.0, iteration=iteration,
        )
    runner._run_agent = fake_agent  # type: ignore[assignment]
    runner._run_tool = fake_agent   # type: ignore[assignment]


def test_parallel_dispatch_speedup(tmp_path: Path):
    """4 independent tasks (no deps) at 0.5s each: parallel ≤ 1.2s
    (well below 4×0.5=2.0s serial). Use a generous bound to avoid CI flakes."""
    runner = _mk_runner(tmp_path, max_parallel=4)
    records: dict[str, tuple[float, float]] = {}
    duration = {f"t{i}": 0.5 for i in range(4)}
    _stub_executor(runner, records, duration)

    tasks = [_fake_task(f"t{i}", 0.5) for i in range(4)]

    results: dict[str, TaskResult] = {}
    t0 = time.monotonic()
    runner._execute_tasks(tasks, results, iteration=0)
    wall = time.monotonic() - t0

    assert len(results) == 4
    assert all(r.success for r in results.values())
    # 4 tasks at 0.5s each, max_parallel=4 → ideal 0.5s. Allow 2x slack for CI.
    assert wall < 1.2, f"expected parallel speedup; wall={wall:.2f}s"


def test_sequential_when_max_parallel_1(tmp_path: Path):
    """max_parallel=1: 4 × 0.2s tasks should take ≥ 0.7s (close to 0.8s
    serial). Verifies the new code path falls back cleanly."""
    runner = _mk_runner(tmp_path, max_parallel=1)
    records: dict[str, tuple[float, float]] = {}
    duration = {f"t{i}": 0.2 for i in range(4)}
    _stub_executor(runner, records, duration)

    tasks = [_fake_task(f"t{i}", 0.2) for i in range(4)]
    results: dict[str, TaskResult] = {}
    t0 = time.monotonic()
    runner._execute_tasks(tasks, results, iteration=0)
    wall = time.monotonic() - t0

    assert len(results) == 4
    # Serial 4×0.2 = 0.8s. Allow modest slack down to 0.7 for measurement noise.
    assert 0.7 < wall < 1.3, f"expected sequential wall ~0.8s; wall={wall:.2f}s"


def test_deps_respected_in_parallel(tmp_path: Path):
    """A depends on B: A must start AFTER B finishes, even with high
    parallelism."""
    runner = _mk_runner(tmp_path, max_parallel=4)
    records: dict[str, tuple[float, float]] = {}
    duration = {"B": 0.4, "A": 0.1}
    _stub_executor(runner, records, duration)

    tasks = [_fake_task("B", 0.4), _fake_task("A", 0.1, deps=["B"])]
    results: dict[str, TaskResult] = {}
    runner._execute_tasks(tasks, results, iteration=0)

    b_end = records["B"][1]
    a_start = records["A"][0]
    assert a_start >= b_end - 0.01, (
        f"A must start after B finishes: a_start={a_start:.3f}, b_end={b_end:.3f}"
    )


def test_upstream_failure_short_circuits(tmp_path: Path):
    """If B fails, A (depends on B) is marked failed without running."""
    runner = _mk_runner(tmp_path, max_parallel=2)

    def runner_run_agent(task, *, iteration):
        if task.id == "B":
            return TaskResult(
                id="B", kind="agent", success=False, duration_s=0.0,
                note="forced failure", iteration=iteration,
            )
        # A should never reach here
        raise AssertionError(f"task {task.id} should not have run")

    runner._run_agent = runner_run_agent  # type: ignore[assignment]
    runner._run_tool = runner_run_agent   # type: ignore[assignment]

    tasks = [_fake_task("B", 0.0), _fake_task("A", 0.0, deps=["B"])]
    results: dict[str, TaskResult] = {}
    runner._execute_tasks(tasks, results, iteration=0)

    assert results["B"].success is False
    assert results["A"].success is False
    assert "upstream failed" in (results["A"].note or "")


def test_deadlock_raises(tmp_path: Path):
    """Task A depends on Z which is not in the task list → unsatisfiable.
    Runner should raise rather than spin forever."""
    runner = _mk_runner(tmp_path, max_parallel=2)
    records: dict[str, tuple[float, float]] = {}
    _stub_executor(runner, records, {})

    tasks = [_fake_task("A", 0.0, deps=["Z_doesnt_exist"])]
    results: dict[str, TaskResult] = {}
    with pytest.raises(RuntimeError, match="deadlock"):
        runner._execute_tasks(tasks, results, iteration=0)
