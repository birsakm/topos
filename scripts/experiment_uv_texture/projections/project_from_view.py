"""bpy.ops.uv.project_from_view projection.

Tries the Blender operator first (via a synthesized VIEW_3D context). The
op is unreliable in --background mode (no real screen / region), so we
fall back to the analytical implementation and log a note when that
happens. The analytical fallback should produce the identical UVs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _common import CamSidecar               # noqa: E402
from projections import analytical_view      # noqa: E402


def apply_projection(
    obj,
    *,
    image_path: Path,
    cam_path: Path,
    view: str,
) -> None:
    sidecar = CamSidecar.load(cam_path)
    _restore_camera(sidecar)

    used_op = False
    try:
        used_op = _try_project_from_view_op(obj)
    except Exception as e:
        print(f"[project_from_view] op raised, falling back to analytical: {e}")

    if not used_op:
        print("[project_from_view] using analytical fallback (headless context)")
        analytical_view._write_uvs_analytical(obj, sidecar)
    else:
        print("[project_from_view] used bpy.ops.uv.project_from_view")

    analytical_view._bind_image_material(obj, image_path)


def _restore_camera(sidecar: CamSidecar) -> None:
    from mathutils import Matrix
    cam_data = bpy.data.cameras.new("uv_tex_exp_cam_pfv")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = sidecar.ortho_scale
    cam_data.clip_start = sidecar.clip_start
    cam_data.clip_end = sidecar.clip_end
    cam_obj = bpy.data.objects.new("uv_tex_exp_cam_pfv", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.matrix_world = Matrix(sidecar.matrix_world_rows)
    bpy.context.scene.camera = cam_obj


def _find_view3d_area():
    """Find a VIEW_3D area + its WINDOW region.

    In `--background` mode `window_manager.windows` is empty but the
    factory `bpy.data.screens` still contains the default 'Layout' screen
    with a VIEW_3D area, so we scan all screens, not just attached
    windows. Returns (window_or_None, area, region) or (None, None, None).
    """
    # Prefer an attached window if there is one (GUI mode).
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        return win, area, region
    # Background mode: walk data screens directly.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        return None, area, region
    return None, None, None


def _try_project_from_view_op(obj) -> bool:
    """Attempt bpy.ops.uv.project_from_view. Returns True iff the op ran."""
    window, area_3d, region_3d = _find_view3d_area()
    if area_3d is None or region_3d is None:
        print("[project_from_view] no VIEW_3D area found in scene screens")
        return False

    # Ensure there's a UV layer to write into.
    if not obj.data.uv_layers:
        obj.data.uv_layers.new(name="uv_view_proj")

    # Point the area's 3D view at the active camera so the op's
    # "camera_bounds=True" reads from our scene.camera.
    space = next((s for s in area_3d.spaces if s.type == "VIEW_3D"), None)
    if space is not None:
        space.region_3d.view_perspective = "CAMERA"

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    override_kwargs = {"area": area_3d, "region": region_3d}
    if window is not None:
        override_kwargs["window"] = window
        override_kwargs["screen"] = window.screen
    try:
        with bpy.context.temp_override(**override_kwargs):
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.project_from_view(
                camera_bounds=True,
                correct_aspect=False,
                scale_to_bounds=False,
            )
            bpy.ops.object.mode_set(mode="OBJECT")
        return True
    except RuntimeError as e:
        print(f"[project_from_view] op raised under override: {e}")
        return False
