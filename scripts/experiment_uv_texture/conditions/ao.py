"""AO condition: light gray part with Workbench cavity shading on white bg.

Gives Gemini form cues (concavity / edges) without dominating the color
budget — the object reads as a neutral 3D form rather than a flat
silhouette. Useful when the prompted texture has complex relief.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from _blender_common import (
    place_ortho_camera,
    render_to_png,
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

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"

    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "SINGLE"
    shading.single_color = (0.78, 0.78, 0.78)
    shading.show_cavity = True
    shading.cavity_type = "WORLD"
    shading.cavity_ridge_factor = 1.2
    shading.cavity_valley_factor = 1.2
    shading.show_object_outline = False
    shading.show_shadows = False

    shading.background_type = "VIEWPORT"
    shading.background_color = (1.0, 1.0, 1.0)
    scene.render.film_transparent = False

    render_to_png(out_path)
    sidecar.dump(cam_path)
