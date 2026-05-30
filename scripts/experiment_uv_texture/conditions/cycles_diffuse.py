"""Cycles_diffuse condition: neutral Cycles render with a soft key+fill.

Light gray part, single overhead-ish sun light, soft ambient — closer to
a typical product render than the Workbench-cavity AO option. Tests
whether richer shading helps Nano Banana or just biases its color
palette.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from _blender_common import (
    place_ortho_camera,
    render_to_png,
    set_white_world_background,
)


def render_condition(
    obj,
    *,
    view: str,
    size: int,
    out_path: Path,
    cam_path: Path,
) -> None:
    _cam, sidecar = place_ortho_camera(obj, view=view, size=size)

    _attach_neutral_material(obj)
    _set_up_neutral_lighting()

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 32
    set_white_world_background()
    scene.render.film_transparent = False

    render_to_png(out_path)
    sidecar.dump(cam_path)


def _attach_neutral_material(obj) -> None:
    mat = bpy.data.materials.new(name=f"uv_tex_exp_diffuse_{obj.name}")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        nt.nodes.clear()
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    # Low albedo lets directional shading show without blowing out under
    # the world-emission ambient that produces the white background.
    bsdf.inputs["Base Color"].default_value = (0.35, 0.35, 0.35, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.65
    bsdf.inputs["Metallic"].default_value = 0.0
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _set_up_neutral_lighting() -> None:
    """Two SUN lights — a soft key from above-front and a low fill."""
    key = bpy.data.lights.new("uv_tex_exp_key", "SUN")
    key.energy = 1.2          # Standard view transform doesn't compress
    key_obj = bpy.data.objects.new("uv_tex_exp_key", key)
    bpy.context.collection.objects.link(key_obj)
    key_obj.rotation_euler = (0.6, 0.15, 0.4)

    fill = bpy.data.lights.new("uv_tex_exp_fill", "SUN")
    fill.energy = 0.4
    fill_obj = bpy.data.objects.new("uv_tex_exp_fill", fill)
    bpy.context.collection.objects.link(fill_obj)
    fill_obj.rotation_euler = (-0.3, -0.6, -0.8)
