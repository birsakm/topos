"""Multi-view fusion orchestrator.

For one part + one prompt, runs N condition-image renders (one per view),
calls Gemini N times in parallel, then a single fusion phase 3 that
assigns each mesh face to whichever view's texture best matches its
normal. Output is a per-cell directory with all intermediate artifacts
plus 3 diagnostic renders.

Usage:
    python scripts/experiment_uv_texture/run_multiview.py \\
        --slug cab_a7_full --part Handle \\
        --prompt "golden dragon relief on dark bronze" \\
        --views front,back,left,right --condition ao
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_REPO_ROOT = _HERE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _common import CONDITION_PROMPT_PREFIX, slugify  # noqa: E402


def _run_blender_phase(*, script: Path, cwd: Path, args_obj: dict,
                       timeout_s: int) -> float:
    from topos.tools._blender_subprocess import run_blender
    t0 = time.monotonic()
    res = run_blender(
        script=script, cwd=cwd, timeout_s=timeout_s,
        script_args=[json.dumps(args_obj)],
    )
    dur = time.monotonic() - t0
    if not res.success:
        print(f"[mv] blender phase FAILED (exit={res.exit_code}) script={script.name}")
        print("---- blender stdout ----")
        print(res.stdout)
        print("---- blender stderr ----")
        print(res.stderr)
        raise RuntimeError(f"blender {script.name} failed")
    print(res.stdout)
    if res.stderr.strip():
        print("---- stderr ----")
        print(res.stderr)
    return dur


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--part", required=True)
    ap.add_argument("--prompt", required=True,
                    help="outer-shell texture prompt (applied to faces whose "
                         "normal points away from the part's bbox center)")
    ap.add_argument("--prompt-inner", default=None,
                    help="optional separate prompt for inner cavity walls "
                         "(faces whose normal points toward the bbox center). "
                         "Doubles Gemini cost. If omitted, the outer textures "
                         "are used for inner faces too.")
    ap.add_argument("--views", default="front,back,left,right,top,bottom",
                    help="comma-separated list of named views to fuse "
                         "(default includes top+bottom for full coverage)")
    ap.add_argument("--condition", default="ao",
                    choices=["silhouette", "ao", "depth", "normal", "cycles_diffuse"])
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--out-tag", default=None)
    ap.add_argument("--keep-blend", action="store_true")
    ap.add_argument("--no-glb", action="store_true",
                    help="skip GLB export (default: textured.glb is written next to the renders)")
    ap.add_argument("--soft-blend", action="store_true",
                    help="use shader-side per-pixel soft blending of N view "
                         "textures instead of hard per-face assignment; smoother "
                         "on curved surfaces, slightly higher GLB size")
    ap.add_argument("--max-gemini-attempts", type=int, default=3)
    args = ap.parse_args()

    views = [v.strip() for v in args.views.split(",") if v.strip()]
    if not views:
        raise SystemExit("--views must list at least one view")
    tag = args.out_tag or _dt.datetime.now().strftime("mv_%Y%m%d_%H%M%S")

    slug_dir = _REPO_ROOT / "outputs" / args.slug
    slug_src = slug_dir / "src"
    if not (slug_src / "build.py").is_file():
        raise FileNotFoundError(f"missing {slug_src/'build.py'}")

    exp_root = slug_dir / "artifacts" / "uv_tex_exp" / tag
    cell_id = f"{args.part}__multiview_{len(views)}__{args.condition}__{slugify(args.prompt)}"
    cell_dir = exp_root / cell_id
    cell_dir.mkdir(parents=True, exist_ok=True)
    print(f"[mv] cell_dir = {cell_dir}")
    print(f"[mv] views = {views}")

    # ===== Phase 1: condition renders (one Blender per view, in parallel) =====
    def _run_phase1(view: str) -> tuple[str, Path, Path, float]:
        cond_png = cell_dir / f"cond_{view}.png"
        cam_json = cell_dir / f"cam_{view}.json"
        dur = _run_blender_phase(
            script=_HERE / "_blender_render.py",
            cwd=cell_dir,
            args_obj={
                "slug_src_dir": str(slug_src),
                "part_name":    args.part,
                "view":         view,
                "size":         args.size,
                "condition":    args.condition,
                "cond_png_out": str(cond_png),
                "cam_json_out": str(cam_json),
            },
            timeout_s=120,
        )
        return view, cond_png, cam_json, dur

    cond_results: dict[str, tuple[Path, Path]] = {}
    print(f"[mv] phase 1: rendering {len(views)} condition images...")
    with ThreadPoolExecutor(max_workers=len(views)) as pool:
        futures = [pool.submit(_run_phase1, v) for v in views]
        for fut in as_completed(futures):
            view, cond_png, cam_json, dur = fut.result()
            cond_results[view] = (cond_png, cam_json)
            print(f"[mv]   phase1 {view}: {dur:.1f}s")

    # ===== Phase 2: Gemini calls (parallel) =====
    from topos.agents.image_gen.base import make_backend
    backend = make_backend("gemini")
    outer_prompt = f"{CONDITION_PROMPT_PREFIX} {args.prompt}"
    inner_prompt = (f"{CONDITION_PROMPT_PREFIX} {args.prompt_inner}"
                    if args.prompt_inner else None)

    # Build the (view, side) work list. We always run outer for every view;
    # if --prompt-inner given we run an additional inner-side call per view
    # reusing the SAME condition image (the cond render is camera-only, so
    # the silhouette/form is identical for both sides).
    sides_to_run = ["outer"] + (["inner"] if inner_prompt else [])
    work = [(v, s) for v in views for s in sides_to_run]

    def _run_gemini(view: str, side: str) -> dict:
        cond_png, _ = cond_results[view]
        full_prompt = inner_prompt if side == "inner" else outer_prompt
        attempts = []
        for attempt_i in range(1, args.max_gemini_attempts + 1):
            r = backend.generate(full_prompt,
                                 condition_image=cond_png, size=args.size)
            attempts.append({
                "attempt": attempt_i, "success": r.success,
                "duration_s": r.duration_s, "cost_usd": r.cost_usd,
                "error": r.error,
            })
            if r.success:
                gen_png = cell_dir / f"gen_{side}_{view}.png"
                gen_png.write_bytes(r.png_bytes)
                return {
                    "view": view, "side": side,
                    "success": True, "gen_path": str(gen_png),
                    "attempts": attempts,
                    "total_cost_usd": sum(a["cost_usd"] for a in attempts),
                    "total_duration_s": sum(a["duration_s"] for a in attempts),
                    "model": r.model,
                }
        return {
            "view": view, "side": side, "success": False, "gen_path": None,
            "attempts": attempts,
            "total_cost_usd": sum(a["cost_usd"] for a in attempts),
            "total_duration_s": sum(a["duration_s"] for a in attempts),
            "model": backend.model,
            "error": attempts[-1]["error"],
        }

    print(f"[mv] phase 2: calling Gemini {len(work)}× in parallel "
          f"({len(views)} view(s) × {len(sides_to_run)} side(s))...")
    gemini_results: dict[tuple[str, str], dict] = {}
    with ThreadPoolExecutor(max_workers=len(work)) as pool:
        futures = [pool.submit(_run_gemini, v, s) for v, s in work]
        for fut in as_completed(futures):
            r = fut.result()
            gemini_results[(r["view"], r["side"])] = r
            tag2 = "✓" if r["success"] else "✗"
            print(f"[mv]   gemini {tag2} {r['side']:5s} {r['view']:8s} "
                  f"${r['total_cost_usd']:.4f} {r['total_duration_s']:.1f}s "
                  f"({len(r['attempts'])} attempt(s))")

    total_cost = sum(r["total_cost_usd"] for r in gemini_results.values())
    failed = [(v, s) for (v, s), r in gemini_results.items() if not r["success"]]
    if failed:
        print(f"[mv] ABORT: {len(failed)} call(s) failed Gemini: {failed}")
        return 1

    # ===== Phase 3: fusion (single Blender) =====
    # Order outer-first so material slot indices are stable + diagnostic
    # renders prefer outer materials when both are present.
    views_data = []
    for side in sides_to_run:
        for v in views:
            entry = gemini_results[(v, side)]
            views_data.append({
                "view": v, "side": side,
                "image_path": entry["gen_path"],
                "cam_path":   str(cond_results[v][1]),
            })
    final_front = cell_dir / "final_front.png"
    final_3q    = cell_dir / "final_3q.png"
    final_back  = cell_dir / "final_back.png"
    blend_out   = cell_dir / "final.blend" if args.keep_blend else None
    glb_out     = cell_dir / "textured.glb" if not args.no_glb else None

    fusion_kind = "soft-blend" if args.soft_blend else "hard-assignment"
    phase3_script = (_HERE / "_blender_apply_softblend.py") if args.soft_blend \
                    else (_HERE / "_blender_apply_multiview.py")
    print(f"[mv] phase 3: fusion ({fusion_kind})...")
    phase3_dur = _run_blender_phase(
        script=phase3_script,
        cwd=cell_dir,
        args_obj={
            "slug_src_dir": str(slug_src),
            "part_name":    args.part,
            "size":         args.size,
            "views_data":   views_data,
            "final_front":  str(final_front),
            "final_3q":     str(final_3q),
            "final_back":   str(final_back),
            "keep_blend":   bool(args.keep_blend),
            "blend_out":    str(blend_out) if blend_out else "",
            "glb_out":      str(glb_out) if glb_out else "",
        },
        timeout_s=240,
    )
    print(f"[mv] phase 3: {phase3_dur:.1f}s")

    manifest = {
        "slug": args.slug, "part": args.part,
        "prompt": args.prompt, "prompt_inner": args.prompt_inner,
        "outer_full_prompt": outer_prompt, "inner_full_prompt": inner_prompt,
        "views": views, "sides": sides_to_run,
        "fusion": fusion_kind,
        "condition": args.condition, "size": args.size, "out_tag": tag,
        "total_cost_usd": total_cost,
        "phase3_duration_s": phase3_dur,
        # JSON keys can't be tuples — flatten to a list of records.
        "gemini": [
            {**r} for r in gemini_results.values()
        ],
        "cell_dir": str(cell_dir),
    }
    (cell_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[mv] done. total ${total_cost:.4f}. {cell_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
