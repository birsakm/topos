"""Blender-side UV atlas condition bake — phase 1.

Invoked via ``blender --background --python _bake_cond.py -- <json>``.

For each part listed in args, this script:
1. Loads the scene via build.py
2. Isolates the part (hides others)
3. UV-unwraps onto a cube-atlas or cylinder-atlas layout
4. Bakes directional-lit diffuse shading into a light-gray-base image
5. Saves cond_<part>.png + uv_<part>.json sidecar
6. Un-isolates for the next part

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

# ── constants (embedded, no external imports) ────────────────────────────

TILE_W = 1.0 / 3.0
TILE_H_6 = 1.0 / 2.0
TILE_H_12 = 1.0 / 4.0
TILE_MARGIN = 0.03

_UV_LAYER_NAME = "uv_atlas"
_BAKE_MARGIN_PX = 8
_ENDCAP_NORMAL_THRESH = 0.7


# ── helpers ──────────────────────────────────────────────────────────────

def _parse_args():
    sep = sys.argv.index("--")
    return json.loads(sys.argv[sep + 1])


def _load_scene(slug_src_dir: Path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build_py = slug_src_dir / "build.py"
    if not build_py.is_file():
        raise FileNotFoundError(f"build.py not found at {build_py}")
    return runpy.run_path(str(build_py), run_name="__main__")


def _isolate(part_name: str):
    target = bpy.data.objects.get(part_name)
    if target is None:
        avail = sorted(o.name for o in bpy.data.objects if o.type == "MESH")
        raise KeyError(f"part {part_name!r} not in scene. MESH objects: {avail}")
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        obj.hide_render = (obj.name != part_name)
        obj.hide_viewport = (obj.name != part_name)
    return target


def _unisolate():
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.hide_render = False
            obj.hide_viewport = False


def _world_aabb(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs, ys, zs = zip(*[(v.x, v.y, v.z) for v in corners])
    lo = Vector((min(xs), min(ys), min(zs)))
    hi = Vector((max(xs), max(ys), max(zs)))
    center = (lo + hi) * 0.5
    extents = hi - lo
    return center, extents


# ── cube-atlas UV unwrap ─────────────────────────────────────────────────

CUBE_ATLAS_6 = {
    (0, -1): (0, 0), (1, -1): (1, 0), (2, -1): (2, 0),
    (0, +1): (0, 1), (1, +1): (1, 1), (2, +1): (2, 1),
}
CUBE_ATLAS_12 = {
    (0, -1, "outer"): (0, 0), (1, -1, "outer"): (1, 0), (2, -1, "outer"): (2, 0),
    (0, +1, "outer"): (0, 1), (1, +1, "outer"): (1, 1), (2, +1, "outer"): (2, 1),
    (0, -1, "inner"): (0, 2), (1, -1, "inner"): (1, 2), (2, -1, "inner"): (2, 2),
    (0, +1, "inner"): (0, 3), (1, +1, "inner"): (1, 3), (2, +1, "inner"): (2, 3),
}


def _cube_atlas_unwrap(obj, *, dual: bool):
    mesh = obj.data
    if _UV_LAYER_NAME in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[_UV_LAYER_NAME])
    uv_layer = mesh.uv_layers.new(name=_UV_LAYER_NAME)
    uv_data = uv_layer.data
    mesh.uv_layers.active_index = list(mesh.uv_layers).index(uv_layer)

    center, extents = _world_aabb(obj)
    mw = obj.matrix_world
    obj_rot = mw.to_3x3()

    tile_h = TILE_H_12 if dual else TILE_H_6
    inner_w = TILE_W - 2 * TILE_MARGIN
    inner_h = tile_h - 2 * TILE_MARGIN

    safe_ext = Vector((
        extents.x if extents.x > 1e-6 else 1.0,
        extents.y if extents.y > 1e-6 else 1.0,
        extents.z if extents.z > 1e-6 else 1.0,
    ))

    def project(axis_i, sign, p_local):
        nx = p_local.x / safe_ext.x + 0.5
        ny = p_local.y / safe_ext.y + 0.5
        nz = p_local.z / safe_ext.z + 0.5
        if axis_i == 0:
            u, v = ny, nz
            if sign < 0:
                u = 1.0 - u
        elif axis_i == 1:
            u, v = nx, nz
            if sign > 0:
                u = 1.0 - u
        else:
            u, v = nx, ny
            if sign < 0:
                v = 1.0 - v
        return u, v

    for poly in mesh.polygons:
        n_world = (obj_rot @ poly.normal).normalized()
        face_center_world = mw @ poly.center
        face_p_local = face_center_world - center

        if dual:
            absp = (abs(face_p_local.x), abs(face_p_local.y), abs(face_p_local.z))
            axis_i = absp.index(max(absp))
            sign = +1 if face_p_local[axis_i] > 0 else -1
            side = "outer" if n_world.dot(face_p_local) > 0 else "inner"
            col, row = CUBE_ATLAS_12[(axis_i, sign, side)]
        else:
            absn = (abs(n_world.x), abs(n_world.y), abs(n_world.z))
            axis_i = absn.index(max(absn))
            sign = +1 if n_world[axis_i] > 0 else -1
            col, row = CUBE_ATLAS_6[(axis_i, sign)]

        tile_u0 = col * TILE_W + TILE_MARGIN
        tile_v0 = row * tile_h + TILE_MARGIN

        for loop_idx in poly.loop_indices:
            v_idx = mesh.loops[loop_idx].vertex_index
            v_world = mw @ Vector(mesh.vertices[v_idx].co)
            p_local = v_world - center
            u, v = project(axis_i, sign, p_local)
            uv_data[loop_idx].uv = (tile_u0 + u * inner_w, tile_v0 + v * inner_h)

    mesh.update()


# ── cylinder-atlas UV unwrap ─────────────────────────────────────────────

def _cylinder_atlas_unwrap(obj, *, axis: int):
    mesh = obj.data
    if _UV_LAYER_NAME in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[_UV_LAYER_NAME])
    uv_layer = mesh.uv_layers.new(name=_UV_LAYER_NAME)
    uv_data = uv_layer.data
    mesh.uv_layers.active_index = list(mesh.uv_layers).index(uv_layer)

    center, extents = _world_aabb(obj)
    mw = obj.matrix_world
    obj_rot = mw.to_3x3()

    tangent_a = (axis + 1) % 3
    tangent_b = (axis + 2) % 3
    axial_half = max(extents[axis] * 0.5, 1e-6)
    radius_half = max(extents[tangent_a], extents[tangent_b]) * 0.5

    lat_u0 = TILE_MARGIN
    lat_v0 = 0.5 + TILE_MARGIN
    lat_w = 1.0 - 2 * TILE_MARGIN
    lat_h = 0.5 - 2 * TILE_MARGIN

    cap_w = 0.5 - 2 * TILE_MARGIN
    cap_h = 0.5 - 2 * TILE_MARGIN
    cap_neg_u0 = TILE_MARGIN
    cap_pos_u0 = 0.5 + TILE_MARGIN
    cap_v0 = TILE_MARGIN

    axis_vec = Vector((0, 0, 0))
    axis_vec[axis] = 1.0

    for poly in mesh.polygons:
        n_world = (obj_rot @ poly.normal).normalized()
        is_endcap = abs(n_world.dot(axis_vec)) > _ENDCAP_NORMAL_THRESH

        for loop_idx in poly.loop_indices:
            v_idx = mesh.loops[loop_idx].vertex_index
            v_world = mw @ Vector(mesh.vertices[v_idx].co)
            p = v_world - center

            if is_endcap:
                t_a = p[tangent_a] / max(radius_half, 1e-6) * 0.5 + 0.5
                t_b = p[tangent_b] / max(radius_half, 1e-6) * 0.5 + 0.5
                if n_world.dot(axis_vec) < 0:
                    u = cap_neg_u0 + t_a * cap_w
                else:
                    u = cap_pos_u0 + t_a * cap_w
                v = cap_v0 + t_b * cap_h
            else:
                theta = math.atan2(p[tangent_b], p[tangent_a])
                u_norm = (theta + math.pi) / (2 * math.pi)
                v_norm = p[axis] / (2 * axial_half) + 0.5
                u = lat_u0 + u_norm * lat_w
                v = lat_v0 + v_norm * lat_h

            uv_data[loop_idx].uv = (u, v)

    mesh.update()


# ── form-shading bake (directional-lit diffuse) ─────────────────────────

def _bake_form(obj, size: int, out_png: Path):
    """Bake directional-lit diffuse shading so even convex shapes show form."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 32
    scene.cycles.bake_type = "DIFFUSE"

    img = bpy.data.images.new("form_bake", width=size, height=size, alpha=False)
    img.generated_color = (0.75, 0.75, 0.75, 1.0)

    # -- light-gray diffuse material ----------------------------------------
    mat = bpy.data.materials.new("form_bake_mat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.75, 1.0)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    nt.nodes.active = tex

    obj.data.materials.clear()
    obj.data.materials.append(mat)

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    # -- gray world for ambient fill ----------------------------------------
    world = bpy.data.worlds.new("form_world")
    world.use_nodes = True
    wnt = world.node_tree
    wnt.nodes.clear()
    bg = wnt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.3, 0.3, 0.3, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    w_out = wnt.nodes.new("ShaderNodeOutputWorld")
    wnt.links.new(bg.outputs["Background"], w_out.inputs["Surface"])
    scene.world = world

    # -- two directional sun lights (key + fill) ----------------------------
    # key: upper-right-front, energy 2.0
    key_data = bpy.data.lights.new("form_key", type="SUN")
    key_data.energy = 2.0
    key_obj = bpy.data.objects.new("form_key", key_data)
    bpy.context.collection.objects.link(key_obj)
    key_obj.rotation_euler = (math.radians(-45), math.radians(30), math.radians(30))

    # fill: lower-left-back, energy 0.8
    fill_data = bpy.data.lights.new("form_fill", type="SUN")
    fill_data.energy = 0.8
    fill_obj = bpy.data.objects.new("form_fill", fill_data)
    bpy.context.collection.objects.link(fill_obj)
    fill_obj.rotation_euler = (math.radians(45), math.radians(-30), math.radians(-150))

    # -- bake DIFFUSE (direct + indirect + color) ---------------------------
    bpy.ops.object.bake(
        type="DIFFUSE",
        pass_filter={"DIRECT", "INDIRECT", "COLOR"},
        margin=_BAKE_MARGIN_PX,
        use_clear=False,
    )

    # -- cleanup lights so next part starts fresh ---------------------------
    bpy.data.objects.remove(key_obj, do_unlink=True)
    bpy.data.objects.remove(fill_obj, do_unlink=True)
    bpy.data.lights.remove(key_data)
    bpy.data.lights.remove(fill_data)

    img.filepath_raw = str(out_png)
    img.file_format = "PNG"
    img.save()
    print(f"[bake_cond] wrote {out_png}")


def _save_uv_sidecar(obj, out_json: Path):
    mesh = obj.data
    uv_layer = mesh.uv_layers.get(_UV_LAYER_NAME)
    if uv_layer is None:
        raise RuntimeError(f"UV layer {_UV_LAYER_NAME!r} not found")
    uvs = [(uv_layer.data[i].uv[0], uv_layer.data[i].uv[1])
           for i in range(len(uv_layer.data))]
    out_json.write_text(json.dumps({
        "uv_layer_name": _UV_LAYER_NAME,
        "n_loops": len(uvs),
        "uvs": uvs,
    }))
    print(f"[bake_cond] wrote {out_json} ({len(uvs)} loops)")


# ── main ─────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()
    slug_src = Path(args["slug_src_dir"])
    parts = args["parts"]  # [{name, atlas_mode, cylinder_axis, dual}]
    size = int(args["size"])
    out_dir = Path(args["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    _load_scene(slug_src)

    for p in parts:
        name = p["name"]
        mode = p.get("atlas_mode", "cube")
        print(f"[bake_cond] processing {name} (mode={mode})")

        obj = _isolate(name)

        if mode == "cylinder":
            _cylinder_atlas_unwrap(obj, axis=int(p.get("cylinder_axis", 2)))
        else:
            dual = bool(p.get("dual", False))
            _cube_atlas_unwrap(obj, dual=dual)

        _bake_form(obj, size, out_dir / f"cond_{name}.png")
        _save_uv_sidecar(obj, out_dir / f"uv_{name}.json")
        _unisolate()

    print(f"[bake_cond] done — {len(parts)} parts")


try:
    main()
except Exception as e:
    import traceback
    print(f"[bake_cond] FAILED: {e}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
