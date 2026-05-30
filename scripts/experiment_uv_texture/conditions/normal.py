"""Normal condition: view-space surface normals mapped to RGB on a white bg.

Convention (standard normal-map encoding):
    R = +X (right)           — 0.5 ± 0.5
    G = +Y (up)              — 0.5 ± 0.5
    B = +Z (toward camera)   — 1.0 at the front-facing surface, 0.5 at silhouette

Bakes per-corner via a CORNER-domain color attribute computed from the
mesh's polygon (face) normals transformed into camera space, so the
shader is just Attribute → Emission (no math, no version drift).
"""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Vector

from _blender_common import (
    place_ortho_camera,
    render_to_png,
    set_white_world_background,
)


_ATTR_NAME = "uv_tex_exp_normal"


def render_condition(
    obj,
    *,
    view: str,
    size: int,
    out_path: Path,
    cam_path: Path,
) -> None:
    cam_obj, sidecar = place_ortho_camera(obj, view=view, size=size)
    _bake_normal_attribute(obj, cam_obj)
    _attach_attribute_emission_material(obj)

    scene = bpy.context.scene
    available = {e.identifier for e in scene.render.bl_rna.properties["engine"].enum_items}
    for choice in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if choice in available:
            scene.render.engine = choice
            break
    else:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 1
    set_white_world_background()
    scene.render.film_transparent = False

    render_to_png(out_path)
    sidecar.dump(cam_path)


def _bake_normal_attribute(obj, cam_obj) -> None:
    mesh = obj.data
    # The 3x3 of the camera's world matrix transforms world vectors into
    # camera-local frame when we use its transpose (= inverse for rotations).
    cam_rot_inv = cam_obj.matrix_world.to_3x3().transposed()
    obj_rot = obj.matrix_world.to_3x3()

    if _ATTR_NAME in mesh.color_attributes:
        mesh.color_attributes.remove(mesh.color_attributes[_ATTR_NAME])
    attr = mesh.color_attributes.new(
        name=_ATTR_NAME, type="FLOAT_COLOR", domain="CORNER"
    )

    # Per-corner normal — face-flat, not smoothed, for clarity. If a
    # smoother result is wanted later, swap to per-vertex `v.normal`.
    for poly in mesh.polygons:
        n_world = (obj_rot @ poly.normal).normalized()
        n_cam = (cam_rot_inv @ n_world).normalized()
        # Encode to [0,1] RGB: (n + 1) / 2.
        r = n_cam.x * 0.5 + 0.5
        g = n_cam.y * 0.5 + 0.5
        b = n_cam.z * 0.5 + 0.5
        col = (r, g, b, 1.0)
        for loop_idx in poly.loop_indices:
            attr.data[loop_idx].color = col

    mesh.color_attributes.active_color_index = list(mesh.color_attributes).index(attr)


def _attach_attribute_emission_material(obj) -> None:
    mat = bpy.data.materials.new(name=f"uv_tex_exp_normal_{obj.name}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    attr_node = nt.nodes.new("ShaderNodeAttribute")
    attr_node.attribute_name = _ATTR_NAME
    attr_node.attribute_type = "GEOMETRY"

    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.0
    nt.links.new(attr_node.outputs["Color"], emit.inputs["Color"])

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])

    obj.data.materials.clear()
    obj.data.materials.append(mat)
