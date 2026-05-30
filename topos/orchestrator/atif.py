"""ATIF (Agent Trajectory Interchange Format) writer for Topos tool tasks.

Reference: https://github.com/harbor-framework/harbor — RFC 0001, v1.7.

Topos uses ATIF only for **tool tasks** (verify_parts / render_* / export_* /
judge). Agent tasks keep claude CLI's native ``--output-format stream-json``
event stream because it's already a well-defined per-event format from
Anthropic and re-encoding it would lose information.

A tool task fits ATIF naturally: it's a function-call-and-result pair, which
maps to one ToolCall step (source=system) plus one Observation step (also
source=system, since the framework — not an LLM — generated the result).
The whole tool execution is a 2-step Trajectory in this model.

Why ATIF instead of just writing a custom JSON shape: the Harbor viewer
(``apps/viewer``) and the SFT/RL pipelines that consume Trajectory objects
can read these files directly. Cross-tool interchange is the whole point
of the format.

The schema models here are a SUBSET sufficient for tool-task emission
(no Step.content / Observation.image_source / SubagentTrajectoryRef);
adding fields later is non-breaking per ATIF's optional-field convention.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ATIF_VERSION = "ATIF-v1.7"
_TOPOS_AGENT_NAME = "topos_runner"


def _now_iso() -> str:
    """ISO-8601 timestamp with UTC tz. ATIF allows any ISO 8601; UTC is the
    least-ambiguous choice for cross-environment artifacts."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_tool_trajectory(
    trajectory_dir: Path,
    *,
    task_id: str,
    iteration: int,
    tool_name: str,
    arguments: dict[str, Any],
    output: dict[str, Any],
    duration_s: float,
    success: bool,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> Path:
    """Emit ``trajectory.json`` (ATIF schema_version 1.7) describing one
    tool execution. Returns the written path.

    Two-step shape:
        step 1: source=system, tool_calls=[<tool_name>(args)]
        step 2: source=system, observation={tool_call_id, result=<output>}

    Both steps have source=system because tool tasks are deterministic
    framework dispatch — no LLM agent is making the call here. (Per-task
    LLM costs surface in final_metrics when the tool internally invokes
    one, e.g. the judge tool calling claude_vision.)
    """
    if started_at is None:
        started_at = _now_iso()
    if finished_at is None:
        finished_at = _now_iso()

    tool_call_id = f"{task_id}_iter{iteration}"
    cost_usd = float(output.get("cost_usd") or 0.0)
    usage = output.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}

    trajectory = {
        "schema_version": _ATIF_VERSION,
        "trajectory_id": tool_call_id,
        "session_id": task_id,        # group all iters of one task under task_id
        "agent": {
            "name": _TOPOS_AGENT_NAME,
            "type": "framework",      # NOT an LLM agent — this is the runner
            "version": "topos",
            # Tool definitions could go here if we want full SFT-readiness.
            # Omitted for v1: tools are exposed in plan.json, not here.
        },
        "steps": [
            {
                "step_id": 1,
                "timestamp": started_at,
                "source": "system",
                "tool_calls": [
                    {
                        "tool_call_id": tool_call_id,
                        "function_name": tool_name,
                        "arguments": _sanitize_args(arguments),
                    }
                ],
            },
            {
                "step_id": 2,
                "timestamp": finished_at,
                "source": "system",
                "observation": {
                    "tool_call_id": tool_call_id,
                    "result": _sanitize_result(output),
                },
            },
        ],
        "final_metrics": {
            "duration_s": duration_s,
            "cost_usd": cost_usd,
            "success": success,
            "usage": usage,
        },
    }

    path = trajectory_dir / "trajectory.json"
    path.write_text(json.dumps(trajectory, indent=2, default=str), encoding="utf-8")
    return path


def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Coerce non-JSON-serializable arg values (e.g. Path) to strings so the
    trajectory is portable across machines. We don't redact anything — the
    workspace path leaks anyway via the agent prompts."""
    return {k: _to_json_safe(v) for k, v in args.items()}


def _sanitize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Mirror of _sanitize_args but for the tool's output dict. Same
    Path-to-str coercion. Keep the full output rather than summarizing —
    downstream consumers want everything (per_criterion feedback,
    warnings, exit_code, etc.)."""
    return {k: _to_json_safe(v) for k, v in result.items()}


def _to_json_safe(v: Any) -> Any:
    """Recursive coercion for nested dicts/lists. Path → str; anything else
    weird falls through to json.dumps's default=str at write time."""
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, dict):
        return {k: _to_json_safe(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_to_json_safe(x) for x in v]
    if isinstance(v, tuple):
        return [_to_json_safe(x) for x in v]
    return v
