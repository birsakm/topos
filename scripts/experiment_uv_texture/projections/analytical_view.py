"""Analytical view-projection: compute UVs directly from the camera matrix.

For an orthographic camera looking down its local -Z axis with square
aspect ratio, the UV of a world-space vertex p is:

    p_cam = M_cam_inv @ p          # world → camera
    u = (p_cam.x + ortho_scale/2) / ortho_scale
    v = (p_cam.y + ortho_scale/2) / ortho_scale

This is the "ground truth" projection — no bpy.ops, no UI context, just
math. project_from_view.py uses the bpy operator and falls back to this
when running headless.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _common import CamSidecar  # noqa: E402


def apply_projection(
    obj,
    *,
    image_path: Path,
    cam_path: Path,
    view: str,
) -> None:
    sidecar = CamSidecar.load(cam_path)
    _write_uvs_analytical(obj, sidecar)
    _bind_image_material(obj, image_path)


def _write_uvs_analytical(obj, sidecar: CamSidecar) -> None:
    mesh = obj.data
    mesh_world = obj.matrix_world
    cam_mw = Matrix(sidecar.matrix_world_rows)
    cam_inv = cam_mw.inverted()
    ortho = sidecar.ortho_scale
    half = ortho * 0.5

    # Ensure there is an active UV layer to write into.
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="uv_view_proj")
    uv_layer = mesh.uv_layers.active.data

    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            loop = mesh.loops[loop_index]
            v_world = mesh_world @ Vector(mesh.vertices[loop.vertex_index].co)
            v_cam = cam_inv @ v_world
            # Map x,y in [-half, +half] → u,v in [0, 1]. Points outside the
            # ortho frustum get UVs outside [0,1] which the ImageTexture
            # will clip (see _bind_image_material).
            u = (v_cam.x + half) / ortho
            v = (v_cam.y + half) / ortho
            uv_layer[loop_index].uv = (u, v)

    mesh.update()


def _bind_image_material(obj, image_path: Path) -> None:
    """Attach a Principled BSDF with the generated PNG as base color.

    Image extension set to CLIP so vertices outside the view frustum (e.g.
    backside faces) take the edge / black rather than tiling.
    """
    mat = bpy.data.materials.new(name=f"uv_tex_exp_{obj.name}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    tex_node = nt.nodes.new("ShaderNodeTexImage")
    img = bpy.data.images.load(str(image_path), check_existing=False)
    tex_node.image = img
    tex_node.extension = "CLIP"
    tex_node.interpolation = "Linear"

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.65
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    obj.data.materials.clear()
    obj.data.materials.append(mat)
