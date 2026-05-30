"""Phase 1 — UV atlas condition.

Smart-unwrap the part, then BAKE ambient occlusion into UV space via
Cycles. Output: a square PNG where each UV island contains the AO of
its corresponding 3D surface, surrounded by a white background.

Also writes a UV sidecar JSON capturing the per-loop (u, v) coordinates
so phase 3 can reproduce the exact same UV layout (without re-running
smart_project, which can drift across Blender invocations).

Args (JSON after `--`):
    slug_src_dir : Path
    part_name    : str
    size         : int (atlas resolution, square)
    cond_png_out : output PNG path
    uv_json_out  : output sidecar path
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
    world_aabb,
)
from _common import (  # noqa: E402
    CUBE_ATLAS_LAYOUT,
    CUBE_ATLAS_LAYOUT_DUAL,
    TILE_H_6,
    TILE_H_12,
    TILE_MARGIN,
    TILE_W,
    parse_blender_args,
)
from mathutils import Vector  # noqa: E402
import math  # noqa: E402


_UV_LAYER_NAME = "uv_atlas_cube"
_BAKE_MARGIN_PX = 8

_ENDCAP_NORMAL_THRESH = 0.7   # |n · axis_vec| above this = endcap face


def _cylinder_atlas_unwrap(obj, *, axis: int) -> None:
    """Cylindrical unwrap — assume the part is dominantly a cylinder along
    the given axis (0=X, 1=Y, 2=Z). Endcap-classified faces (normal mostly
    parallel to axis) go to two square caps in the bottom half of the
    canvas; everything else goes to a single 2:1 LATERAL band in the top
    half (U=theta around the axis, V=position along the axis).

    Non-cylindrical sub-parts (e.g. mounting posts on a handle) get
    suboptimal UVs in the lateral band — accepted as a known limitation;
    they typically take little surface area.
    """
    mesh = obj.data
    if _UV_LAYER_NAME in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[_UV_LAYER_NAME])
    uv_layer = mesh.uv_layers.new(name=_UV_LAYER_NAME)
    uv_data = uv_layer.data
    mesh.uv_layers.active_index = list(mesh.uv_layers).index(uv_layer)

    center, extents, _ = world_aabb(obj)
    mw = obj.matrix_world
    obj_rot = mw.to_3x3()

    tangent_a = (axis + 1) % 3
    tangent_b = (axis + 2) % 3

    axial_half = max(extents[axis] * 0.5, 1e-6)
    radius_half = max(extents[tangent_a], extents[tangent_b]) * 0.5
    if radius_half < 1e-6:
        radius_half = 1e-6

    # Lateral band rectangle in UV space: top half, full width.
    lat_u0 = TILE_MARGIN
    lat_v0 = 0.5 + TILE_MARGIN
    lat_w  = 1.0 - 2 * TILE_MARGIN
    lat_h  = 0.5 - 2 * TILE_MARGIN
    # Cap squares: bottom half, two 0.5×0.5 cells.
    cap_w = 0.5 - 2 * TILE_MARGIN
    cap_h = 0.5 - 2 * TILE_MARGIN
    cap_neg_u0 = TILE_MARGIN
    cap_pos_u0 = 0.5 + TILE_MARGIN
    cap_v0 = TILE_MARGIN

    seam_fix_count = 0

    for poly in mesh.polygons:
        n_world = (obj_rot @ poly.normal).normalized()
        n_along = abs(n_world[axis])
        is_endcap = n_along > _ENDCAP_NORMAL_THRESH

        if is_endcap:
            sign = +1 if n_world[axis] > 0 else -1
            tile_u0 = cap_pos_u0 if sign > 0 else cap_neg_u0
            tile_v0 = cap_v0
            for loop_idx in poly.loop_indices:
                v_idx = mesh.loops[loop_idx].vertex_index
                v_world = mw @ Vector(mesh.vertices[v_idx].co)
                p_local = v_world - center
                # Project onto tangent plane, normalize to [0, 1] using
                # radius_half so a unit disc spans the full cap tile.
                u_local = p_local[tangent_a] / radius_half * 0.5 + 0.5
                v_local = p_local[tangent_b] / radius_half * 0.5 + 0.5
                u_local = max(0.0, min(1.0, u_local))
                v_local = max(0.0, min(1.0, v_local))
                # For the -axis cap, mirror so the disc reads as if viewed
                # from outside (looking back into +axis direction).
                if sign < 0:
                    u_local = 1.0 - u_local
                uv_data[loop_idx].uv = (
                    tile_u0 + u_local * cap_w,
                    tile_v0 + v_local * cap_h,
                )
        else:
            # Lateral surface — compute per-loop theta + axial.
            face_us = []
            face_vs = []
            face_loops = list(poly.loop_indices)
            for loop_idx in face_loops:
                v_idx = mesh.loops[loop_idx].vertex_index
                v_world = mw @ Vector(mesh.vertices[v_idx].co)
                p_local = v_world - center
                t = (p_local[axis] / axial_half) * 0.5 + 0.5
                t = max(0.0, min(1.0, t))
                theta = math.atan2(p_local[tangent_b], p_local[tangent_a])
                u_norm = theta / (2.0 * math.pi) + 0.5
                face_us.append(u_norm)
                face_vs.append(t)
            # Seam-fix: if this face crosses the theta=±π seam, shift the
            # small u's by +1.0 so the face stays contiguous in UV space.
            if face_us and (max(face_us) - min(face_us) > 0.5):
                seam_fix_count += 1
                face_us = [u + 1.0 if u < 0.5 else u for u in face_us]
            for loop_idx, u, vv in zip(face_loops, face_us, face_vs):
                # u may now be in [0, 2]; modulo by 1 keeps it in [0,1]
                # band, with seam-crossing handled by REPEAT-style wrap.
                u_in_band = u - math.floor(u)  # [0, 1)
                uv_data[loop_idx].uv = (
                    lat_u0 + u_in_band * lat_w,
                    lat_v0 + vv * lat_h,
                )

    if seam_fix_count:
        print(f"[atlas-cond] cylinder unwrap: {seam_fix_count} seam-crossing faces")


def _cube_atlas_unwrap(obj, *, dual: bool) -> None:
    """Manual cube projection into a tile grid in UV space.

    Two modes:
      dual=False — 6-tile (3×2) layout. Each face goes to one of 6 outer
        tiles by NORMAL axis. Use for solid parts (no cavity), e.g. handle,
        knob, single panel.
      dual=True  — 12-tile (3×4) layout. Top half holds inner cavity walls,
        bottom half holds outer shell. Per-face side is decided by the sign
        of dot(face_normal, face_position_relative_to_bbox_center); axis
        (front/back/left/right/top/bottom) is decided by POSITION so a
        cavity wall on the +X side of the part is labelled "right cavity
        wall" regardless of the geometric normal direction. Use for hollow
        parts (drawer, cabinet frame).
    """
    mesh = obj.data
    if _UV_LAYER_NAME in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[_UV_LAYER_NAME])
    uv_layer = mesh.uv_layers.new(name=_UV_LAYER_NAME)
    uv_data = uv_layer.data
    mesh.uv_layers.active_index = list(mesh.uv_layers).index(uv_layer)

    center, extents, _ = world_aabb(obj)
    mw = obj.matrix_world
    obj_rot = mw.to_3x3()

    tile_h = TILE_H_12 if dual else TILE_H_6
    inner_w = TILE_W - 2 * TILE_MARGIN
    inner_h = tile_h - 2 * TILE_MARGIN

    # Per-axis projection: given a world-relative position p, pick the two
    # axes orthogonal to the dominant axis and produce (u, v) in [0, 1].
    # Sign-dependent flips keep the orientation consistent with "looking at
    # the face from OUTSIDE the bbox".
    safe_ext = Vector((
        extents.x if extents.x > 1e-6 else 1.0,
        extents.y if extents.y > 1e-6 else 1.0,
        extents.z if extents.z > 1e-6 else 1.0,
    ))

    def project(axis_i: int, sign: int, p_local: Vector) -> tuple[float, float]:
        nx = p_local.x / safe_ext.x + 0.5
        ny = p_local.y / safe_ext.y + 0.5
        nz = p_local.z / safe_ext.z + 0.5
        if axis_i == 0:  # X-aligned face → YZ plane
            u, v = ny, nz
            if sign < 0:  # -X face: flip Y so right-of-image points to +Y
                u = 1.0 - u
        elif axis_i == 1:  # Y-aligned face → XZ plane
            u, v = nx, nz
            if sign > 0:  # +Y (back): viewer sees X reversed
                u = 1.0 - u
        else:  # Z-aligned face → XY plane
            u, v = nx, ny
            if sign < 0:  # -Z (bottom looking up): flip Y
                v = 1.0 - v
        return u, v

    for poly in mesh.polygons:
        n_world = (obj_rot @ poly.normal).normalized()
        face_center_world = mw @ poly.center
        face_p_local = face_center_world - center

        if dual:
            # Position-based axis (where is the face physically?) +
            # normal·position sign for outer/inner side.
            absp = (abs(face_p_local.x),
                    abs(face_p_local.y),
                    abs(face_p_local.z))
            axis_i = absp.index(max(absp))
            sign = +1 if face_p_local[axis_i] > 0 else -1
            side = "outer" if n_world.dot(face_p_local) > 0 else "inner"
            col, row, _, _ = CUBE_ATLAS_LAYOUT_DUAL[(axis_i, sign, side)]
        else:
            # Normal-based axis. Solid-part mode.
            absn = (abs(n_world.x), abs(n_world.y), abs(n_world.z))
            axis_i = absn.index(max(absn))
            sign = +1 if n_world[axis_i] > 0 else -1
            col, row, _, _ = CUBE_ATLAS_LAYOUT[(axis_i, sign)]

        tile_u0 = col * TILE_W + TILE_MARGIN
        tile_v0 = row * tile_h + TILE_MARGIN

        for loop_idx in poly.loop_indices:
            v_idx = mesh.loops[loop_idx].vertex_index
            v_world = mw @ Vector(mesh.vertices[v_idx].co)
            p_local = v_world - center
            u, v = project(axis_i, sign, p_local)
            uv_data[loop_idx].uv = (
                tile_u0 + u * inner_w,
                tile_v0 + v * inner_h,
            )


def _attach_bake_material(obj, target_image) -> None:
    """Material whose only purpose is to host the bake-target image node."""
    mat = bpy.data.materials.new(name="uv_atlas_bake_mat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.75, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.7
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    # The TexImage node must be selected + active when bake runs — Cycles
    # writes into whichever image-texture node is "active" in the material.
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = target_image
    tex.select = True
    nt.nodes.active = tex

    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _bake_ao_to_image(obj, target_image) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 64
    scene.cycles.bake_type = "AO"

    # AO sampling distance — small relative to part size keeps the bake
    # local to creases and corners (where the form is most legible).
    if scene.world is None:
        scene.world = bpy.data.worlds.new("uv_atlas_world")
    scene.world.light_settings.distance = 0.05

    # Make sure the target UV layer is active for rendering / baking.
    obj.data.uv_layers.active_index = list(obj.data.uv_layers).index(
        obj.data.uv_layers[_UV_LAYER_NAME])

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # use_clear=False preserves the white pre-fill outside UV islands.
    bpy.ops.object.bake(
        type="AO",
        margin=_BAKE_MARGIN_PX,
        use_clear=False,
    )


def _save_uv_sidecar(obj, uv_json_out: Path) -> None:
    mesh = obj.data
    layer = mesh.uv_layers[_UV_LAYER_NAME].data
    uvs = [(layer[i].uv.x, layer[i].uv.y) for i in range(len(layer))]
    uv_json_out.parent.mkdir(parents=True, exist_ok=True)
    uv_json_out.write_text(json.dumps({
        "uv_layer_name": _UV_LAYER_NAME,
        "n_loops": len(uvs),
        "uvs": uvs,
    }))


def main() -> None:
    args = parse_blender_args(sys.argv)
    slug_src_dir = Path(args["slug_src_dir"])
    part_name    = str(args["part_name"])
    size         = int(args["size"])
    cond_png_out = Path(args["cond_png_out"])
    uv_json_out  = Path(args["uv_json_out"])

    print(f"[atlas-cond] slug_src={slug_src_dir} part={part_name} size={size}")
    load_scene_from_slug(slug_src_dir)
    obj = isolate_part(part_name)

    cylinder_axis = args.get("cylinder_axis", None)
    dual = bool(args.get("dual_atlas", False))
    if cylinder_axis is not None:
        cylinder_axis = int(cylinder_axis)
        print(f"[atlas-cond] mode=cylinder axis={cylinder_axis} (0=X,1=Y,2=Z)")
        _cylinder_atlas_unwrap(obj, axis=cylinder_axis)
    else:
        print(f"[atlas-cond] mode=cube dual_atlas={dual}")
        _cube_atlas_unwrap(obj, dual=dual)

    # Create a target image and pre-fill with WHITE so anything outside the
    # baked UV islands stays white. (`use_clear=True` would overwrite to
    # black; we set False below and rely on this pre-fill.)
    target_image = bpy.data.images.new(
        name="uv_atlas_target", width=size, height=size, alpha=True,
    )
    # foreach_set on .pixels is the fastest way to fill all pixels at once.
    target_image.pixels.foreach_set([1.0] * (size * size * 4))

    _attach_bake_material(obj, target_image)
    _bake_ao_to_image(obj, target_image)

    cond_png_out.parent.mkdir(parents=True, exist_ok=True)
    target_image.filepath_raw = str(cond_png_out)
    target_image.file_format = "PNG"
    target_image.save()
    print(f"[atlas-cond] wrote {cond_png_out}")

    _save_uv_sidecar(obj, uv_json_out)
    print(f"[atlas-cond] wrote {uv_json_out}")


try:
    main()
except Exception as e:
    import traceback
    print(f"[atlas-cond] FAILED: {e}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
