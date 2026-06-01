"""``texture_uv_atlas`` tool: UV-atlas condition bake → Gemini image-gen →
apply texture + render multiview + export GLB.

EXPERIMENTAL / not wired into the default plan. The articulated plan
(``plan_generator`` + ``expand.articulated_parts``) only ever schedules
``generate_texture_image`` (flat per-part PNG); nothing emits a ToolTask for
this tool, and the designer prompt no longer advertises ``kind: "uv_atlas"``.
It survives as the only high-fidelity UV-conditioned path and is reachable only
from the rocket example. Do not document it as a default texture mode.

Reads ``design.json`` for per-part ``texture`` specs with ``kind: "uv_atlas"``.
For each qualifying part, runs a 3-phase pipeline:

  Phase 1 (Blender): Smart UV Project unwrap, AO-bake condition PNG
  Phase 2 (Gemini):  Generate texture from condition image + UV-layout prompt prefix
  Phase 3 (Blender): Apply all generated textures, render multiview, export GLB

Produces the same output shape as ``render_multiview`` (``view_0.png`` … ``view_N.png``)
so the judge tool can evaluate the textured renders directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...agents.image_gen.base import make_backend
from .._blender_subprocess import run_blender
from .._paths import resolve_under_workspace
from ..registry import tool

_HERE = Path(__file__).resolve().parent

# ── UV-layout prompt prefix ──────────────────────────────────────────────

# Smart UV Project produces irregular angle-based islands (not a labelled
# grid), so the prompt describes the layout generically: paint inside the
# AO-shaded islands, leave everything else alone, no wireframe. This is the
# "paint inside the coloring-book shapes" framing the threejs-pipeline branch
# found works best for UV-conditioned image generation.
_UV_PROMPT_PREFIX = (
    "The attached image is a UV layout of one part of a 3D object: irregular "
    "islands of its surface shown with soft ambient-occlusion form shading on "
    "a light-gray base, surrounded by flat gray space. Paint realistic, "
    "seamless material texture ONLY inside the form-shaded islands, following "
    "their shape; leave the surrounding flat-gray space untouched. Do NOT draw "
    "any mesh wireframe, triangulation, island outlines, seams, text, or "
    "borders. Use flat, even, neutral lighting with no extra cast shadows or "
    "baked highlights. Output exactly the same dimensions. Subject:"
)


# ── tool ─────────────────────────────────────────────────────────────────

@tool(
    "texture_uv_atlas",
    description=(
        "UV-atlas texture pipeline: for each part with ``texture.kind='uv_atlas'`` "
        "in design.json, Smart-UV-Project unwraps the part and bakes an AO "
        "condition image, calls Gemini to generate the texture, then applies all "
        "textures and renders multiview + exports GLB. Replaces render_multiview "
        "+ export_glb for textured output."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "script_relpath": {"type": "string", "default": "src/build.py"},
            "design_relpath": {"type": "string", "default": "src/design.json"},
            "output_subdir": {"type": "string", "default": "artifacts/uv_textured"},
            "size": {"type": "integer", "default": 1024,
                     "description": "Atlas texture resolution (square)."},
            "render_resolution": {"type": "integer", "default": 512},
            "n_views": {"type": "integer", "default": 8},
            "glb_relpath": {"type": "string", "default": "artifacts/textured.glb"},
            "joints_relpath": {"type": "string", "default": "src/joints.yaml",
                               "description": "Path to joints YAML for URDF export."},
            "urdf_relpath": {"type": "string", "default": "artifacts/textured.urdf",
                             "description": "Output URDF with textured per-part GLBs."},
            "backend": {"type": "string",
                        "description": "ImageGenBackend name (default: config.image_gen.default)."},
            "timeout_s": {"type": "integer", "default": 600},
        },
        "required": ["workspace"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "parts_textured": {"type": "integer"},
            "render_dir": {"type": "string"},
            "glb_path": {"type": "string"},
            "total_cost_usd": {"type": "number"},
            "per_part": {"type": "array"},
        },
    },
    side_effects=True,
)
def texture_uv_atlas(
    *,
    workspace: str,
    script_relpath: str = "src/build.py",
    design_relpath: str = "src/design.json",
    output_subdir: str = "artifacts/uv_textured",
    size: int = 1024,
    render_resolution: int = 512,
    n_views: int = 8,
    glb_relpath: str = "artifacts/textured.glb",
    joints_relpath: str = "src/joints.yaml",
    urdf_relpath: str = "artifacts/textured.urdf",
    backend: str | None = None,
    timeout_s: int = 600,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    design_path = resolve_under_workspace(ws, design_relpath, label="design_relpath")
    if not design_path.is_file():
        raise FileNotFoundError(f"{design_relpath} not found")
    design = json.loads(design_path.read_text(encoding="utf-8"))

    parts = design.get("parts") or []
    global_refs = [
        ws / img for img in (design.get("reference_images") or [])
        if (ws / img).is_file()
    ]
    uv_parts = []
    for p in parts:
        tex = p.get("texture") or {}
        if tex.get("kind") == "uv_atlas":
            part_refs = [
                ws / img for img in (p.get("reference_images") or [])
                if (ws / img).is_file()
            ]
            uv_parts.append({
                "name": p["name"],
                "prompt": tex.get("prompt", ""),
                "atlas_mode": tex.get("atlas_mode", "cube"),
                "dual": bool(tex.get("dual", False)),
                "cylinder_axis": tex.get("cylinder_axis"),
                "reference_images": part_refs + global_refs,
            })

    if not uv_parts:
        return {
            "success": True,
            "parts_textured": 0,
            "render_dir": "",
            "glb_path": "",
            "total_cost_usd": 0.0,
            "per_part": [],
            "note": "no parts with texture.kind='uv_atlas' in design.json",
        }

    out_dir = ws / output_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    slug_src = ws / "src"

    # ── Phase 1: UV unwrap + AO bake (single Blender launch) ────────────
    print(f"[texture_uv_atlas] phase 1: baking {len(uv_parts)} condition images...")
    phase1_args = {
        "slug_src_dir": str(slug_src),
        "parts": [
            {
                "name": p["name"],
                "atlas_mode": p["atlas_mode"],
                "dual": p["dual"],
                "cylinder_axis": p.get("cylinder_axis"),
            }
            for p in uv_parts
        ],
        "size": size,
        "out_dir": str(out_dir),
    }
    res1 = run_blender(
        script=_HERE / "_bake_cond.py",
        cwd=out_dir,
        timeout_s=min(timeout_s, 300),
        script_args=[json.dumps(phase1_args)],
    )
    if not res1.success:
        print(res1.stdout)
        print(res1.stderr)
        return {
            "success": False,
            "parts_textured": 0,
            "render_dir": "",
            "glb_path": "",
            "total_cost_usd": 0.0,
            "per_part": [],
            "error": f"phase 1 (bake_cond) failed: exit={res1.exit_code}",
            "stderr": res1.stderr[-2000:],
        }
    print(res1.stdout)

    # ── Phase 2: Gemini texture generation ───────────────────────────────
    print(f"[texture_uv_atlas] phase 2: generating {len(uv_parts)} textures via Gemini...")
    try:
        img_backend = make_backend(backend)
    except (ValueError, RuntimeError) as e:
        return {
            "success": False,
            "parts_textured": 0,
            "render_dir": "",
            "glb_path": "",
            "total_cost_usd": 0.0,
            "per_part": [],
            "error": f"image-gen backend init failed: {e}",
        }

    per_part_results = []
    total_cost = 0.0
    parts_data_for_phase3 = []

    for p in uv_parts:
        name = p["name"]
        cond_png = out_dir / f"cond_{name}.png"
        uv_json = out_dir / f"uv_{name}.json"
        gen_png = out_dir / f"gen_{name}.png"

        if not cond_png.is_file():
            per_part_results.append({
                "name": name, "success": False,
                "error": f"condition image not found: {cond_png}",
                "cost_usd": 0.0,
            })
            continue

        prefix = _UV_PROMPT_PREFIX
        ref_imgs = p.get("reference_images") or []
        if ref_imgs:
            full_prompt = (
                f"{prefix} {p['prompt']}\n\n"
                f"Style reference: the additional image(s) attached after the UV layout "
                f"show the desired visual style / material appearance. Match their look."
            )
        else:
            full_prompt = f"{prefix} {p['prompt']}"

        result = img_backend.generate(
            full_prompt, condition_image=cond_png,
            reference_images=ref_imgs, size=size,
        )
        cost = float(result.cost_usd or 0.0)
        total_cost += cost

        if not result.success:
            per_part_results.append({
                "name": name, "success": False,
                "error": result.error or "unknown",
                "cost_usd": cost, "model": result.model,
            })
            continue

        gen_png.write_bytes(result.png_bytes)
        per_part_results.append({
            "name": name, "success": True,
            "cost_usd": cost, "model": result.model,
            "duration_s": result.duration_s,
        })
        parts_data_for_phase3.append({
            "name": name,
            "gen_png": str(gen_png),
            "uv_json": str(uv_json),
        })
        print(f"[texture_uv_atlas]   {name}: ✓ ${cost:.4f} {result.duration_s:.1f}s")

    if not parts_data_for_phase3:
        return {
            "success": False,
            "parts_textured": 0,
            "render_dir": str(out_dir),
            "glb_path": "",
            "total_cost_usd": total_cost,
            "per_part": per_part_results,
            "error": "all Gemini calls failed",
        }

    # ── Phase 3: apply textures + render + export (single Blender) ───────
    render_dir = out_dir / "renders"
    glb_path = ws / glb_relpath
    textured_parts_dir = out_dir / "parts"

    print(f"[texture_uv_atlas] phase 3: applying {len(parts_data_for_phase3)} textures + rendering...")
    phase3_args = {
        "slug_src_dir": str(slug_src),
        "parts_data": parts_data_for_phase3,
        "resolution": render_resolution,
        "n_views": n_views,
        "out_dir": str(render_dir),
        "glb_out": str(glb_path),
        "parts_dir": str(textured_parts_dir),
    }
    res3 = run_blender(
        script=_HERE / "_apply_all.py",
        cwd=out_dir,
        timeout_s=min(timeout_s, 300),
        script_args=[json.dumps(phase3_args)],
    )
    if not res3.success:
        print(res3.stdout)
        print(res3.stderr)
        return {
            "success": False,
            "parts_textured": len(parts_data_for_phase3),
            "render_dir": str(render_dir),
            "glb_path": "",
            "total_cost_usd": total_cost,
            "per_part": per_part_results,
            "error": f"phase 3 (apply+render) failed: exit={res3.exit_code}",
            "stderr": res3.stderr[-2000:],
        }
    print(res3.stdout)

    # ── Phase 4: write URDF with textured per-part GLBs ──────────────────
    urdf_path_str = ""
    joints_path = ws / joints_relpath
    manifest_path = textured_parts_dir / "manifest.json"
    if joints_path.is_file() and manifest_path.is_file():
        try:
            import yaml
            from topos.urdf import Joint, Link, write_urdf

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            obj_by_name = {o["name"]: o for o in manifest.get("objects", [])}

            spec = yaml.safe_load(joints_path.read_text(encoding="utf-8")) or {}
            robot_name = spec.get("robot") or ws.name
            urdf_path = ws / urdf_relpath
            urdf_path.parent.mkdir(parents=True, exist_ok=True)
            parts_rel = textured_parts_dir.relative_to(urdf_path.parent)

            links = []
            for lspec in spec.get("links") or []:
                name = lspec["name"]
                bpy_name = lspec.get("object") or name
                if bpy_name not in obj_by_name:
                    print(f"[texture_uv_atlas] URDF: link {name!r} → object {bpy_name!r} not in manifest, skipping")
                    continue
                info = obj_by_name[bpy_name]
                mesh_file = info.get("mesh_path") or info.get("obj_path")
                links.append(Link(
                    name=name,
                    mesh_path=str(parts_rel / mesh_file),
                    world_xyz=tuple(info["world_xyz"]),
                    world_rpy=tuple(info.get("world_rpy") or (0, 0, 0)),
                    color_rgba=tuple(lspec["color_rgba"]) if lspec.get("color_rgba") else None,
                ))

            joints = []
            for jspec in spec.get("joints") or []:
                limit = jspec.get("limit") or [0, 0]
                joints.append(Joint(
                    name=jspec["name"],
                    type=jspec["type"],
                    parent=jspec["parent"],
                    child=jspec["child"],
                    origin_xyz=tuple(jspec.get("origin") or jspec.get("origin_xyz") or (0, 0, 0)),
                    origin_rpy=tuple(jspec.get("rpy") or jspec.get("origin_rpy") or (0, 0, 0)),
                    axis=tuple(jspec.get("axis") or (0, 0, 1)),
                    limit_lower=float(limit[0]),
                    limit_upper=float(limit[1]),
                    limit_effort=float(jspec.get("effort", 10.0)),
                    limit_velocity=float(jspec.get("velocity", 1.0)),
                ))

            write_urdf(robot_name, links, joints, urdf_path)
            urdf_path_str = str(urdf_path.relative_to(ws))
            print(f"[texture_uv_atlas] URDF: wrote {urdf_path} ({len(links)} links, {len(joints)} joints)")
        except Exception as e:
            print(f"[texture_uv_atlas] URDF: failed — {e}")
    else:
        print(f"[texture_uv_atlas] URDF: skipped (joints={joints_path.exists()} manifest={manifest_path.exists()})")

    return {
        "success": True,
        "parts_textured": len(parts_data_for_phase3),
        "render_dir": str(render_dir.relative_to(ws)),
        "glb_path": str(glb_path.relative_to(ws)),
        "urdf_path": urdf_path_str,
        "total_cost_usd": total_cost,
        "per_part": per_part_results,
    }
