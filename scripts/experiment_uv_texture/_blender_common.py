"""Blender-side helpers shared across phase-1 (condition render) and
phase-3 (apply + re-render). Imports `bpy` and `mathutils`, so this module
must only be imported inside a Blender subprocess.
"""

from __future__ import annotations

import math
import runpy
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

# Allow `from _common import ...` from this dir when launched by Blender.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _common import VIEW_DIRECTIONS, CamSidecar  # noqa: E402


def load_scene_from_slug(slug_src_dir: Path) -> dict:
    """Boot a clean Blender scene and exec the slug's build.py.

    Returns the build.py globals (so callers can inspect DESIGN if needed).
    Print streaming from build.py is preserved.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build_py = slug_src_dir / "build.py"
    if not build_py.is_file():
        raise FileNotFoundError(f"build.py not found at {build_py}")
    # build.py inserts its own dir to sys.path and imports parts/*. runpy
    # gives it a __file__ so its `Path(__file__).parent` resolves correctly.
    return runpy.run_path(str(build_py), run_name="__main__")


def isolate_part(part_name: str) -> bpy.types.Object:
    """Hide every renderable object except `part_name`. Returns the target."""
    target = bpy.data.objects.get(part_name)
    if target is None:
        avail = sorted(o.name for o in bpy.data.objects if o.type == "MESH")
        raise KeyError(
            f"part {part_name!r} not in scene. Mesh objects present: {avail}"
        )
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        obj.hide_render = (obj.name != part_name)
        obj.hide_viewport = (obj.name != part_name)
    return target


def world_aabb(obj: bpy.types.Object) -> tuple[Vector, Vector, Vector]:
    """Return (center, extents, half_extents_diag) of obj's world AABB.

    half_extents_diag is the magnitude of the half-extents vector — handy
    for camera distance / framing computations.
    """
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs, ys, zs = zip(*[(v.x, v.y, v.z) for v in corners])
    lo = Vector((min(xs), min(ys), min(zs)))
    hi = Vector((max(xs), max(ys), max(zs)))
    center = (lo + hi) * 0.5
    extents = hi - lo
    return center, extents, Vector(extents) * 0.5


def place_ortho_camera(
    obj: bpy.types.Object,
    *,
    view: str,
    size: int,
    margin: float = 1.10,
) -> tuple[bpy.types.Object, CamSidecar]:
    """Add an orthographic camera framed on `obj` from named `view`.

    Returns the camera object + a CamSidecar capturing the exact transform
    so phase 3 can reproduce it bit-for-bit.
    """
    if view not in VIEW_DIRECTIONS:
        raise KeyError(f"unknown view {view!r}; choices: {sorted(VIEW_DIRECTIONS)}")

    center, extents, _ = world_aabb(obj)
    direction = Vector(VIEW_DIRECTIONS[view]).normalized()

    # Camera distance: place camera well outside the AABB along `direction`,
    # then extend the clip range generously. With an ortho camera the
    # absolute distance does not affect framing — only ortho_scale does —
    # but we still want clip_start/end to bracket the part.
    half_diag = extents.length * 0.5
    cam_distance = max(half_diag * 4.0, 1.0)
    cam_location = center + direction * cam_distance

    # Camera looks at `center`. Compute rotation from -Z forward (Blender
    # camera default) to (center - cam_location) = -direction.
    forward = (center - cam_location).normalized()
    # Build a rotation that maps default camera forward (-Z) → `forward`
    # and default camera up (+Y) → world up, with a sensible fallback when
    # `forward` is co-linear with world up (top / bottom views).
    world_up = Vector((0.0, 0.0, 1.0))
    if abs(forward.dot(world_up)) > 0.999:
        # Looking straight up or down: pick +Y as up to keep handedness.
        world_up = Vector((0.0, 1.0, 0.0))
    right = forward.cross(world_up).normalized()
    up    = right.cross(forward).normalized()
    # Camera basis: X=right, Y=up, Z=-forward (matches Blender convention).
    rot_3x3 = Matrix((right, up, -forward)).transposed()
    cam_matrix = Matrix.Translation(cam_location) @ rot_3x3.to_4x4()

    # Pick an ortho_scale that frames the AABB on the two screen axes for
    # this view direction. Screen-x = right · world_axis; screen-y = up · world_axis.
    e = extents
    screen_x = abs(right.x) * e.x + abs(right.y) * e.y + abs(right.z) * e.z
    screen_y = abs(up.x)    * e.x + abs(up.y)    * e.y + abs(up.z)    * e.z
    ortho_scale = max(screen_x, screen_y) * margin

    cam_data = bpy.data.cameras.new("uv_tex_exp_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho_scale
    cam_data.clip_start = 0.001
    cam_data.clip_end = cam_distance * 4.0 + 10.0

    cam_obj = bpy.data.objects.new("uv_tex_exp_cam", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.matrix_world = cam_matrix
    bpy.context.scene.camera = cam_obj

    # Square render config.
    scene = bpy.context.scene
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100

    sidecar = CamSidecar(
        matrix_world_rows=[list(row) for row in cam_matrix.row],
        ortho_scale=ortho_scale,
        clip_start=cam_data.clip_start,
        clip_end=cam_data.clip_end,
        resolution=size,
        view=view,
    )
    return cam_obj, sidecar


def restore_camera_from_sidecar(sidecar: CamSidecar) -> bpy.types.Object:
    """Recreate the phase-1 camera with the exact same transform."""
    cam_data = bpy.data.cameras.new("uv_tex_exp_cam_restored")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = sidecar.ortho_scale
    cam_data.clip_start = sidecar.clip_start
    cam_data.clip_end = sidecar.clip_end
    cam_obj = bpy.data.objects.new("uv_tex_exp_cam_restored", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.matrix_world = Matrix(sidecar.matrix_world_rows)
    bpy.context.scene.camera = cam_obj
    scene = bpy.context.scene
    scene.render.resolution_x = sidecar.resolution
    scene.render.resolution_y = sidecar.resolution
    scene.render.resolution_percentage = 100
    return cam_obj


def render_to_png(out_path: Path) -> None:
    """Render the active scene with the current settings to out_path (PNG).

    Forces view_transform="Standard" so 1.0 linear → 1.0 sRGB (Filmic, the
    default in Blender 4+, compresses 1.0 → ~0.78 which makes "pure white"
    backgrounds and high-key emission shaders read as gray).
    """
    scene = bpy.context.scene
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = str(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)


def set_white_world_background() -> None:
    """Set the world background to pure white (used by Cycles/EEVEE)."""
    world = bpy.context.scene.world or bpy.data.worlds.new("uv_tex_exp_world")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
