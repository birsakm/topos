"""Silhouette condition: pure black part on a pure white background.

Workbench engine + single-color flat shading, no lighting, no cavity. The
cheapest condition image; tells Gemini "this is the region; everything
else is white". Nano Banana has to invent all form cues itself.
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
    shading.light = "FLAT"
    shading.color_type = "SINGLE"
    shading.single_color = (0.0, 0.0, 0.0)
    shading.show_cavity = False
    shading.show_object_outline = False
    shading.show_shadows = False
    shading.show_xray = False

    # White viewport background.
    shading.background_type = "VIEWPORT"
    shading.background_color = (1.0, 1.0, 1.0)
    scene.render.film_transparent = False

    render_to_png(out_path)
    sidecar.dump(cam_path)
