"""Phase 3 soft-blend fusion.

Same N gen images as the hard-assignment fusion, but instead of picking
one view per face, builds a single material that blends all N textures
per-pixel by:

    w_i  = max(0, n · v_dir_i)^sharpness
    out  = sum(w_i * texture_i(uv_i)) / (sum(w_i) + eps)

where `texture_i` is sampled through `uv_i` — one UV layer per view,
analytically projected from that view's camera.

Curved surfaces (cylindrical posts, organic forms) no longer show the
view-region seams the hard fusion produced. Cost: O(N) extra shader
nodes + O(N) UV layers per part.

Args (JSON after `--`): same as `_blender_apply_multiview.py`.
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
)
from _common import VIEW_DIRECTIONS, CamSidecar, parse_blender_args  # noqa: E402


_SHARPNESS = 4.0      # exponent on (n·v_dir); higher = harder transitions
_EPS = 1e-5


def _populate_uv_layer(mesh, mw, uv_name: str, sidecar: CamSidecar) -> None:
    """Create/overwrite a UV layer with analytical view-projection UVs."""
    cam_mw = Matrix(sidecar.matrix_world_rows)
    cam_inv = cam_mw.inverted()
    ortho = sidecar.ortho_scale
    half = ortho * 0.5

    if uv_name in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[uv_name])
    uv_layer_obj = mesh.uv_layers.new(name=uv_name)
    uv_layer = uv_layer_obj.data
    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            v_idx = mesh.loops[loop_idx].vertex_index
            v_world = mw @ Vector(mesh.vertices[v_idx].co)
            v_cam = cam_inv @ v_world
            u = (v_cam.x + half) / ortho
            vv = (v_cam.y + half) / ortho
            uv_layer[loop_idx].uv = (u, vv)


def _build_softblend_material(name: str, view_specs: list[dict]):
    """Build a single material that blends N textures by direction weights.

    view_specs: list of {view: str, image_path: Path, uv_name: str}
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    geom = nt.nodes.new("ShaderNodeNewGeometry")
    normal_out = geom.outputs["Normal"]

    weight_outs = []   # scalar (per-view raw weights)
    color_outs = []    # vector (per-view RGB)
    for spec in view_specs:
        uv_node = nt.nodes.new("ShaderNodeUVMap")
        uv_node.uv_map = spec["uv_name"]

        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(spec["image_path"]),
                                          check_existing=False)
        tex.extension = "CLIP"
        tex.interpolation = "Linear"
        nt.links.new(uv_node.outputs["UV"], tex.inputs["Vector"])
        color_outs.append(tex.outputs["Color"])

        # Direction constant vector via Combine XYZ.
        v_dir = Vector(VIEW_DIRECTIONS[spec["view"]])
        combine = nt.nodes.new("ShaderNodeCombineXYZ")
        combine.inputs["X"].default_value = v_dir.x
        combine.inputs["Y"].default_value = v_dir.y
        combine.inputs["Z"].default_value = v_dir.z

        # dot = n · v_dir
        dot = nt.nodes.new("ShaderNodeVectorMath")
        dot.operation = "DOT_PRODUCT"
        nt.links.new(normal_out, dot.inputs[0])
        nt.links.new(combine.outputs["Vector"], dot.inputs[1])

        # max(0, dot)
        clip0 = nt.nodes.new("ShaderNodeMath")
        clip0.operation = "MAXIMUM"
        nt.links.new(dot.outputs["Value"], clip0.inputs[0])
        clip0.inputs[1].default_value = 0.0

        # weight = clip^sharpness
        pwr = nt.nodes.new("ShaderNodeMath")
        pwr.operation = "POWER"
        nt.links.new(clip0.outputs[0], pwr.inputs[0])
        pwr.inputs[1].default_value = _SHARPNESS
        weight_outs.append(pwr.outputs[0])

    # Sum of weights (scalar).
    sum_out = weight_outs[0]
    for w in weight_outs[1:]:
        s = nt.nodes.new("ShaderNodeMath")
        s.operation = "ADD"
        nt.links.new(sum_out, s.inputs[0])
        nt.links.new(w, s.inputs[1])
        sum_out = s.outputs[0]

    # sum + eps to avoid div-by-zero at silhouette grazing angles.
    sum_eps = nt.nodes.new("ShaderNodeMath")
    sum_eps.operation = "ADD"
    nt.links.new(sum_out, sum_eps.inputs[0])
    sum_eps.inputs[1].default_value = _EPS
    sum_norm = sum_eps.outputs[0]

    # Weighted sum of colors (vectors).
    final = None
    for w_out, c_out in zip(weight_outs, color_outs):
        # normalized weight = w / sum
        norm = nt.nodes.new("ShaderNodeMath")
        norm.operation = "DIVIDE"
        nt.links.new(w_out, norm.inputs[0])
        nt.links.new(sum_norm, norm.inputs[1])

        # scaled color = color * normalized_weight
        scl = nt.nodes.new("ShaderNodeVectorMath")
        scl.operation = "SCALE"
        nt.links.new(c_out, scl.inputs[0])
        nt.links.new(norm.outputs[0], scl.inputs["Scale"])

        if final is None:
            final = scl.outputs[0]
        else:
            add = nt.nodes.new("ShaderNodeVectorMath")
            add.operation = "ADD"
            nt.links.new(final, add.inputs[0])
            nt.links.new(scl.outputs[0], add.inputs[1])
            final = add.outputs[0]

    # Hook to Principled BSDF base color.
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.65
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(final, bsdf.inputs["Base Color"])

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _configure_eevee() -> None:
    scene = bpy.context.scene
    available = {e.identifier for e in scene.render.bl_rna.properties["engine"].enum_items}
    for choice in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if choice in available:
            scene.render.engine = choice
            return
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16


def _add_three_point_light() -> None:
    key = bpy.data.objects.new("sb_key", bpy.data.lights.new("sb_kl", "SUN"))
    bpy.context.collection.objects.link(key)
    key.data.energy = 1.2
    key.rotation_euler = (0.6, 0.15, 0.4)
    fill = bpy.data.objects.new("sb_fill", bpy.data.lights.new("sb_fl", "SUN"))
    bpy.context.collection.objects.link(fill)
    fill.data.energy = 0.4
    fill.rotation_euler = (-0.3, -0.6, -0.8)


def _export_glb(obj, glb_out: Path) -> None:
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
    glb_out      = Path(args["glb_out"]) if args.get("glb_out") else None

    print(f"[phase3sb] slug_src={slug_src_dir} part={part_name} "
          f"views={[v['view'] for v in views_data]} "
          f"sharpness={_SHARPNESS}")

    load_scene_from_slug(slug_src_dir)
    obj = isolate_part(part_name)
    mesh = obj.data
    mw = obj.matrix_world

    # Build per-view UV layers + collect view_specs for the material.
    # Soft-blend ignores the "side" key — it just blends every provided view.
    view_specs = []
    front_cam_mw = None
    front_ortho  = None
    for i, vd in enumerate(views_data):
        view = vd["view"]
        uv_name = f"uv_sb_{i:02d}_{view}_{vd.get('side', 'outer')}"
        sidecar = CamSidecar.load(Path(vd["cam_path"]))
        _populate_uv_layer(mesh, mw, uv_name, sidecar)
        view_specs.append({
            "view": view,
            "image_path": Path(vd["image_path"]),
            "uv_name": uv_name,
        })
        if view == "front" and front_cam_mw is None:
            front_cam_mw = Matrix(sidecar.matrix_world_rows)
            front_ortho  = sidecar.ortho_scale

    mesh.update()

    # Single material handles all the blending.
    mat = _build_softblend_material(f"sb_{part_name}", view_specs)
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    _configure_eevee()
    set_white_world_background()
    _add_three_point_light()

    # Diagnostic renders.
    if front_cam_mw is None:
        front_cam_mw = Matrix(CamSidecar.load(Path(views_data[0]["cam_path"])).matrix_world_rows)
        front_ortho  = CamSidecar.load(Path(views_data[0]["cam_path"])).ortho_scale

    cam_data = bpy.data.cameras.new("sb_diag_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = front_ortho
    cam_data.clip_start = 0.001
    cam_data.clip_end = 100.0
    cam_obj = bpy.data.objects.new("sb_diag_cam", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.matrix_world = front_cam_mw
    bpy.context.scene.camera = cam_obj
    scene = bpy.context.scene
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    render_to_png(final_front)
    print(f"[phase3sb] wrote {final_front}")

    _cam, _ = place_ortho_camera(obj, view="front_3q", size=size)
    render_to_png(final_3q)
    print(f"[phase3sb] wrote {final_3q}")

    _cam, _ = place_ortho_camera(obj, view="back", size=size)
    render_to_png(final_back)
    print(f"[phase3sb] wrote {final_back}")

    if keep_blend and blend_out is not None:
        blend_out.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_out))
        print(f"[phase3sb] wrote {blend_out}")

    if glb_out is not None:
        _export_glb(obj, glb_out)
        print(f"[phase3sb] wrote {glb_out}")


try:
    main()
except Exception as e:
    import traceback
    print(f"[phase3sb] FAILED: {e}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
