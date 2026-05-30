"""Blender-side UV unwrap + condition bake — phase 1.

Invoked via ``blender --background --python _bake_cond.py -- <json>``.

For each part listed in args, this script:
1. Loads the scene via build.py
2. Isolates the part (hides others)
3. UV-unwraps with Blender's Smart UV Project (angle-based, island-packed
   into [0,1] — the Blender-native analog of xatlas; far better than a
   hand-rolled cube/cylinder projection for arbitrary geometry)
4. Bakes directional-lit diffuse shading into a light-gray-base image —
   the *condition image* the image model paints inside
5. Saves cond_<part>.png + uv_<part>.json sidecar (per-loop UVs)
6. Un-isolates for the next part

Phase 3 (``_apply_all.py``) restores the per-loop UVs from the sidecar onto
the same mesh and binds the painted texture. Smart UV Project only writes
UVs (it never changes mesh topology), so the per-loop sidecar stays valid
across the two Blender launches.

Self-contained: no ``topos`` imports. Runs inside Blender's bundled Python.
"""

from __future__ import annotations

import json
import math
import runpy
import sys
from pathlib import Path

import bpy

# ── constants (embedded, no external imports) ────────────────────────────

_UV_LAYER_NAME = "uv_atlas"
_BAKE_MARGIN_PX = 8

# Smart UV Project params. angle_limit is in radians (Blender 2.8+);
# 1.15 rad ≈ 66°, the operator's own default seam angle. island_margin
# keeps a little gutter between islands so the bake/paint of one island
# doesn't bleed into its neighbour.
_SMART_ANGLE_LIMIT = 1.15
_SMART_ISLAND_MARGIN = 0.02


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


# ── Smart UV Project unwrap ──────────────────────────────────────────────

def _smart_uv_unwrap(obj):
    """Angle-based unwrap with island packing into [0,1], written to the
    ``uv_atlas`` layer. Mesh topology is untouched, so the per-loop UV
    order matches what phase 3 restores."""
    mesh = obj.data
    if _UV_LAYER_NAME in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[_UV_LAYER_NAME])
    uv_layer = mesh.uv_layers.new(name=_UV_LAYER_NAME)
    mesh.uv_layers.active_index = list(mesh.uv_layers).index(uv_layer)

    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(
        angle_limit=_SMART_ANGLE_LIMIT,
        island_margin=_SMART_ISLAND_MARGIN,
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.select_set(False)
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
    parts = args["parts"]  # [{name, ...}] — atlas_mode/dual/cylinder_axis ignored (Smart UV Project)
    size = int(args["size"])
    out_dir = Path(args["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    _load_scene(slug_src)

    for p in parts:
        name = p["name"]
        print(f"[bake_cond] processing {name}")

        obj = _isolate(name)
        _smart_uv_unwrap(obj)
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
