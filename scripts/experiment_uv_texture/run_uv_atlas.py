"""UV-atlas pipeline: 1 Gemini call instead of N.

Phase 1 bakes AO into a smart-projected UV atlas. The atlas image is fed
to Gemini as the condition_image. Phase 3 binds the returned PNG via the
same UV map. Single Gemini call (~$0.04), no view-projection, no
backside-mirror artifacts.

Usage:
    python scripts/experiment_uv_texture/run_uv_atlas.py \\
        --slug cab_a7_full --part Handle \\
        --prompt "golden dragon relief on dark bronze, ornate brass endcaps"
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
_REPO_ROOT = _HERE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _common import (  # noqa: E402
    CUBE_ATLAS_LAYOUT,
    CUBE_ATLAS_LAYOUT_DUAL,
    CYLINDER_ATLAS_LABELS,
    TILE_H_6,
    TILE_H_12,
    TILE_MARGIN,
    TILE_W,
    slugify,
)


# 6-tile (solid part) prompt prefix.
_ATLAS_PROMPT_PREFIX_6 = (
    "The attached image is a labelled UV layout of a 3D object's surface, "
    "arranged in a 3-column × 2-row grid of 6 tiles. The tile positions and "
    "labels are FIXED — you must paint each label's content into THAT EXACT "
    "POSITION:\n"
    "  - Top-left tile     = RIGHT face\n"
    "  - Top-middle tile   = BACK face\n"
    "  - Top-right tile    = TOP face\n"
    "  - Bottom-left tile  = LEFT face\n"
    "  - Bottom-middle tile = FRONT face\n"
    "  - Bottom-right tile = BOTTOM face\n"
    "Each tile shows the 3D form of its corresponding face as ambient-"
    "occlusion shading inside black outlines; the white space around the "
    "tiles is outside the unwrap and MUST stay white in your output. "
    "Paint your texture content ONLY inside each tile's outline, matching "
    "the per-face description below. Replace any red label text with your "
    "texture. DO NOT swap content between tiles based on visual prominence "
    "— the label position is the ground truth for what each tile depicts. "
    "Output the same dimensions. Per-face content description:"
)

# Cylinder-atlas prompt prefix. 3 zones: one big lateral band on top, two
# endcap squares on the bottom.
_ATLAS_PROMPT_PREFIX_CYL = (
    "The attached image is a labelled UV layout of a cylindrical 3D "
    "object. The TOP half (rows above the middle horizontal line) is a "
    "single wide LATERAL band — the cylinder's side surface unwrapped flat. "
    "Horizontal position = angle around the cylinder axis (0° at the left "
    "edge, wrapping back to 0° at the right). Vertical position = "
    "distance along the cylinder axis (one end at the bottom of the band, "
    "the other end at the top). The BOTTOM half holds two endcap squares: "
    "CAP-NEG on the bottom-left (the cap on the negative end of the axis) "
    "and CAP-POS on the bottom-right (the positive end). Labels are in "
    "red at the top-left of each zone. The white space around the islands "
    "is outside the unwrap and MUST stay white. Paint your texture content "
    "inside each island. The lateral band should typically carry a "
    "continuous wraparound motif (the left/right edges of the band meet "
    "on the back of the cylinder). The endcaps are circles inscribed in "
    "their squares. Output the same dimensions. Per-zone content "
    "description:"
)

# 12-tile (hollow part) prompt prefix. Splits the canvas into an outer
# shell atlas (bottom half) and an inner cavity atlas (top half).
_ATLAS_PROMPT_PREFIX_12 = (
    "The attached image is a labelled UV layout of a 3D HOLLOW object, "
    "arranged in a 3-column × 4-row grid of 12 tiles. The bottom HALF of "
    "the image (rows 0-1 in UV space, the lower portion of the canvas) "
    "holds the OUTER shell surfaces — the side a viewer normally sees. "
    "The top HALF holds the INNER cavity walls — surfaces visible when "
    "looking into the open cavity. Each tile has a large red label like "
    "OUT-F (outer front) or IN-Bot (inner cavity floor). The 12 fixed "
    "positions:\n"
    "  - Row 3 (top):    IN-R | IN-B   | IN-T   (inner cavity, +X/+Y/+Z)\n"
    "  - Row 2:          IN-L | IN-F   | IN-Bot (inner cavity, -X/-Y/-Z)\n"
    "  - Row 1:          OUT-R| OUT-B  | OUT-T  (outer shell,  +X/+Y/+Z)\n"
    "  - Row 0 (bottom): OUT-L| OUT-F  | OUT-Bot(outer shell,  -X/-Y/-Z)\n"
    "Each tile shows the 3D form of its face as AO shading; the white space "
    "around the tiles is outside the unwrap and MUST stay white. Paint "
    "ONLY inside each tile's outline. Replace the red label with your "
    "texture. The outer-shell tiles typically carry visible decoration; "
    "the inner-cavity tiles are usually plainer interior surfaces. DO NOT "
    "swap content between tiles based on visual prominence. Output the "
    "same dimensions. Per-face content description:"
)


@dataclass
class AtlasResult:
    success: bool
    cost_usd: float
    gemini_duration_s: float
    gemini_error: str | None
    phase1_duration_s: float
    phase3_duration_s: float
    cell_dir: str


def _overlay_atlas_labels(cond_png: Path, *, size: int, dual: bool,
                          cylinder: bool = False) -> None:
    """Stamp tile labels at the top of each atlas tile.

    Uses Pillow (outer Python only — Blender's bundled Python may not have it).
    Big red bold text on a thin white halo so Gemini can't ignore them.
    """
    from PIL import Image, ImageDraw, ImageFont
    img = Image.open(cond_png).convert("RGB")
    draw = ImageDraw.Draw(img)

    target_px = (size // 18)
    if dual:
        target_px = size // 28
    elif cylinder:
        target_px = size // 22
    font = None
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        try:
            font = ImageFont.truetype(p, max(20, target_px))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    def _stamp(x_px: int, y_px: int, label: str) -> None:
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            draw.text((x_px + dx, y_px + dy), label, fill="white", font=font)
        draw.text((x_px, y_px), label, fill=(220, 0, 0), font=font)

    if cylinder:
        # Three labels at the top-left of their respective zones.
        # Lateral band: top half, full width. Place at UV (margin, 1 - margin - small).
        _stamp(int(TILE_MARGIN * size) + 4,
               int((1.0 - (0.5 + TILE_MARGIN + 0.5 - 2 * TILE_MARGIN)) * size) + 4,
               "LATERAL")
        # CAP-NEG: bottom-left square.
        _stamp(int(TILE_MARGIN * size) + 4,
               int((1.0 - (TILE_MARGIN + 0.5 - 2 * TILE_MARGIN)) * size) + 4,
               "CAP-NEG")
        # CAP-POS: bottom-right square.
        _stamp(int((0.5 + TILE_MARGIN) * size) + 4,
               int((1.0 - (TILE_MARGIN + 0.5 - 2 * TILE_MARGIN)) * size) + 4,
               "CAP-POS")
    else:
        tile_h = TILE_H_12 if dual else TILE_H_6
        layout = CUBE_ATLAS_LAYOUT_DUAL if dual else CUBE_ATLAS_LAYOUT
        for _key, (col, row, short_label, _full) in layout.items():
            tile_u0 = col * TILE_W + TILE_MARGIN
            tile_v0 = row * tile_h + TILE_MARGIN
            x = int(tile_u0 * size) + 4
            y_uv_bottom = tile_v0
            y = int((1.0 - (y_uv_bottom + tile_h - 2 * TILE_MARGIN)) * size) + 4
            _stamp(x, y, short_label)
    img.save(cond_png)


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
        print(f"[atlas] blender FAILED (exit={res.exit_code}) {script.name}")
        print(res.stdout)
        print("---- stderr ----")
        print(res.stderr)
        raise RuntimeError(f"phase {script.name} failed")
    print(res.stdout)
    if res.stderr.strip():
        print("---- stderr ----")
        print(res.stderr)
    return dur


def run_atlas(*, slug: str, part: str, prompt: str, size: int = 1024,
              out_tag: str | None = None, skip_gemini: bool = False,
              dual: bool = False,
              cylinder_axis: int | None = None,
              max_attempts: int = 3) -> AtlasResult:
    slug_dir = _REPO_ROOT / "outputs" / slug
    slug_src = slug_dir / "src"
    if not (slug_src / "build.py").is_file():
        raise FileNotFoundError(f"missing {slug_src/'build.py'}")

    tag = out_tag or _dt.datetime.now().strftime("atlas_%Y%m%d_%H%M%S")
    exp_root = slug_dir / "artifacts" / "uv_tex_exp" / tag
    cell_id = f"{part}__uv_atlas__{slugify(prompt)}"
    cell_dir = exp_root / cell_id
    cell_dir.mkdir(parents=True, exist_ok=True)
    print(f"[atlas] cell_dir = {cell_dir}")

    cond_png  = cell_dir / "cond.png"
    uv_json   = cell_dir / "uv_layer.json"
    gen_png   = cell_dir / "gen.png"
    gen_meta  = cell_dir / "gen.json"
    final_front = cell_dir / "final_front.png"
    final_3q    = cell_dir / "final_3q.png"
    final_back  = cell_dir / "final_back.png"
    glb_out     = cell_dir / "textured.glb"

    # Phase 1 — bake UV atlas AO + sidecar
    p1_args = {
        "slug_src_dir": str(slug_src),
        "part_name":    part,
        "size":         size,
        "cond_png_out": str(cond_png),
        "uv_json_out":  str(uv_json),
        "dual_atlas":   dual,
    }
    if cylinder_axis is not None:
        p1_args["cylinder_axis"] = int(cylinder_axis)
    phase1_dur = _run_blender_phase(
        script=_HERE / "_blender_uv_atlas_cond.py",
        cwd=cell_dir,
        args_obj=p1_args,
        timeout_s=180,
    )
    _overlay_atlas_labels(cond_png, size=size, dual=dual,
                          cylinder=(cylinder_axis is not None))
    mode = ("cylinder" if cylinder_axis is not None
            else ("dual" if dual else "cube"))
    print(f"[atlas] overlaid tile labels on {cond_png} (mode={mode})")

    # Phase 2 — Gemini
    gen_cost, gen_dur, gen_err = 0.0, 0.0, None
    if skip_gemini:
        # Placeholder: copy cond.png as gen.png so phase 3 has something
        # to map. Useful for wiring sanity.
        gen_png.write_bytes(cond_png.read_bytes())
        gen_meta.write_text(json.dumps({"skipped": True, "prompt": prompt}, indent=2))
    else:
        from topos.agents.image_gen.base import make_backend
        backend = make_backend("gemini")
        if cylinder_axis is not None:
            prefix = _ATLAS_PROMPT_PREFIX_CYL
        elif dual:
            prefix = _ATLAS_PROMPT_PREFIX_12
        else:
            prefix = _ATLAS_PROMPT_PREFIX_6
        full_prompt = f"{prefix} {prompt}"
        attempts = []
        result = None
        for attempt_i in range(1, max_attempts + 1):
            result = backend.generate(full_prompt, condition_image=cond_png, size=size)
            attempts.append({
                "attempt": attempt_i, "success": result.success,
                "duration_s": result.duration_s, "cost_usd": result.cost_usd,
                "error": result.error,
            })
            gen_cost += result.cost_usd
            gen_dur  += result.duration_s
            if result.success:
                if attempt_i > 1:
                    print(f"[atlas] gemini ok on attempt {attempt_i}")
                break
            print(f"[atlas] gemini attempt {attempt_i} failed: {result.error}")
        gen_err = None if (result and result.success) else (result.error if result else "no result")
        gen_meta.write_text(json.dumps({
            "prompt": prompt, "full_prompt": full_prompt,
            "model": result.model if result else "",
            "success": bool(result and result.success),
            "duration_s_total": gen_dur, "cost_usd_total": gen_cost,
            "error": gen_err, "attempts": attempts,
        }, indent=2))
        if not (result and result.success):
            return AtlasResult(
                success=False, cost_usd=gen_cost, gemini_duration_s=gen_dur,
                gemini_error=gen_err, phase1_duration_s=phase1_dur,
                phase3_duration_s=0.0, cell_dir=str(cell_dir),
            )
        gen_png.write_bytes(result.png_bytes)
        print(f"[atlas] gemini ok: ${gen_cost:.4f} / {gen_dur:.1f}s")

    # Phase 3 — apply
    phase3_dur = _run_blender_phase(
        script=_HERE / "_blender_uv_atlas_apply.py",
        cwd=cell_dir,
        args_obj={
            "slug_src_dir": str(slug_src),
            "part_name":    part,
            "size":         size,
            "gen_png_in":   str(gen_png),
            "uv_json_in":   str(uv_json),
            "final_front":  str(final_front),
            "final_3q":     str(final_3q),
            "final_back":   str(final_back),
            "glb_out":      str(glb_out),
        },
        timeout_s=180,
    )

    return AtlasResult(
        success=True, cost_usd=gen_cost, gemini_duration_s=gen_dur,
        gemini_error=gen_err, phase1_duration_s=phase1_dur,
        phase3_duration_s=phase3_dur, cell_dir=str(cell_dir),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--part", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--out-tag", default=None)
    ap.add_argument("--skip-gemini", action="store_true")
    ap.add_argument("--dual", action="store_true",
                    help="12-tile dual atlas (outer shell + inner cavity). "
                         "Use for hollow parts like drawers and cabinet frames.")
    ap.add_argument("--cylinder-axis", choices=["X", "Y", "Z"], default=None,
                    help="Use cylindrical unwrap with the given axis as the "
                         "cylinder's axis (lateral band + 2 endcap squares). "
                         "Use for handles, knobs, columns.")
    args = ap.parse_args()
    cyl_axis = None
    if args.cylinder_axis is not None:
        cyl_axis = {"X": 0, "Y": 1, "Z": 2}[args.cylinder_axis]
    res = run_atlas(
        slug=args.slug, part=args.part, prompt=args.prompt,
        size=args.size, out_tag=args.out_tag,
        skip_gemini=args.skip_gemini,
        dual=args.dual,
        cylinder_axis=cyl_axis,
    )
    print(json.dumps(asdict(res), indent=2))
    return 0 if res.success else 1


if __name__ == "__main__":
    sys.exit(main())
