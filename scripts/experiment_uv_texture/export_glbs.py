"""Walk an experiment dir and (re-)export textured.glb for every cell.

For each leaf cell directory under `outputs/<slug>/artifacts/uv_tex_exp/`:
- Detect type by file presence:
    multi-view → has cond_<view>.png + cam_<view>.json + gen_<view>.png
    single-view → has cond.png + cam.json + gen.png
- Re-run the matching phase-3 Blender script in re-bake mode (existing
  textures, just write textured.glb).
- Skip cells that already have textured.glb unless `--force`.

Usage:
    python scripts/experiment_uv_texture/export_glbs.py outputs/cab_a7_full/artifacts/uv_tex_exp/
    python scripts/experiment_uv_texture/export_glbs.py <some_cell_dir>        # single cell
    python scripts/experiment_uv_texture/export_glbs.py <tag_dir>              # one tag
"""

from __future__ import annotations

import argparse
import json
import re
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


_OUTPUTS_ROOT = _REPO_ROOT / "outputs"


def _slug_and_part_from_cell_dir(cell_dir: Path) -> tuple[str, str]:
    """Recover (slug, part_name) from the cell_dir path.

    Path format: <repo>/outputs/<slug>/artifacts/uv_tex_exp/<tag>/<cell_id>
    cell_id format: <part>__<rest>__...  (part is the first '__'-segment)
    """
    parts = cell_dir.parts
    try:
        i = parts.index("outputs")
    except ValueError as e:
        raise SystemExit(f"cell_dir not under outputs/: {cell_dir}") from e
    slug = parts[i + 1]
    part = cell_dir.name.split("__", 1)[0]
    return slug, part


def _detect_views(cell_dir: Path) -> list[str] | None:
    """Return list of view names if this is a multi-view cell, else None.

    Supports both naming conventions:
      v1: gen_<view>.png (single texture per view)
      v2: gen_outer_<view>.png [+ optional gen_inner_<view>.png]
    """
    views = []
    for p in cell_dir.glob("cond_*.png"):
        m = re.match(r"^cond_(.+)\.png$", p.name)
        if m:
            views.append(m.group(1))
    if not views:
        return None
    complete = []
    for v in views:
        if not (cell_dir / f"cam_{v}.json").is_file():
            continue
        if ((cell_dir / f"gen_{v}.png").is_file()
                or (cell_dir / f"gen_outer_{v}.png").is_file()):
            complete.append(v)
    return complete or None


def _multiview_views_data(cell_dir: Path, views: list[str]) -> list[dict]:
    """Build views_data entries for phase 3, matching the existing v1/v2
    naming on disk."""
    out = []
    for v in views:
        cam_path = str(cell_dir / f"cam_{v}.json")
        outer_v2 = cell_dir / f"gen_outer_{v}.png"
        inner_v2 = cell_dir / f"gen_inner_{v}.png"
        legacy   = cell_dir / f"gen_{v}.png"
        if outer_v2.is_file():
            out.append({"view": v, "side": "outer",
                        "image_path": str(outer_v2), "cam_path": cam_path})
            if inner_v2.is_file():
                out.append({"view": v, "side": "inner",
                            "image_path": str(inner_v2), "cam_path": cam_path})
        elif legacy.is_file():
            out.append({"view": v, "image_path": str(legacy), "cam_path": cam_path})
    return out


def _is_single_view(cell_dir: Path) -> bool:
    return ((cell_dir / "cond.png").is_file()
            and (cell_dir / "cam.json").is_file()
            and (cell_dir / "gen.png").is_file())


def _run_blender_phase(*, script: Path, cwd: Path, args_obj: dict,
                       timeout_s: int = 180) -> tuple[bool, str]:
    from topos.tools._blender_subprocess import run_blender
    r = run_blender(
        script=script, cwd=cwd, timeout_s=timeout_s,
        script_args=[json.dumps(args_obj)],
    )
    if not r.success:
        return False, r.stdout + "\n---STDERR---\n" + r.stderr
    return True, r.stdout


def _projection_for_cell(cell_dir: Path) -> str:
    """For single-view cells, infer projection from cell_id."""
    name = cell_dir.name
    if "__project_from_view__" in name:
        return "project_from_view"
    return "analytical_view"


def _view_for_cell(cell_dir: Path) -> str:
    """Single-view cells have view as second '__'-segment."""
    return cell_dir.name.split("__")[1]


def export_one(cell_dir: Path, *, force: bool = False) -> dict:
    glb_out = cell_dir / "textured.glb"
    if glb_out.is_file() and not force:
        return {"cell": cell_dir.name, "skipped": True,
                "reason": "textured.glb exists (use --force to overwrite)"}

    if not cell_dir.is_dir():
        return {"cell": str(cell_dir), "skipped": True, "reason": "not a directory"}

    slug, part = _slug_and_part_from_cell_dir(cell_dir)
    slug_src = _OUTPUTS_ROOT / slug / "src"
    if not (slug_src / "build.py").is_file():
        return {"cell": cell_dir.name, "ok": False,
                "error": f"missing {slug_src}/build.py"}

    multiview = _detect_views(cell_dir)
    t0 = time.monotonic()

    if multiview:
        # Read size from manifest if available, else default 1024
        size = 1024
        manifest_path = cell_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                size = int(json.loads(manifest_path.read_text()).get("size", 1024))
            except Exception:
                pass
        views_data = _multiview_views_data(cell_dir, multiview)
        ok, log = _run_blender_phase(
            script=_HERE / "_blender_apply_multiview.py",
            cwd=cell_dir,
            args_obj={
                "slug_src_dir": str(slug_src),
                "part_name":    part,
                "size":         size,
                "views_data":   views_data,
                # Re-render the diagnostic PNGs too (cheap, ensures they
                # match the GLB exactly). Could be skipped if perf matters.
                "final_front":  str(cell_dir / "final_front.png"),
                "final_3q":     str(cell_dir / "final_3q.png"),
                "final_back":   str(cell_dir / "final_back.png"),
                "keep_blend":   False,
                "blend_out":    "",
                "glb_out":      str(glb_out),
            },
            timeout_s=240,
        )
        kind = f"multiview_{len(multiview)}"
    elif _is_single_view(cell_dir):
        size = 1024
        result_json = cell_dir / "result.json"
        if result_json.is_file():
            try:
                size = int(json.loads(result_json.read_text()).get("size", 1024))
            except Exception:
                pass
        view = _view_for_cell(cell_dir)
        proj = _projection_for_cell(cell_dir)
        ok, log = _run_blender_phase(
            script=_HERE / "_blender_apply.py",
            cwd=cell_dir,
            args_obj={
                "slug_src_dir": str(slug_src),
                "part_name":    part,
                "view":         view,
                "size":         size,
                "projection":   proj,
                "gen_png_in":   str(cell_dir / "gen.png"),
                "cam_json_in":  str(cell_dir / "cam.json"),
                "final_front":  str(cell_dir / "final_front.png"),
                "final_3q":     str(cell_dir / "final_3q.png"),
                "final_back":   str(cell_dir / "final_back.png"),
                "keep_blend":   False,
                "blend_out":    "",
                "glb_out":      str(glb_out),
            },
            timeout_s=180,
        )
        kind = "single"
    else:
        return {"cell": cell_dir.name, "skipped": True,
                "reason": "no recognized phase-3 artifacts (cond.png or cond_<view>.png)"}

    dur = time.monotonic() - t0
    out = {"cell": cell_dir.name, "kind": kind, "duration_s": dur,
           "ok": ok, "glb": str(glb_out) if ok else None}
    if not ok:
        out["log_tail"] = "\n".join(log.splitlines()[-15:])
    return out


def _iter_cell_dirs(target: Path):
    """Yield candidate cell dirs under `target`.

    A cell dir is one that contains either `cond.png` or any `cond_*.png`.
    Walks 0-2 levels deep so a single cell, a tag dir, or the whole
    uv_tex_exp/ root all work.
    """
    target = target.resolve()
    # Is target itself a cell?
    if _is_single_view(target) or _detect_views(target):
        yield target
        return
    for child in sorted(target.iterdir()):
        if not child.is_dir():
            continue
        if _is_single_view(child) or _detect_views(child):
            yield child
            continue
        for sub in sorted(child.iterdir()):
            if sub.is_dir() and (_is_single_view(sub) or _detect_views(sub)):
                yield sub


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", help="cell dir, tag dir, or uv_tex_exp root")
    ap.add_argument("--force", action="store_true",
                    help="re-export even if textured.glb already exists")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="parallel Blender subprocesses (each ~2-3s for phase 3)")
    args = ap.parse_args()

    target = Path(args.target)
    cells = list(_iter_cell_dirs(target))
    if not cells:
        print(f"[glb] no cell dirs found under {target}")
        return 1

    print(f"[glb] {len(cells)} cell(s), concurrency={args.concurrency}")
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(export_one, c, force=args.force): c for c in cells}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            if r.get("skipped"):
                print(f"[glb] {i}/{len(cells)} ⊝  {r['cell']}: {r['reason']}")
            elif r.get("ok"):
                print(f"[glb] {i}/{len(cells)} ✓  {r['cell']} "
                      f"[{r['kind']}, {r['duration_s']:.1f}s]")
            else:
                print(f"[glb] {i}/{len(cells)} ✗  {r['cell']}: {r.get('error')}")
                if r.get("log_tail"):
                    for line in r["log_tail"].splitlines():
                        print(f"     | {line}")

    n_ok = sum(1 for r in results if r.get("ok"))
    n_sk = sum(1 for r in results if r.get("skipped"))
    n_fail = len(results) - n_ok - n_sk
    print(f"\n[glb] done. ok={n_ok} skipped={n_sk} failed={n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
