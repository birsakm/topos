"""Phase 3 — apply generated texture + multi-view diagnostic renders.

Args (single JSON blob after `--`):
    slug_src_dir : absolute path to outputs/<slug>/src/
    part_name    : object name to texture
    view         : view used in phase 1 (sidecar will agree)
    size         : square render resolution
    projection   : key in projections.REGISTRY
    gen_png_in   : absolute path to Gemini-returned PNG
    cam_json_in  : absolute path to phase-1 camera sidecar
    final_front  : absolute path for the same-view re-render
    final_3q     : absolute path for the 3/4 view re-render
    final_back   : absolute path for the back view re-render
    keep_blend   : optional bool; if True, save a .blend snapshot
    blend_out    : absolute path for the optional .blend
"""

from __future__ import annotations

import sys
from pathlib import Path

# sibling imports
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import bpy  # noqa: E402

from _blender_common import (  # noqa: E402
    isolate_part,
    load_scene_from_slug,
    place_ortho_camera,
    render_to_png,
    restore_camera_from_sidecar,
    set_white_world_background,
)
from _common import CamSidecar, parse_blender_args  # noqa: E402
import projections                                  # noqa: E402


def _configure_eevee_render() -> None:
    """Pick a fast realtime engine; tolerate Blender version drift between
    BLENDER_EEVEE (3.x) and BLENDER_EEVEE_NEXT (4.2+)."""
    scene = bpy.context.scene
    available = {e.identifier for e in scene.render.bl_rna.properties["engine"].enum_items}
    for choice in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if choice in available:
            scene.render.engine = choice
            return
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16


def _add_three_point_light() -> None:
    """Add a simple 3-light rig so the textured part is legible at all views."""
    key = bpy.data.objects.new("uv_tex_exp_key", bpy.data.lights.new("key", "SUN"))
    bpy.context.collection.objects.link(key)
    key.data.energy = 3.0
    key.rotation_euler = (0.7, 0.2, 0.4)

    fill = bpy.data.objects.new("uv_tex_exp_fill", bpy.data.lights.new("fill", "SUN"))
    bpy.context.collection.objects.link(fill)
    fill.data.energy = 1.0
    fill.rotation_euler = (0.4, -0.7, -1.0)


def main() -> None:
    args = parse_blender_args(sys.argv)

    slug_src_dir = Path(args["slug_src_dir"])
    part_name    = str(args["part_name"])
    view         = str(args["view"])
    size         = int(args["size"])
    projection   = str(args["projection"])
    gen_png_in   = Path(args["gen_png_in"])
    cam_json_in  = Path(args["cam_json_in"])
    final_front  = Path(args["final_front"])
    final_3q     = Path(args["final_3q"])
    final_back   = Path(args["final_back"])
    keep_blend   = bool(args.get("keep_blend", False))
    blend_out    = Path(args["blend_out"]) if args.get("blend_out") else None

    print(f"[phase3] slug_src={slug_src_dir} part={part_name} view={view} "
          f"projection={projection}")

    load_scene_from_slug(slug_src_dir)
    obj = isolate_part(part_name)

    apply_fn = projections.get(projection)
    apply_fn(
        obj,
        image_path=gen_png_in,
        cam_path=cam_json_in,
        view=view,
    )

    _configure_eevee_render()
    set_white_world_background()
    _add_three_point_light()

    # Same-view re-render — uses the phase-1 camera exactly.
    sidecar = CamSidecar.load(cam_json_in)
    restore_camera_from_sidecar(sidecar)
    render_to_png(final_front)
    print(f"[phase3] wrote {final_front}")

    # 3/4 view — fresh ortho camera at front_3q.
    _cam_3q, _sc_3q = place_ortho_camera(obj, view="front_3q", size=size)
    render_to_png(final_3q)
    print(f"[phase3] wrote {final_3q}")

    # Back view.
    _cam_back, _sc_back = place_ortho_camera(obj, view="back", size=size)
    render_to_png(final_back)
    print(f"[phase3] wrote {final_back}")

    if keep_blend and blend_out is not None:
        blend_out.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_out))
        print(f"[phase3] wrote {blend_out}")

    glb_out = Path(args["glb_out"]) if args.get("glb_out") else None
    if glb_out is not None:
        _export_glb(obj, glb_out)
        print(f"[phase3] wrote {glb_out}")


def _export_glb(obj, glb_out: Path) -> None:
    """Export only the textured part as a self-contained GLB with embedded textures."""
    glb_out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(
        filepath=str(glb_out),
        export_format="GLB",
        use_selection=True,
        export_image_format="AUTO",
        export_apply=True,
        export_extras=False,
    )


try:
    main()
except Exception as e:
    import traceback
    print(f"[phase3] FAILED: {e}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
