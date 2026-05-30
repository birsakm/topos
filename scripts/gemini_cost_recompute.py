"""Re-compute USD cost for a topos run by walking trajectories.

Gemini agent + gemini_vision critic both return tokens but not USD, and the
framework only learned to multiply by a price table after some runs had
already completed (cost_usd=0 frozen into run_report.json). This script
re-derives the real cost from raw token counts × the price table in
``topos.backends._pricing``, and prints a per-task breakdown.

Usage:
  python scripts/gemini_cost_recompute.py outputs/cab_gemini_flash_palace5
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from topos.backends._pricing import gemini_cost_usd


def _agent_cost(transcript_json: Path) -> tuple[str, float, dict]:
    """Read an agent task's transcript.json (gemini-cli's final result event)
    and return (model, cost_usd, raw_stats)."""
    d = json.loads(transcript_json.read_text())
    stats = d.get("stats") or {}
    models = stats.get("models") or {}
    # Most calls touch one model; if multiple, sum cost across each model's stats.
    cost = 0.0
    primary_model = ""
    if models:
        for mname, mstats in models.items():
            primary_model = primary_model or mname
            cost += gemini_cost_usd(
                mname,
                input_tokens=mstats.get("input_tokens", 0) or 0,
                output_tokens=mstats.get("output_tokens", 0) or 0,
                cached_input_tokens=mstats.get("cached", 0) or 0,
            )
    else:
        # Fall back to top-level stats (older shape).
        cost = gemini_cost_usd(
            stats.get("model"),
            input_tokens=stats.get("input_tokens", 0) or 0,
            output_tokens=stats.get("output_tokens", 0) or 0,
            cached_input_tokens=stats.get("cached", 0) or 0,
        )
        primary_model = stats.get("model") or ""
    return primary_model, cost, stats


def _judge_cost(score_json: Path) -> tuple[str, float, dict]:
    """Read a judge tool's score.json and re-derive cost from its usage block."""
    d = json.loads(score_json.read_text())
    usage = d.get("usage") or {}
    model = usage.get("model") or ""
    cost = gemini_cost_usd(
        model,
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        cached_input_tokens=usage.get("cached_input_tokens", 0) or 0,
    )
    return model, cost, usage


def main(workspace: Path) -> None:
    traj_root = workspace / "trajectories"
    if not traj_root.is_dir():
        print(f"no trajectory dir at {traj_root}", file=sys.stderr)
        sys.exit(1)

    total_agent = 0.0
    total_judge = 0.0
    rows: list[tuple[str, str, str, float, dict]] = []  # (task_id, kind, model, cost, raw)

    for task_dir in sorted(traj_root.iterdir()):
        if not task_dir.is_dir():
            continue
        # Agent task: has transcript.json with gemini result event
        tj = task_dir / "transcript.json"
        sj = task_dir / "score.json"
        if "_agent_" in task_dir.name and tj.is_file():
            try:
                model, cost, stats = _agent_cost(tj)
                rows.append((task_dir.name, "agent", model, cost, stats))
                total_agent += cost
            except (json.JSONDecodeError, KeyError):
                pass
        elif "_judge" in task_dir.name and sj.is_file():
            try:
                model, cost, usage = _judge_cost(sj)
                rows.append((task_dir.name, "judge", model, cost, usage))
                total_judge += cost
            except (json.JSONDecodeError, KeyError):
                pass

    print(f"=== gemini cost recompute: {workspace.name} ===")
    print(f"{'task':<45} {'kind':<6} {'model':<28} {'in':>10} {'out':>8} {'cached':>10} {'$cost':>9}")
    print("-" * 122)
    for tid, kind, model, cost, raw in rows:
        in_t = raw.get("input_tokens", 0) or 0
        out_t = raw.get("output_tokens", 0) or 0
        cached_t = raw.get("cached", 0) or raw.get("cached_input_tokens", 0) or 0
        if not in_t and kind == "agent":
            # Try per-model breakdown
            models = (raw.get("models") or {})
            if models:
                m0 = next(iter(models.values()))
                in_t = m0.get("input_tokens", 0) or 0
                out_t = m0.get("output_tokens", 0) or 0
                cached_t = m0.get("cached", 0) or 0
        print(f"{tid:<45} {kind:<6} {model:<28} {in_t:>10,} {out_t:>8,} {cached_t:>10,} ${cost:>7.4f}")

    print("-" * 122)
    total = total_agent + total_judge
    print(f"{'TOTALS':<45} {'':<6} {'':<28} {'':>10} {'':>8} {'':>10}")
    print(f"  agent  ${total_agent:>7.4f}  ({sum(1 for r in rows if r[1] == 'agent')} calls)")
    print(f"  judge  ${total_judge:>7.4f}  ({sum(1 for r in rows if r[1] == 'judge')} calls)")
    print(f"  TOTAL  ${total:>7.4f}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/gemini_cost_recompute.py <workspace_path>", file=sys.stderr)
        sys.exit(1)
    main(Path(sys.argv[1]).resolve())
