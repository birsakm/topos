"""Deterministic metric extraction from trajectory files. No LLM needed.

Reads run_report.json, per-task trajectory dirs (transcript.jsonl, result.json,
score.json, output.json), and design.json to produce structured RunAnalysis.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaskMetrics:
    task_id: str
    kind: str  # "agent" | "tool"
    iter: int
    duration_s: float
    cost_usd: float
    success: bool
    # Agent-specific:
    n_tool_calls: int = 0
    tool_call_summary: list[dict] = field(default_factory=list)
    skills_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    n_edit_cycles: int = 0  # how many times same file was edited
    # Judge-specific:
    score: float | None = None
    per_criterion: dict[str, float] | None = None
    suggested_fixes: list[str] | None = None
    # Tool-specific:
    tool_name: str | None = None
    tool_output_summary: str | None = None  # 1-line summary of output.json


@dataclass
class RunAnalysis:
    slug: str
    n_parts: int
    n_joints: int
    total_cost_usd: float
    total_wall_time_s: float
    iterations: int
    final_score: float
    passed: bool
    tasks: list[TaskMetrics]
    design_summary: dict  # part names, texture kinds, joint types
    warnings: list[str]  # from verify_parts, bbox errors, etc.


def extract_run_metrics(workspace: Path) -> RunAnalysis:
    """Extract all structured metrics from a completed run.

    ``workspace`` is the root of the workspace, e.g. ``outputs/<slug>/``.
    """
    report_path = workspace / "run_report.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"no run_report.json at {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    slug = report.get("project", workspace.name)
    cost_section = report.get("cost") or {}
    total_cost = cost_section.get("total_usd_all_iters", 0.0)
    if total_cost == 0.0:
        for h in report.get("history") or []:
            total_cost += h.get("cost_usd", 0.0)
    total_wall = report.get("duration_s", 0.0)
    iterations = report.get("iteration_count", 1)
    final_score = report.get("final_judge_score", 0.0)
    # Some reports use "final_judge_score", others embed it in history
    if final_score == 0.0:
        history = report.get("history") or []
        if history:
            final_score = history[-1].get("judge_score", 0.0) or 0.0
    passed = bool(report.get("final_judge_passed", False))

    # Design summary
    design_summary, n_parts, n_joints = _extract_design_summary(workspace)

    # Warnings from verify_parts / export warnings
    warnings = _collect_warnings(workspace, report)

    # Per-task metrics
    tasks = _extract_task_metrics(workspace, report)

    return RunAnalysis(
        slug=slug,
        n_parts=n_parts,
        n_joints=n_joints,
        total_cost_usd=total_cost,
        total_wall_time_s=total_wall,
        iterations=iterations,
        final_score=final_score,
        passed=passed,
        tasks=tasks,
        design_summary=design_summary,
        warnings=warnings,
    )


# ---------- internals ----------


def _extract_design_summary(workspace: Path) -> tuple[dict, int, int]:
    """Read design.json and return (summary_dict, n_parts, n_joints)."""
    design_path = workspace / "src" / "design.json"
    if not design_path.is_file():
        return {}, 0, 0
    try:
        design = json.loads(design_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, 0, 0

    parts = design.get("parts") or []
    joints = design.get("joints") or []
    part_names = [p.get("name", "?") for p in parts]
    texture_kinds = {}
    for p in parts:
        tex = p.get("texture") or {}
        texture_kinds[p.get("name", "?")] = tex.get("kind", "none")
    joint_types = [j.get("type", "?") for j in joints]

    summary = {
        "part_names": part_names,
        "texture_kinds": texture_kinds,
        "joint_types": joint_types,
        "robot_name": design.get("robot_name", design.get("name", "")),
        "description": design.get("description", "")[:200],
    }
    return summary, len(parts), len(joints)


def _collect_warnings(workspace: Path, report: dict) -> list[str]:
    """Collect warnings from export outputs and verify_parts results."""
    warnings: list[str] = []

    # Walk trajectory output.json files for tool tasks that have warnings
    traj_root = workspace / "trajectories"
    if traj_root.is_dir():
        for traj_dir in sorted(traj_root.iterdir()):
            output_path = traj_dir / "output.json"
            if not output_path.is_file():
                continue
            try:
                output = json.loads(output_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for w in output.get("warnings") or []:
                warnings.append(f"[{traj_dir.name}] {w}")

    return warnings


def _extract_task_metrics(workspace: Path, report: dict) -> list[TaskMetrics]:
    """Build TaskMetrics for every task in the run_report results."""
    results = report.get("results") or {}
    traj_root = workspace / "trajectories"
    tasks: list[TaskMetrics] = []

    for task_id, r in results.items():
        kind = r.get("kind", "unknown")
        # Skip subgraph aggregate entries — they roll up into child tasks
        if kind == "subgraph":
            continue

        iteration = r.get("iteration", 0)
        duration_s = r.get("duration_s", 0.0)
        cost_usd = r.get("cost_usd", 0.0)
        success = bool(r.get("success", False))

        tm = TaskMetrics(
            task_id=task_id,
            kind=kind,
            iter=iteration,
            duration_s=duration_s,
            cost_usd=cost_usd,
            success=success,
        )

        # Find the trajectory dir: <task_id>_iter<N>
        traj_dir = traj_root / f"{task_id}_iter{iteration}"
        if not traj_dir.is_dir():
            # The run_report may record a task at iteration=2 but the
            # actual trajectory was written at the iteration the agent ran
            # (e.g. iter0). Search for the latest matching dir.
            candidates = sorted(
                traj_root.glob(f"{task_id}_iter*"),
                key=lambda p: p.name,
                reverse=True,
            )
            traj_dir = candidates[0] if candidates else None
        if traj_dir is not None and not traj_dir.is_dir():
            traj_dir = None

        if kind == "agent" and traj_dir:
            _enrich_agent_metrics(tm, traj_dir)
        elif kind == "tool":
            _enrich_tool_metrics(tm, task_id, traj_dir)

        tasks.append(tm)

    return tasks


def _enrich_agent_metrics(tm: TaskMetrics, traj_dir: Path) -> None:
    """Parse transcript.jsonl (streaming) for tool call patterns."""
    jsonl_path = traj_dir / "transcript.jsonl"
    if not jsonl_path.is_file():
        return

    tool_calls: list[str] = []
    skill_reads: list[str] = []
    files_written: list[str] = []
    files_edited: list[str] = []

    try:
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "assistant":
                    continue
                content = (d.get("message") or {}).get("content") or []
                if not isinstance(content, list):
                    continue
                for block in content:
                    if block.get("type") != "tool_use":
                        continue
                    name = block.get("name", "")
                    inp = block.get("input") or {}
                    tool_calls.append(name)

                    # Detect skill reads
                    if name == "Read":
                        fp = inp.get("file_path", "")
                        if ".topos_skills/" in fp:
                            # Extract skill name from path
                            m = re.search(r"\.topos_skills/([^/]+)", fp)
                            if m:
                                skill_reads.append(m.group(1))

                    # Detect file writes
                    if name == "Write":
                        fp = inp.get("file_path", "")
                        if fp:
                            files_written.append(fp)

                    # Detect file edits
                    if name == "Edit":
                        fp = inp.get("file_path", "")
                        if fp:
                            files_edited.append(fp)
    except OSError:
        return

    tm.n_tool_calls = len(tool_calls)
    tm.tool_call_summary = [
        {"tool": name, "count": count}
        for name, count in Counter(tool_calls).most_common()
    ]
    tm.skills_read = sorted(set(skill_reads))
    tm.files_written = sorted(set(files_written))
    # n_edit_cycles: number of files that were edited more than once
    edit_counts = Counter(files_edited)
    tm.n_edit_cycles = sum(1 for c in edit_counts.values() if c > 1)


def _enrich_tool_metrics(
    tm: TaskMetrics, task_id: str, traj_dir: Path | None,
) -> None:
    """Extract tool name and score/output summary from trajectory files."""
    # Infer tool name from task_id pattern: "NN_tool_<name>_..."
    m = re.search(r"tool_(\w+)", task_id)
    if m:
        tm.tool_name = m.group(1)

    if traj_dir is None:
        return

    # Score data (for judge tasks)
    score_path = traj_dir / "score.json"
    if score_path.is_file():
        try:
            score_data = json.loads(score_path.read_text(encoding="utf-8"))
            tm.score = score_data.get("overall_score")
            raw_criteria = score_data.get("per_criterion") or {}
            tm.per_criterion = {}
            for k, v in raw_criteria.items():
                if isinstance(v, dict):
                    tm.per_criterion[k] = v.get("score", 0.0)
                elif isinstance(v, (int, float)):
                    tm.per_criterion[k] = float(v)
                # skip non-numeric values (lists, strings, etc.)
            tm.suggested_fixes = score_data.get("suggested_fixes") or []
        except (json.JSONDecodeError, OSError):
            pass

    # Output summary (for non-judge tool tasks)
    output_path = traj_dir / "output.json"
    if output_path.is_file():
        try:
            output = json.loads(output_path.read_text(encoding="utf-8"))
            tm.tool_output_summary = _summarise_output(output)
        except (json.JSONDecodeError, OSError):
            pass


def _summarise_output(output: dict) -> str:
    """Produce a 1-line summary of a tool task output.json."""
    success = output.get("success", "?")
    parts: list[str] = [f"success={success}"]

    # GLB export
    if "glb_path" in output:
        size = output.get("byte_size", 0)
        parts.append(f"glb={output['glb_path']} ({size} bytes)")
    # URDF export
    if "urdf_path" in output:
        parts.append(f"urdf={output['urdf_path']}")
    # Render
    if "image_paths" in output:
        paths = output["image_paths"]
        parts.append(f"{len(paths)} images")
    # Texture gen
    if "image_path" in output and "kind" in output:
        parts.append(f"kind={output['kind']}")
        if output.get("note"):
            parts.append(output["note"][:80])
    # Verify parts
    if "violations" in output:
        n = len(output["violations"])
        parts.append(f"{n} violation(s)")
    # Warnings
    n_warn = len(output.get("warnings") or [])
    if n_warn:
        parts.append(f"{n_warn} warning(s)")

    return "; ".join(parts)
