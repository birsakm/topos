"""Single-cell experiment runner.

Outer Python (with `topos` installed) → orchestrates two Blender subprocesses
and one Gemini call. See ../../scripts/experiment_uv_texture/README.md for
the harness contract and CLI overview.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Make sure the repo root is importable so `import topos` works regardless
# of the user's CWD.
_REPO_ROOT = _HERE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _common import CONDITION_PROMPT_PREFIX, slugify  # noqa: E402


@dataclass
class CellPaths:
    cell_dir: Path
    cond_png:    Path
    cam_json:    Path
    gen_png:     Path
    gen_json:    Path
    final_front: Path
    final_3q:    Path
    final_back:  Path
    blend_out:   Path


@dataclass
class CellResult:
    success: bool
    skipped_gemini: bool
    cost_usd: float
    gemini_duration_s: float
    gemini_error: str | None
    phase1_duration_s: float
    phase3_duration_s: float
    cell_dir: str


def build_cell_paths(out_dir: Path, part: str, view: str, condition: str,
                     projection: str, prompt_slug: str) -> CellPaths:
    cell_id = f"{part}__{view}__{condition}__{projection}__{prompt_slug}"
    cd = out_dir / cell_id
    cd.mkdir(parents=True, exist_ok=True)
    return CellPaths(
        cell_dir=cd,
        cond_png=cd / "cond.png",
        cam_json=cd / "cam.json",
        gen_png=cd / "gen.png",
        gen_json=cd / "gen.json",
        final_front=cd / "final_front.png",
        final_3q=cd / "final_3q.png",
        final_back=cd / "final_back.png",
        blend_out=cd / "final.blend",
    )


def _run_blender_phase(*, script: Path, cwd: Path, args_obj: dict,
                       timeout_s: int) -> float:
    """Invoke a Blender subprocess; raise on failure. Returns duration_s."""
    from topos.tools._blender_subprocess import run_blender

    payload = json.dumps(args_obj)
    t0 = time.monotonic()
    result = run_blender(
        script=script,
        cwd=cwd,
        timeout_s=timeout_s,
        script_args=[payload],
    )
    dur = time.monotonic() - t0
    if not result.success:
        print(f"[run] blender phase FAILED (exit={result.exit_code})")
        print("---- blender stdout ----")
        print(result.stdout)
        print("---- blender stderr ----")
        print(result.stderr)
        raise RuntimeError(
            f"blender phase script {script.name!r} failed: exit={result.exit_code}"
        )
    print(result.stdout)
    if result.stderr.strip():
        print("---- blender stderr ----")
        print(result.stderr)
    return dur


def run_cell(
    *,
    slug: str,
    part: str,
    prompt: str,
    condition: str,
    projection: str,
    view: str = "front",
    size: int = 1024,
    out_tag: str | None = None,
    skip_gemini: bool = False,
    keep_blend: bool = False,
    skip_glb: bool = False,
    out_root: Path | None = None,
) -> CellResult:
    """One end-to-end cell. Builds paths, runs phase 1, calls Gemini, runs phase 3."""

    repo_root = _REPO_ROOT
    slug_dir = (out_root or (repo_root / "outputs")) / slug
    slug_src = slug_dir / "src"
    if not (slug_src / "build.py").is_file():
        raise FileNotFoundError(f"slug build.py missing: {slug_src/'build.py'}")

    tag = out_tag or _dt.datetime.now().strftime("v_%Y%m%d_%H%M%S")
    exp_root = slug_dir / "artifacts" / "uv_tex_exp" / tag
    exp_root.mkdir(parents=True, exist_ok=True)

    prompt_slug = slugify(prompt)
    paths = build_cell_paths(exp_root, part=part, view=view, condition=condition,
                             projection=projection, prompt_slug=prompt_slug)
    print(f"[run] cell_dir = {paths.cell_dir}")

    # ---------- Phase 1: condition render ----------
    phase1_args = {
        "slug_src_dir": str(slug_src),
        "part_name":    part,
        "view":         view,
        "size":         size,
        "condition":    condition,
        "cond_png_out": str(paths.cond_png),
        "cam_json_out": str(paths.cam_json),
    }
    phase1_dur = _run_blender_phase(
        script=_HERE / "_blender_render.py",
        cwd=paths.cell_dir,
        args_obj=phase1_args,
        timeout_s=120,
    )

    # ---------- Phase 2: Gemini ----------
    gen_cost = 0.0
    gen_dur  = 0.0
    gen_err  = None
    if skip_gemini:
        # Write a placeholder magenta PNG so phase 3 still has something
        # to project. Pure-stdlib PNG via a tiny synthetic image.
        _write_placeholder_png(paths.gen_png, size=size)
        paths.gen_json.write_text(json.dumps({
            "skipped": True,
            "prompt": prompt,
            "note": "placeholder magenta PNG — --skip-gemini was set",
        }, indent=2))
    else:
        from topos.agents.image_gen.base import make_backend
        backend = make_backend("gemini")
        full_prompt = f"{CONDITION_PROMPT_PREFIX} {prompt}"
        print(f"[run] calling gemini ({backend.model})...")
        # Nano Banana 2 occasionally returns text-only ("no image data") for
        # otherwise-fine prompts — observed ~16% on the v0 matrix. Retry up
        # to 2 extra times before giving up; each attempt is independently
        # billed but extra calls are rare.
        max_attempts = 3
        attempts: list[dict] = []
        result = None
        for attempt_i in range(1, max_attempts + 1):
            result = backend.generate(
                full_prompt,
                condition_image=paths.cond_png,
                size=size,
            )
            attempts.append({
                "attempt": attempt_i,
                "success": result.success,
                "duration_s": result.duration_s,
                "cost_usd": result.cost_usd,
                "error": result.error,
            })
            gen_cost += result.cost_usd
            gen_dur  += result.duration_s
            if result.success:
                if attempt_i > 1:
                    print(f"[run] gemini succeeded on attempt {attempt_i}/{max_attempts}")
                break
            print(f"[run] gemini attempt {attempt_i}/{max_attempts} failed: "
                  f"{result.error}")

        gen_err = None if (result and result.success) else (result.error if result else "no result")
        meta = {
            "prompt":          prompt,
            "full_prompt":     full_prompt,
            "model":           result.model if result else "",
            "success":         bool(result and result.success),
            "duration_s_total": gen_dur,
            "cost_usd_total":  gen_cost,
            "error":           gen_err,
            "attempts":        attempts,
            "response_size_bytes": (result.raw_meta.get("response_size_bytes")
                                    if result else None),
        }
        paths.gen_json.write_text(json.dumps(meta, indent=2))
        if not (result and result.success):
            print(f"[run] gemini FAILED after {len(attempts)} attempt(s): {gen_err}")
            return CellResult(
                success=False, skipped_gemini=False,
                cost_usd=gen_cost, gemini_duration_s=gen_dur,
                gemini_error=gen_err,
                phase1_duration_s=phase1_dur, phase3_duration_s=0.0,
                cell_dir=str(paths.cell_dir),
            )
        paths.gen_png.write_bytes(result.png_bytes)
        print(f"[run] gemini ok: total ${gen_cost:.4f}, {gen_dur:.1f}s, "
              f"{len(attempts)} attempt(s)")

    # ---------- Phase 3: apply + multi-view render ----------
    glb_out = paths.cell_dir / "textured.glb" if (not skip_glb) else None
    phase3_args = {
        "slug_src_dir": str(slug_src),
        "part_name":    part,
        "view":         view,
        "size":         size,
        "projection":   projection,
        "gen_png_in":   str(paths.gen_png),
        "cam_json_in":  str(paths.cam_json),
        "glb_out":      str(glb_out) if glb_out else "",
        "final_front":  str(paths.final_front),
        "final_3q":     str(paths.final_3q),
        "final_back":   str(paths.final_back),
        "keep_blend":   keep_blend,
        "blend_out":    str(paths.blend_out) if keep_blend else "",
    }
    phase3_dur = _run_blender_phase(
        script=_HERE / "_blender_apply.py",
        cwd=paths.cell_dir,
        args_obj=phase3_args,
        timeout_s=180,
    )

    return CellResult(
        success=True, skipped_gemini=skip_gemini,
        cost_usd=gen_cost, gemini_duration_s=gen_dur, gemini_error=gen_err,
        phase1_duration_s=phase1_dur, phase3_duration_s=phase3_dur,
        cell_dir=str(paths.cell_dir),
    )


def _write_placeholder_png(out_path: Path, *, size: int) -> None:
    """Magenta-on-white test image written via stdlib zlib + struct — no
    Pillow dependency. Used only for --skip-gemini dry-runs."""
    import struct, zlib  # noqa: E401
    width = height = size
    # Build raw RGBA scanlines: magenta circle on white bg, ~30% radius.
    rows = []
    half = size / 2.0
    radius2 = (size * 0.30) ** 2
    for y in range(height):
        row = bytearray()
        row.append(0)  # filter type none
        for x in range(width):
            dx = x - half
            dy = y - half
            if dx * dx + dy * dy < radius2:
                row += b"\xff\x00\xff\xff"  # magenta
            else:
                row += b"\xff\xff\xff\xff"  # white
        rows.append(bytes(row))
    raw = b"".join(rows)
    compressed = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", compressed)
           + chunk(b"IEND", b""))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True, help="outputs/<slug>/ to target")
    p.add_argument("--part", required=True, help="bpy object name (case sensitive)")
    p.add_argument("--prompt", required=True, help="texture prompt")
    p.add_argument("--condition", required=True,
                   choices=["silhouette", "ao", "depth", "normal", "cycles_diffuse"])
    p.add_argument("--projection", required=True,
                   choices=["project_from_view", "analytical_view"])
    p.add_argument("--view", default="front")
    p.add_argument("--size", type=int, default=1024)
    p.add_argument("--out-tag", default=None,
                   help="subdir under outputs/<slug>/artifacts/uv_tex_exp/ "
                        "(default: timestamp)")
    p.add_argument("--skip-gemini", action="store_true",
                   help="use a magenta placeholder image instead of calling "
                        "Gemini (dry-run / wiring sanity)")
    p.add_argument("--keep-blend", action="store_true",
                   help="save final.blend snapshot for manual inspection")
    p.add_argument("--no-glb", action="store_true",
                   help="skip GLB export (default: textured.glb is written next to the renders)")
    return p


def main() -> int:
    args = _build_argparser().parse_args()
    res = run_cell(
        slug=args.slug,
        part=args.part,
        prompt=args.prompt,
        condition=args.condition,
        projection=args.projection,
        view=args.view,
        size=args.size,
        out_tag=args.out_tag,
        skip_gemini=args.skip_gemini,
        keep_blend=args.keep_blend,
        skip_glb=args.no_glb,
    )
    # Always write a sidecar result.json so run_matrix.py can read it
    # without parsing log output. cell_dir was constructed inside run_cell.
    try:
        Path(res.cell_dir, "result.json").write_text(
            json.dumps(asdict(res), indent=2)
        )
    except Exception as e:
        print(f"[run] warning: could not write result.json: {e}")
    print(json.dumps(asdict(res), indent=2))
    return 0 if res.success else 1


if __name__ == "__main__":
    sys.exit(main())
