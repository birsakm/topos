"""Phase 3 — UV atlas apply.

Restore the phase-1 UV layout (from the uv_layer.json sidecar), bind the
Gemini-generated image as the base color of a Principled BSDF using that
UV layer. Render 3 diagnostic views + export GLB.

Args (JSON after `--`):
    slug_src_dir : Path
    part_name    : str
    size         : int
    gen_png_in   : Path
    uv_json_in   : Path
    final_front  : Path
    final_3q     : Path
    final_back   : Path
    glb_out      : Path | ""
    keep_blend   : bool
    blend_out    : Path | ""
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import bpy  # noqa: E402

from _blender_common import (  # noqa: E402
    isolate_part,
    load_scene_from_slug,
    place_ortho_camera,
    render_to_png,
    set_white_world_background,
)
from _common import parse_blender_args  # noqa: E402


def _restore_uv_layer(obj, uv_json_in: Path) -> str:
    raw = json.loads(uv_json_in.read_text())
    uv_name = raw["uv_layer_name"]
    uvs = raw["uvs"]
    mesh = obj.data

    if uv_name in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[uv_name])
    layer_obj = mesh.uv_layers.new(name=uv_name)
    if len(layer_obj.data) != len(uvs):
        raise RuntimeError(
            f"loop count mismatch: mesh has {len(layer_obj.data)} loops "
            f"but sidecar has {len(uvs)} uv records — was build.py changed?"
        )
    for i, (u, v) in enumerate(uvs):
        layer_obj.data[i].uv = (u, v)
    mesh.uv_layers.active_index = list(mesh.uv_layers).index(layer_obj)
    return uv_name


def _attach_textured_material(obj, image_path: Path, uv_name: str) -> None:
    mat = bpy.data.materials.new(name=f"uv_atlas_{obj.name}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    uv_node = nt.nodes.new("ShaderNodeUVMap")
    uv_node.uv_map = uv_name

    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(image_path), check_existing=False)
    tex.extension = "CLIP"
    tex.interpolation = "Linear"
    nt.links.new(uv_node.outputs["UV"], tex.inputs["Vector"])

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.65
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    obj.data.materials.clear()
    obj.data.materials.append(mat)


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
    key = bpy.data.objects.new("at_key", bpy.data.lights.new("at_kl", "SUN"))
    bpy.context.collection.objects.link(key)
    key.data.energy = 1.2
    key.rotation_euler = (0.6, 0.15, 0.4)
    fill = bpy.data.objects.new("at_fill", bpy.data.lights.new("at_fl", "SUN"))
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
    gen_png_in   = Path(args["gen_png_in"])
    uv_json_in   = Path(args["uv_json_in"])
    final_front  = Path(args["final_front"])
    final_3q     = Path(args["final_3q"])
    final_back   = Path(args["final_back"])
    glb_out      = Path(args["glb_out"]) if args.get("glb_out") else None

    print(f"[atlas-apply] slug_src={slug_src_dir} part={part_name}")
    load_scene_from_slug(slug_src_dir)
    obj = isolate_part(part_name)

    uv_name = _restore_uv_layer(obj, uv_json_in)
    _attach_textured_material(obj, gen_png_in, uv_name)

    _configure_eevee()
    set_white_world_background()
    _add_three_point_light()

    _cam, _ = place_ortho_camera(obj, view="front", size=size)
    render_to_png(final_front)
    print(f"[atlas-apply] wrote {final_front}")

    _cam, _ = place_ortho_camera(obj, view="front_3q", size=size)
    render_to_png(final_3q)
    print(f"[atlas-apply] wrote {final_3q}")

    _cam, _ = place_ortho_camera(obj, view="back", size=size)
    render_to_png(final_back)
    print(f"[atlas-apply] wrote {final_back}")

    if glb_out is not None:
        _export_glb(obj, glb_out)
        print(f"[atlas-apply] wrote {glb_out}")


try:
    main()
except Exception as e:
    import traceback
    print(f"[atlas-apply] FAILED: {e}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
