"""Matrix runner — sweeps {conditions} × {projections} × {prompts} as cells.

Cells run concurrently in their own `python run.py` subprocesses (so logs
don't interleave). Each cell's full output goes to a per-cell log file
under <out_tag>/logs/. Nano Banana 2 is happy with many concurrent
requests, so default concurrency is one worker per cell.

Manifest is written incrementally so progress survives Ctrl-C.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import itertools
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Make repo root importable so child run.py subprocesses also can.
_REPO_ROOT = _HERE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _common import slugify  # noqa: E402


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--part", required=True)
    p.add_argument("--prompt", default=None,
                   help="single prompt; mutually exclusive with --prompts-file")
    p.add_argument("--prompts-file", default=None,
                   help="newline-separated prompts file (lines starting with # ignored)")
    p.add_argument("--conditions", default="silhouette,ao,depth")
    p.add_argument("--projections", default="project_from_view,analytical_view")
    p.add_argument("--view", default="front")
    p.add_argument("--size", type=int, default=1024)
    p.add_argument("--out-tag", default=None)
    p.add_argument("--skip-gemini", action="store_true")
    p.add_argument("--keep-blend", action="store_true")
    p.add_argument("--cost-ceiling-usd", type=float, default=3.0,
                   help="abort if cumulative gemini cost exceeds this (best-effort: "
                        "in-flight cells may still complete)")
    p.add_argument("--concurrency", type=int, default=0,
                   help="number of cells to run concurrently. 0 = one per cell. "
                        "Default 0 because Nano Banana handles concurrent calls fine.")
    p.add_argument("--cell-timeout-s", type=int, default=480,
                   help="kill any single cell that runs longer than this (s).")
    return p


def _load_prompts(args) -> list[str]:
    if args.prompts_file and args.prompt:
        raise SystemExit("use only one of --prompt or --prompts-file")
    if args.prompts_file:
        text = Path(args.prompts_file).read_text()
        return [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
    if args.prompt:
        return [args.prompt]
    raise SystemExit("either --prompt or --prompts-file is required")


def _run_single_cell_subprocess(
    *,
    cond: str,
    proj: str,
    prompt: str,
    base_args,
    exp_root: Path,
) -> dict:
    """Spawn `python run.py` for one cell. Returns a result dict (always)."""
    prompt_slug = slugify(prompt)
    cell_id = f"{base_args.part}__{base_args.view}__{cond}__{proj}__{prompt_slug}"
    log_path = exp_root / "logs" / f"{cell_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(_HERE / "run.py"),
        "--slug",       base_args.slug,
        "--part",       base_args.part,
        "--prompt",     prompt,
        "--condition",  cond,
        "--projection", proj,
        "--view",       base_args.view,
        "--size",       str(base_args.size),
        "--out-tag",    base_args.out_tag,
    ]
    if base_args.skip_gemini:
        cmd.append("--skip-gemini")
    if base_args.keep_blend:
        cmd.append("--keep-blend")

    t0 = time.monotonic()
    proc_failed_reason = None
    try:
        with open(log_path, "w") as f:
            f.write(f"# CMD: {' '.join(cmd)}\n\n")
            f.flush()
            subprocess.run(
                cmd, stdout=f, stderr=subprocess.STDOUT,
                text=True, timeout=base_args.cell_timeout_s,
                check=False,
            )
    except subprocess.TimeoutExpired:
        proc_failed_reason = f"cell exceeded --cell-timeout-s={base_args.cell_timeout_s}"
    except Exception as e:
        proc_failed_reason = f"subprocess error: {e}"
    duration = time.monotonic() - t0

    # Each successful cell writes result.json into its cell_dir. Find that
    # by deterministic path construction (matches run.run_cell layout).
    slug_dir = _REPO_ROOT / "outputs" / base_args.slug
    cell_dir = slug_dir / "artifacts" / "uv_tex_exp" / base_args.out_tag / cell_id
    result_json = cell_dir / "result.json"

    base = {
        "condition": cond, "projection": proj, "prompt": prompt,
        "prompt_slug": prompt_slug,
        "cell_dir": str(cell_dir),
        "cell_log": str(log_path),
        "wall_duration_s": duration,
    }
    if result_json.is_file():
        payload = json.loads(result_json.read_text())
        return {**base, **payload}
    return {
        **base,
        "success": False,
        "skipped_gemini": base_args.skip_gemini,
        "cost_usd": 0.0,
        "gemini_duration_s": 0.0,
        "gemini_error": proc_failed_reason or "no result.json (cell crashed)",
        "phase1_duration_s": 0.0,
        "phase3_duration_s": 0.0,
    }


def main() -> int:
    args = _build_argparser().parse_args()
    conditions = _split_csv(args.conditions)
    projections = _split_csv(args.projections)
    prompts = _load_prompts(args)
    args.out_tag = args.out_tag or _dt.datetime.now().strftime("matrix_%Y%m%d_%H%M%S")

    slug_dir = _REPO_ROOT / "outputs" / args.slug
    exp_root = slug_dir / "artifacts" / "uv_tex_exp" / args.out_tag
    exp_root.mkdir(parents=True, exist_ok=True)
    manifest_path = exp_root / "manifest.json"

    combos = list(itertools.product(conditions, projections, prompts))
    concurrency = args.concurrency if args.concurrency > 0 else len(combos)

    print(f"[matrix] {len(combos)} cells, concurrency={concurrency}: "
          f"{len(conditions)} conditions × {len(projections)} projections × "
          f"{len(prompts)} prompts → {exp_root}")

    cells: list[dict] = []
    total_cost = 0.0
    aborted = False
    lock = threading.Lock()

    def _write_manifest() -> None:
        manifest_path.write_text(json.dumps({
            "slug": args.slug,
            "part": args.part,
            "view": args.view,
            "size": args.size,
            "conditions": conditions,
            "projections": projections,
            "prompts": prompts,
            "out_tag": args.out_tag,
            "concurrency": concurrency,
            "total_cost_usd": total_cost,
            "cells": cells,
        }, indent=2))

    t_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_combo = {
            pool.submit(_run_single_cell_subprocess,
                        cond=cond, proj=proj, prompt=prompt,
                        base_args=args, exp_root=exp_root): (cond, proj, prompt)
            for (cond, proj, prompt) in combos
        }
        n_done = 0
        for fut in as_completed(future_to_combo):
            cond, proj, prompt = future_to_combo[fut]
            try:
                result = fut.result()
            except Exception as e:
                result = {
                    "condition": cond, "projection": proj, "prompt": prompt,
                    "prompt_slug": slugify(prompt),
                    "success": False,
                    "cost_usd": 0.0,
                    "gemini_error": f"future raised: {e}",
                }
            with lock:
                cells.append(result)
                total_cost += float(result.get("cost_usd") or 0.0)
                n_done += 1
                ok = "✓" if result.get("success") else "✗"
                err = result.get("gemini_error") or ""
                print(f"[matrix] {n_done}/{len(combos)} {ok} "
                      f"cond={cond:10s} proj={proj:18s} "
                      f"prompt={prompt[:30]!r:32s} "
                      f"cost=${result.get('cost_usd', 0):.4f} "
                      f"wall={result.get('wall_duration_s', 0):.1f}s"
                      + (f"  ERR: {err[:80]}" if err else ""))
                _write_manifest()
                if total_cost > args.cost_ceiling_usd:
                    print(f"[matrix] cost ceiling exceeded "
                          f"(${total_cost:.2f} > ${args.cost_ceiling_usd:.2f}); "
                          f"won't start more cells, in-flight will finish")
                    aborted = True
                    # Don't break — let in-flight cells finish so manifest is complete.

    n_ok   = sum(1 for c in cells if c.get("success"))
    n_fail = len(cells) - n_ok
    total_wall = time.monotonic() - t_start
    print(f"\n[matrix] done in {total_wall:.1f}s. "
          f"ok={n_ok} fail={n_fail} total_cost=${total_cost:.4f} aborted={aborted}")
    print(f"[matrix] manifest: {manifest_path}")
    return 0 if (n_fail == 0 and not aborted) else 1


if __name__ == "__main__":
    sys.exit(main())
