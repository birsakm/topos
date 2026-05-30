"""Per-task and per-run result types written by the DAG runner.

These dataclasses are pure data — no ``Runner`` reference, no methods that
mutate orchestration state. They are imported by the runner itself, by
tests, and by anything that reads ``run_report.json`` after a run
completes.

Layout convention: sibling to ``plan_schema.py`` and ``tasks.py`` — both
of which are also pure data files under ``topos/orchestrator/``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TaskResult:
    """One agent/tool task's outcome from a single iteration."""
    id: str
    kind: str
    success: bool
    duration_s: float
    note: str | None = None
    output: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    model_usage: dict[str, Any] = field(default_factory=dict)
    iteration: int = 0  # which fix-loop pass this result belongs to


@dataclass
class IterationSnapshot:
    """One slice of run history — captures the state at the end of an
    iteration (original plan = iter 0; fix-loop iter N = N>0)."""
    iteration: int
    success: bool
    judge_passed: bool | None
    judge_score: float | None
    duration_s: float
    cost_usd: float


@dataclass
class RunReport:
    """Final per-run artifact serialized to ``outputs/<slug>/run_report.json``.

    ``results`` holds the LATEST iteration's per-task outcome (older
    iterations live under ``trajectories/<task_id>_iter<N>/``). ``history`` is the
    per-iteration summary including the running cost.
    """
    project: str
    success: bool
    results: dict[str, TaskResult]
    duration_s: float
    iteration_count: int = 1
    history: list[IterationSnapshot] = field(default_factory=list)
    total_cost_usd_all_iters: float = 0.0
    final_judge_passed: bool | None = None

    @property
    def total_cost_usd(self) -> float:
        """Cost reflected by the *latest* iteration's results (last-iter view)."""
        return sum(r.cost_usd for r in self.results.values())

    @property
    def cost_by_kind(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in self.results.values():
            out[r.kind] = out.get(r.kind, 0.0) + r.cost_usd
        return out

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "success": self.success,
            "duration_s": self.duration_s,
            "iteration_count": self.iteration_count,
            "final_judge_passed": self.final_judge_passed,
            "cost": {
                "total_usd_last_iter": self.total_cost_usd,
                "total_usd_all_iters": self.total_cost_usd_all_iters,
                "by_kind_last_iter": self.cost_by_kind,
                "per_task_last_iter": {k: v.cost_usd for k, v in self.results.items()},
            },
            "history": [asdict(h) for h in self.history],
            "results": {k: asdict(v) for k, v in self.results.items()},
        }
