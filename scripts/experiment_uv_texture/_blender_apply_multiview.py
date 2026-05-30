"""Phase 3 multi-view fusion — assign each face to its best-matching view.

Strategy:
- Build one material per view, each with that view's gen.png as base color.
- For each polygon, score n_world · view_direction across all input views;
  the view with the largest positive dot wins (face points toward that
  camera most directly).
- Set poly.material_index to the winning view's slot.
- Compute UV for the polygon's loops using that view's camera (analytical
  ortho projection, same math as projections/analytical_view.py).

Result: each mesh face shows the texture from whichever view captured it
best — the back side stops being a mirror of the front, the left/right
sides get their own treatment, top/bottom likewise.

Args (JSON after `--`):
    slug_src_dir : absolute path to outputs/<slug>/src/
    part_name    : object name
    size         : square render resolution
    views_data   : list[{view, image_path, cam_path}]
    final_front  : path for the same-view diagnostic render (uses the
                   front-most view's camera if present, else view[0])
    final_3q     : path for the 3/4 diagnostic render
    final_back   : path for the back diagnostic render
    keep_blend   : optional bool
    blend_out    : optional absolute path
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

from _blender_common import (  # noqa: E402
    isolate_part,
    load_scene_from_slug,
    place_ortho_camera,
    render_to_png,
    set_white_world_background,
    world_aabb,
)
from _common import VIEW_DIRECTIONS, CamSidecar, parse_blender_args  # noqa: E402


def _build_view_material(name: str, image_path: Path):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    tex = nt.nodes.new("ShaderNodeTexImage")
    img = bpy.data.images.load(str(image_path), check_existing=False)
    tex.image = img
    tex.extension = "CLIP"
    tex.interpolation = "Linear"

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.65
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _add_three_point_light() -> None:
    key = bpy.data.objects.new("mv_key", bpy.data.lights.new("mv_kl", "SUN"))
    bpy.context.collection.objects.link(key)
    key.data.energy = 1.2
    key.rotation_euler = (0.6, 0.15, 0.4)
    fill = bpy.data.objects.new("mv_fill", bpy.data.lights.new("mv_fl", "SUN"))
    bpy.context.collection.objects.link(fill)
    fill.data.energy = 0.4
    fill.rotation_euler = (-0.3, -0.6, -0.8)


def _configure_eevee() -> None:
    scene = bpy.context.scene
    available = {e.identifier for e in scene.render.bl_rna.properties["engine"].enum_items}
    for choice in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if choice in available:
            scene.render.engine = choice
            return
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16


def main() -> None:
    args = parse_blender_args(sys.argv)
    slug_src_dir = Path(args["slug_src_dir"])
    part_name    = str(args["part_name"])
    size         = int(args["size"])
    views_data   = args["views_data"]
    final_front  = Path(args["final_front"])
    final_3q     = Path(args["final_3q"])
    final_back   = Path(args["final_back"])
    keep_blend   = bool(args.get("keep_blend", False))
    blend_out    = Path(args["blend_out"]) if args.get("blend_out") else None

    print(f"[phase3mv] slug_src={slug_src_dir} part={part_name} "
          f"views={[v['view'] for v in views_data]}")

    load_scene_from_slug(slug_src_dir)
    obj = isolate_part(part_name)

    # Load all view sidecars + build N materials. Each views_data entry may
    # carry an optional "side" key ∈ {"outer", "inner"}; absent ⇒ "outer".
    per_view = []
    mats = []
    for i, vd in enumerate(views_data):
        view = vd["view"]
        side = vd.get("side", "outer")
        img  = Path(vd["image_path"])
        cam_path = Path(vd["cam_path"])
        sidecar = CamSidecar.load(cam_path)
        cam_mw = Matrix(sidecar.matrix_world_rows)
        mat = _build_view_material(f"mvfusion_{side}_{view}", img)
        mats.append(mat)
        per_view.append({
            "view":        view,
            "side":        side,
            "cam_mw":      cam_mw,
            "cam_inv":     cam_mw.inverted(),
            "ortho_scale": sidecar.ortho_scale,
            "view_dir":    Vector(VIEW_DIRECTIONS[view]),
        })

    obj.data.materials.clear()
    for mat in mats:
        obj.data.materials.append(mat)

    # Per-face: classify outer/inner if both sides present, pick best view
    # from the matching subset, assign material_index, write UVs.
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="uv_mv_fusion")
    uv_layer = mesh.uv_layers.active.data

    mw = obj.matrix_world
    obj_rot = mw.to_3x3()

    sides_present = set(pv["side"] for pv in per_view)
    has_split = ("outer" in sides_present and "inner" in sides_present)

    bbox_center, _, _ = world_aabb(obj)

    # Index per_view by side for fast subset lookup.
    by_side: dict[str, list[tuple[int, dict]]] = {"outer": [], "inner": []}
    for i, pv in enumerate(per_view):
        by_side.setdefault(pv["side"], []).append((i, pv))

    face_assign_counts: dict[str, int] = {}
    for poly in mesh.polygons:
        n_world = (obj_rot @ poly.normal).normalized()

        # Outer/inner classification: a face whose normal points TOWARD the
        # bbox center is on an internal cavity wall; pointing AWAY = external
        # shell. Faces near the geometric center (vec length ~0) default to
        # outer.
        face_center_world = mw @ poly.center
        vec_to_center = bbox_center - face_center_world
        if has_split and vec_to_center.length > 1e-5:
            is_inner = n_world.dot(vec_to_center.normalized()) > 0.0
            face_side = "inner" if is_inner else "outer"
            candidates = by_side[face_side] or by_side["outer"]
        else:
            face_side = "outer"
            candidates = list(enumerate(per_view))

        best_i, best_score = candidates[0][0], -2.0
        for i, pv in candidates:
            s = n_world.dot(pv["view_dir"])
            if s > best_score:
                best_score = s
                best_i = i
        poly.material_index = best_i
        chosen = per_view[best_i]

        key = f"{chosen['side']}_{chosen['view']}"
        face_assign_counts[key] = face_assign_counts.get(key, 0) + 1

        cam_inv = chosen["cam_inv"]
        half = chosen["ortho_scale"] * 0.5
        ortho = chosen["ortho_scale"]
        for loop_idx in poly.loop_indices:
            loop = mesh.loops[loop_idx]
            v_world = mw @ Vector(mesh.vertices[loop.vertex_index].co)
            v_cam = cam_inv @ v_world
            u = (v_cam.x + half) / ortho
            v = (v_cam.y + half) / ortho
            uv_layer[loop_idx].uv = (u, v)
    mesh.update()

    print(f"[phase3mv] split={has_split} face assignment: {face_assign_counts}")

    _configure_eevee()
    set_white_world_background()
    _add_three_point_light()

    # Diagnostic renders. Reuse the front-view camera if available so the
    # "same-view" render lines up with what the user prompted against.
    front_pv = next((pv for pv in per_view if pv["view"] == "front"), per_view[0])

    cam_data = bpy.data.cameras.new("mv_diag_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = front_pv["ortho_scale"]
    cam_data.clip_start = 0.001
    cam_data.clip_end = 100.0
    cam_obj = bpy.data.objects.new("mv_diag_cam", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.matrix_world = front_pv["cam_mw"]
    bpy.context.scene.camera = cam_obj
    scene = bpy.context.scene
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    render_to_png(final_front)
    print(f"[phase3mv] wrote {final_front}")

    _cam, _ = place_ortho_camera(obj, view="front_3q", size=size)
    render_to_png(final_3q)
    print(f"[phase3mv] wrote {final_3q}")

    _cam, _ = place_ortho_camera(obj, view="back", size=size)
    render_to_png(final_back)
    print(f"[phase3mv] wrote {final_back}")

    if keep_blend and blend_out is not None:
        blend_out.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_out))
        print(f"[phase3mv] wrote {blend_out}")

    glb_out = Path(args["glb_out"]) if args.get("glb_out") else None
    if glb_out is not None:
        _export_glb(obj, glb_out)
        print(f"[phase3mv] wrote {glb_out}")


def _export_glb(obj, glb_out: Path) -> None:
    """Export only the textured part as a self-contained GLB."""
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
    print(f"[phase3mv] FAILED: {e}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
