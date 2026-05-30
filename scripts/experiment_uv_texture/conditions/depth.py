"""Depth condition: camera-space distance baked into per-corner vertex
colors, rendered via an Attribute → Emission shader.

Convention: brighter = closer to camera (matches ControlNet depth maps).
World background is pure white.

The depth value is computed CPU-side per mesh corner (not per shading
point), so the shader has no math to do — just plug a named float color
attribute into Emission. This avoids quirks in `ShaderNodeVectorTransform`'s
WORLD→CAMERA convention across Blender versions.
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


_ATTR_NAME = "uv_tex_exp_depth"


def render_condition(
    obj,
    *,
    view: str,
    size: int,
    out_path: Path,
    cam_path: Path,
) -> None:
    cam_obj, sidecar = place_ortho_camera(obj, view=view, size=size)
    _bake_depth_attribute(obj, cam_obj)
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


def _bake_depth_attribute(obj, cam_obj) -> None:
    mesh = obj.data
    cam_inv = cam_obj.matrix_world.inverted()
    mw = obj.matrix_world

    # Camera-space Z range across the part's 8 world-AABB corners.
    zs = []
    for corner in obj.bound_box:
        p_cam = cam_inv @ (mw @ Vector(corner))
        zs.append(p_cam.z)
    z_near = max(zs)
    z_far  = min(zs)
    denom = z_far - z_near
    if abs(denom) < 1e-6:
        denom = -1e-6

    # Drop any prior color attribute by this name and create fresh.
    if _ATTR_NAME in mesh.color_attributes:
        mesh.color_attributes.remove(mesh.color_attributes[_ATTR_NAME])
    attr = mesh.color_attributes.new(
        name=_ATTR_NAME, type="FLOAT_COLOR", domain="CORNER"
    )

    # Per-corner vertex color = grayscale depth.
    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            v_idx = mesh.loops[loop_idx].vertex_index
            v_world = mw @ Vector(mesh.vertices[v_idx].co)
            v_cam = cam_inv @ v_world
            t = (v_cam.z - z_near) / denom
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            # Gamma stretch toward midtones: thin parts viewed face-on
            # otherwise cluster near t=0; pow 0.5 broadens the band so the
            # mesh is visibly a depth map rather than near-white.
            t = t ** 0.5
            gray = 1.0 - t  # brighter = closer
            attr.data[loop_idx].color = (gray, gray, gray, 1.0)

    # Make this the rendering color attribute so shader picks it up.
    mesh.color_attributes.active_color_index = list(mesh.color_attributes).index(attr)


def _attach_attribute_emission_material(obj) -> None:
    mat = bpy.data.materials.new(name=f"uv_tex_exp_depth_{obj.name}")
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
