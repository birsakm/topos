"""Blender-side UV atlas apply + render + export — phase 3.

Invoked via ``blender --background --python _apply_all.py -- <json>``.

For each part listed in args:
1. Restore UV layer from sidecar JSON
2. Create material binding the generated texture PNG
Then render multiview of the full scene + export GLB.

Self-contained: no ``topos`` imports. Runs inside Blender's bundled Python.
"""

from __future__ import annotations

import json
import math
import runpy
import sys
from pathlib import Path

import bpy
from mathutils import Vector

# ── 8 octant viewpoints (matches render wrapper convention) ──────────────
OCTANT_VIEWS = [
    (270, 30, "front_low"),
    (  0, 30, "right_low"),
    ( 90, 30, "back_low"),
    (180, 30, "left_low"),
    (315, 60, "front_right_high"),
    ( 45, 60, "back_right_high"),
    (135, 60, "back_left_high"),
    (225, 60, "front_left_high"),
]


def _parse_args():
    sep = sys.argv.index("--")
    return json.loads(sys.argv[sep + 1])


def _load_scene(slug_src_dir: Path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build_py = slug_src_dir / "build.py"
    return runpy.run_path(str(build_py), run_name="__main__")


def _scene_aabb():
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for c in obj.bound_box:
            w = obj.matrix_world @ Vector(c)
            lo.x = min(lo.x, w.x); lo.y = min(lo.y, w.y); lo.z = min(lo.z, w.z)
            hi.x = max(hi.x, w.x); hi.y = max(hi.y, w.y); hi.z = max(hi.z, w.z)
    center = (lo + hi) * 0.5
    extents = hi - lo
    return center, extents


def _restore_uv_and_texture(obj, gen_png: Path, uv_json: Path):
    sidecar = json.loads(uv_json.read_text())
    uv_name = sidecar["uv_layer_name"]
    uvs = sidecar["uvs"]
    mesh = obj.data

    if uv_name in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[uv_name])
    uv_layer = mesh.uv_layers.new(name=uv_name)
    uv_data = uv_layer.data

    if len(uvs) != len(uv_data):
        print(f"[apply] WARNING: UV loop count mismatch for {obj.name}: "
              f"sidecar={len(uvs)} mesh={len(uv_data)}")

    for i, (u, v) in enumerate(uvs):
        if i < len(uv_data):
            uv_data[i].uv = (u, v)
    mesh.uv_layers.active_index = list(mesh.uv_layers).index(uv_layer)

    mat = bpy.data.materials.new(f"uvatlas_{obj.name}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    uv_node = nt.nodes.new("ShaderNodeUVMap")
    uv_node.uv_map = uv_name

    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(gen_png), check_existing=False)
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
    mesh.update()
    print(f"[apply] textured {obj.name} from {gen_png.name}")


def _configure_eevee():
    scene = bpy.context.scene
    available = {e.identifier for e in scene.render.bl_rna.properties["engine"].enum_items}
    for choice in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if choice in available:
            scene.render.engine = choice
            return
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16


def _add_lighting():
    key = bpy.data.objects.new("uva_key", bpy.data.lights.new("uva_kl", "SUN"))
    bpy.context.collection.objects.link(key)
    key.data.energy = 1.2
    key.rotation_euler = (0.6, 0.15, 0.4)
    fill = bpy.data.objects.new("uva_fill", bpy.data.lights.new("uva_fl", "SUN"))
    bpy.context.collection.objects.link(fill)
    fill.data.energy = 0.4
    fill.rotation_euler = (-0.3, -0.6, -0.8)


def _set_white_world():
    world = bpy.data.worlds.new("uva_world")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    out_node = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], out_node.inputs["Surface"])


def _render_to_png(out_path: Path):
    scene = bpy.context.scene
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = str(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)


def _render_multiview(center, extents, resolution: int, out_dir: Path, n_views: int):
    half_diag = extents.length * 0.5
    cam_distance = max(half_diag * 3.5, 0.5)
    margin = 1.15

    cam_data = bpy.data.cameras.new("uva_cam")
    cam_data.type = "ORTHO"
    cam_data.clip_start = 0.001
    cam_data.clip_end = cam_distance * 4.0 + 10.0
    cam_obj = bpy.data.objects.new("uva_cam", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    scene = bpy.context.scene
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100

    out_dir.mkdir(parents=True, exist_ok=True)

    for i, (az_deg, el_deg, label) in enumerate(OCTANT_VIEWS[:n_views]):
        az = math.radians(az_deg)
        el = math.radians(el_deg)
        dx = math.cos(el) * math.cos(az)
        dy = math.cos(el) * math.sin(az)
        dz = math.sin(el)
        direction = Vector((dx, dy, dz)).normalized()
        cam_loc = center + direction * cam_distance
        forward = (center - cam_loc).normalized()

        world_up = Vector((0, 0, 1))
        if abs(forward.dot(world_up)) > 0.999:
            world_up = Vector((0, 1, 0))
        right = forward.cross(world_up).normalized()
        up = right.cross(forward).normalized()

        from mathutils import Matrix
        rot_3x3 = Matrix((right, up, -forward)).transposed()
        cam_obj.matrix_world = Matrix.Translation(cam_loc) @ rot_3x3.to_4x4()

        ex = extents
        screen_x = abs(right.x)*ex.x + abs(right.y)*ex.y + abs(right.z)*ex.z
        screen_y = abs(up.x)*ex.x + abs(up.y)*ex.y + abs(up.z)*ex.z
        cam_data.ortho_scale = max(screen_x, screen_y) * margin

        out_path = out_dir / f"view_{i}.png"
        _render_to_png(out_path)
        print(f"[apply] rendered {label} → {out_path.name}")


def _export_glb(glb_out: Path):
    glb_out.parent.mkdir(parents=True, exist_ok=True)
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.select_set(True)
    bpy.ops.export_scene.gltf(
        filepath=str(glb_out),
        export_format="GLB",
        use_selection=True,
        export_image_format="AUTO",
        export_apply=True,
        export_extras=False,
    )
    print(f"[apply] wrote {glb_out}")


def _export_per_part_glbs(parts_dir: Path):
    """Export each MESH object as a separate textured GLB + write manifest.json.

    Each part GLB is in object-local coords (origin-centered, rotation/scale
    baked into mesh). The manifest records world_xyz so the URDF writer can
    reconstruct link frames. Format matches export/wrapper.py --mode parts.
    """
    parts_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"objects": []}
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]

    for obj in mesh_objs:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        loc = obj.matrix_world.to_translation()

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.duplicate()
        dup = bpy.context.active_object
        try:
            dup.location = (0.0, 0.0, 0.0)
            bpy.context.view_layer.update()
            bpy.ops.object.select_all(action="DESELECT")
            dup.select_set(True)
            bpy.context.view_layer.objects.active = dup
            out_path = parts_dir / f"{obj.name}.glb"
            bpy.ops.export_scene.gltf(
                filepath=str(out_path),
                export_format="GLB",
                use_selection=True,
                export_image_format="AUTO",
                export_apply=True,
                export_yup=False,
            )
            manifest["objects"].append({
                "name": obj.name,
                "mesh_path": out_path.name,
                "world_xyz": [loc.x, loc.y, loc.z],
                "world_rpy": [0.0, 0.0, 0.0],
                "world_scale": [1.0, 1.0, 1.0],
                "vertex_count": len(obj.data.vertices),
            })
            print(f"[apply] part GLB: {obj.name} → {out_path.name}")
        finally:
            bpy.data.objects.remove(dup, do_unlink=True)

    manifest_path = parts_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[apply] manifest: {len(manifest['objects'])} textured parts")


# ── main ─────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()
    slug_src = Path(args["slug_src_dir"])
    parts_data = args["parts_data"]  # [{name, gen_png, uv_json}]
    resolution = int(args.get("resolution", 512))
    n_views = int(args.get("n_views", 8))
    out_dir = Path(args["out_dir"])
    glb_out = Path(args["glb_out"]) if args.get("glb_out") else None
    parts_dir = Path(args["parts_dir"]) if args.get("parts_dir") else None

    _load_scene(slug_src)

    for pd in parts_data:
        obj = bpy.data.objects.get(pd["name"])
        if obj is None:
            print(f"[apply] WARNING: part {pd['name']!r} not in scene, skipping texture")
            continue
        _restore_uv_and_texture(obj, Path(pd["gen_png"]), Path(pd["uv_json"]))

    if parts_dir:
        _export_per_part_glbs(parts_dir)

    _configure_eevee()
    _set_white_world()
    _add_lighting()

    center, extents = _scene_aabb()
    _render_multiview(center, extents, resolution, out_dir, n_views)

    if glb_out:
        _export_glb(glb_out)


try:
    main()
except Exception as e:
    import traceback
    print(f"[apply] FAILED: {e}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
