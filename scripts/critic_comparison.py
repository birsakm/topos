"""Cross-critic comparison: same 8 cabinet views + same rubric → 5 critics
each score the result. Compare overall scores, per-criterion breakdown,
duration, cost, and feedback specificity.

Usage:
    python scripts/critic_comparison.py [workspace_slug]

Defaults to ``cab_a9_palace3`` since it has 8 rendered views + a clean src/
tree that source-aware CLI critics can inspect.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from topos.agents.visual_critic.base import (
    CriticInputs, load_rubric, make_critic,
)


def _inject_openai_key_from_codex() -> bool:
    """Extract OPENAI_API_KEY from ~/.codex/auth.json and inject into env."""
    if os.environ.get("OPENAI_API_KEY"):
        return True
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.is_file():
        return False
    try:
        data = json.loads(auth_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    key = data.get("OPENAI_API_KEY")
    if key:
        os.environ["OPENAI_API_KEY"] = key
        return True
    return False


def _inject_gemini_key_from_topos() -> bool:
    """Lift the Gemini key from topos config and inject as env (gemini-cli wants env)."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return True
    from topos import config as cfg
    eff = cfg.load_effective_config()
    key = (eff.get("image_gen", {}).get("gemini", {}).get("api_key")
           or eff.get("visual_critic", {}).get("gemini_vision", {}).get("api_key"))
    if key:
        os.environ["GEMINI_API_KEY"] = key
        return True
    return False


def run_one(backend_name: str, rubric_name: str, workspace: Path,
             images: list[Path]) -> dict:
    rubric = load_rubric(rubric_name)
    rubric.judge_backend = backend_name
    try:
        critic = make_critic(rubric)
    except Exception as e:
        return {"backend": backend_name, "error": f"factory failed: {e}"}

    metadata = {"workspace_path": str(workspace)}
    start = time.monotonic()
    try:
        result = critic.evaluate(
            CriticInputs(images=images, metadata=metadata), rubric,
        )
        duration = time.monotonic() - start
        return {
            "backend": backend_name,
            "passed": result.passed,
            "overall_score": result.overall_score,
            "per_criterion": {k: v.get("score") for k, v in result.per_criterion.items()},
            "suggested_fixes": result.suggested_fixes[:3],   # first 3 only
            "duration_s": duration,
            "cost_usd": result.cost_usd,
            "usage": result.usage,
        }
    except Exception as e:
        return {
            "backend": backend_name,
            "error": f"evaluate failed: {e}",
            "duration_s": time.monotonic() - start,
        }


def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "cab_a9_palace3"
    ws = Path(f"/lab/yipeng/topos/outputs/{slug}")
    images = sorted(ws.glob("artifacts/view_*.png"))
    if not images:
        sys.exit(f"no rendered views found at {ws}/artifacts/view_*.png")

    print(f"workspace: {ws}")
    print(f"images: {len(images)} views")
    print(f"rubric: articulated_object_v1")
    print()

    # Auth setup
    has_openai = _inject_openai_key_from_codex()
    has_gemini = _inject_gemini_key_from_topos()
    print(f"auth: OPENAI={'✓' if has_openai else '✗'}  "
          f"GEMINI={'✓' if has_gemini else '✗'}  "
          f"claude=subscription")
    print()

    backends = [
        "claude_vision",   # source-aware CLI
        "codex_cli",       # source-aware CLI
        "gemini_cli",      # source-aware CLI
        "openai_vision",   # HTTP API, images only
        "gemini_vision",   # HTTP API, images only
    ]

    all_results = []
    for b in backends:
        print(f"\n=== running {b} ===")
        result = run_one(b, "articulated_object_v1", ws, images)
        all_results.append(result)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
        else:
            print(f"  overall={result['overall_score']:.3f}  passed={result['passed']}  "
                  f"dur={result['duration_s']:.1f}s  cost=${result['cost_usd']:.4f}")
            for c, s in result['per_criterion'].items():
                if s is not None:
                    print(f"    {c:<25} {s:.2f}")
            if result['suggested_fixes']:
                print(f"  top fix: {result['suggested_fixes'][0][:200]}")

    out_path = ws / "critic_comparison.json"
    out_path.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\n→ full results: {out_path}")

    # Comparison table
    print("\n\n" + "=" * 80)
    print("COMPARISON TABLE")
    print("=" * 80)
    header = f"{'backend':<16} {'overall':>8} {'pass':>5} {'duration':>10} {'cost':>10}"
    print(header)
    print("-" * len(header))
    for r in all_results:
        if "error" in r:
            print(f"{r['backend']:<16} {'ERROR':>8}  {'—':>5} {r.get('duration_s', 0):>9.1f}s {'$0.00':>10}")
        else:
            print(f"{r['backend']:<16} {r['overall_score']:>8.3f}  {('✓' if r['passed'] else '✗'):>5} "
                  f"{r['duration_s']:>9.1f}s ${r['cost_usd']:>8.4f}")


if __name__ == "__main__":
    main()
