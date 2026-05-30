"""Inside-Blender rendering wrapper.

Invoked as:

    blender --background --python topos/tools/blender_render/wrapper.py -- \\
        --mode {single|multiview|turntable} \\
        --script <path-to-agent-build.py> \\
        --output-dir <path-to-artifacts-dir> \\
        [--n-views N] [--n-frames N] [--resolution N] \\
        [--engine workbench|eevee|cycles] \\
        [--coloring as_authored|palette] \\
        [--view-prefix view_]

Contract with the agent script:
- The agent's ``src/build.py`` is **pure geometry** — it places mesh objects
  in the scene. It must NOT add cameras, lights, render config, or call
  ``bpy.ops.render.render``. Anything it does add of those is stripped here.
- ``bpy.ops.wm.read_factory_settings(use_empty=True)`` is OK if the agent
  calls it; the wrapper also calls it before running the script.

This file MUST stay free of any ``topos`` imports — it runs in Blender's
bundled Python, not the host venv.
"""

from __future__ import annotations

import argparse
import math
import os
import runpy
import sys
from pathlib import Path

import bpy
from mathutils import Vector


# 8 octant viewpoints: (azimuth_deg, elevation_deg, label).
#
# Camera position is computed as
#   cam.x = r * cos(el) * cos(az)
#   cam.y = r * cos(el) * sin(az)
# so azimuth=0° puts the camera at +X, azimuth=270° puts it at -Y, etc.
#
# Topos's documented modeling convention (see CLAUDE.md + topos_design_articulated
# SKILL + every shipped intent.md) is "-Y is the front of the object", which
# means the camera should be at -Y to see the object's front face. That maps
# azimuth=270° → "front_low". Labels below are aligned to that convention so a
# user's "front view" actually shows the modelled front face. (Earlier topos
# versions copied infinigen's labels verbatim, in which "front_low" was azimuth
# 0° / camera at +X — which silently rendered the object's right side when the
# user expected the front.)
OCTANT_VIEWS = [
    (270, 30, "front_low"),
    (  0, 30, "right_low"),
    ( 90, 30, "back_low"),
    (180, 30, "left_low"),
    (315, 60, "front_right_high"),
    ( 45, 60, "back_right_high"),
    (135, 60, "back_left_high"),
    (225, 60, "front_left_high"),
]

# 5-color research palette for coloring=palette mode (high-contrast, vivid).
PALETTE = [
    (0.92, 0.20, 0.12, 1.0),  # coral red
    (0.04, 0.50, 0.30, 1.0),  # emerald teal
    (0.95, 0.48, 0.00, 1.0),  # amber gold
    (0.38, 0.08, 0.70, 1.0),  # deep violet
    (0.08, 0.32, 0.80, 1.0),  # steel blue
]


def _parse_args() -> argparse.Namespace:
    if "--" not in sys.argv:
        raise SystemExit("render_wrapper: no '--' separator in argv")
    raw = sys.argv[sys.argv.index("--") + 1:]
    p = argparse.ArgumentParser(prog="render_wrapper")
    p.add_argument("--mode", required=True,
                   choices=["single", "multiview", "turntable", "wireframe", "cross_section", "part"])
    # --script is the agent's whole-scene script (build.py). Required for
    # every mode EXCEPT mode=part, which can use --parts-dir + --parts to
    # construct the scene by importing each part directly — useful when
    # per-part renders are wanted BEFORE build.py exists.
    p.add_argument("--script", required=False, default=None,
                   help="agent's geometry script (build.py). Required for all modes except mode=part with --parts-dir.")
    p.add_argument("--parts-dir", default=None,
                   help="mode=part only: directory containing parts/<lower>.py files (typically <ws>/src/parts). "
                        "Replaces --script. Each named part's build_<lower>() is called directly + texture_<lower>() if defined.")
    p.add_argument("--output-dir", required=True, help="absolute output directory")
    p.add_argument("--n-views", type=int, default=8)
    p.add_argument("--n-frames", type=int, default=36)
    p.add_argument("--resolution", type=int, default=512)
    p.add_argument("--engine", default="workbench", choices=["workbench", "eevee", "cycles"])
    p.add_argument("--coloring", default="as_authored", choices=["as_authored", "palette"])
    p.add_argument("--view-prefix", default="view_")
    p.add_argument("--single-view", default="front_low",
                   help="for --mode single: which octant view label to use")
    # Cross-section knobs (mode == cross_section)
    p.add_argument("--section-axis", default="y", choices=["x", "y", "z"],
                   help="axis along which to cut (e.g. 'y' = cut through the front-back axis)")
    p.add_argument("--section-frac", type=float, default=0.5,
                   help="cut position along the axis, 0=min face, 1=max face, 0.5=center")
    # Wireframe knobs (mode == wireframe)
    p.add_argument("--wire-thickness-frac", type=float, default=0.003,
                   help="wireframe edge thickness as a fraction of the object's longest extent")
    # Part-mode knobs
    p.add_argument("--parts", default="",
                   help="comma-separated list of MESH names to render in isolation (mode=part)")
    p.add_argument("--part-n-views", type=int, default=4,
                   help="per-part view count (mode=part). 4 covers main octants; 6-8 for more coverage.")
    return p.parse_args(raw)


def _clean_factory() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _pascal_to_snake(name: str) -> str:
    """PascalCase / mixed-case → snake_case, handling acronyms.

    ``IntakeLip``    → ``intake_lip``
    ``FanBlade_0``   → ``fan_blade_0``    (existing underscores preserved)
    ``LPCompressor`` → ``lp_compressor``  (acronym followed by word)
    ``HPTurbine``    → ``hp_turbine``
    ``XMLParser``    → ``xml_parser``
    ``Nacelle``      → ``nacelle``

    Two regex passes are needed:
      1. ``(?<!^)(?=[A-Z][a-z])`` — split acronym→word boundary
         (``LPCompressor`` → ``LP_Compressor``). A second pass alone
         would collapse this to ``lpcompressor`` because there's no
         lower→upper transition inside the leading acronym.
      2. ``(?<=[a-z0-9])(?=[A-Z])`` — standard camelCase split.

    Then lowercase + collapse any doubled underscores (from existing
    underscores in input like ``FanBlade_0``).
    """
    import re as _re
    s = _re.sub(r'(?<!^)(?=[A-Z][a-z])', '_', name)
    s = _re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', s).lower()
    s = _re.sub(r'_+', '_', s)
    return s


def _build_parts_scene(parts_dir: Path, part_names: list[str]) -> None:
    """For mode=part: import each parts/<lower>.py directly and construct the
    scene from per-part builders only. Used when per-part renders run BEFORE
    the build-agent has authored src/build.py.

    Matches the convention build.py uses: import path is ``parts.<snake>`` so
    we insert parts_dir's PARENT (i.e. src/) into sys.path. After build,
    optionally calls texture_<snake>(obj) if present.

    Per CLAUDE.md rule #12 (fail loud): import / build failures are
    collected and reported as a SystemExit at the end so the orchestrator
    sees a non-zero exit (and the per-part judge doesn't score a partial
    scene as if everything worked). Texture failures stay non-fatal —
    missing texture is cosmetic, not structural.

    Buildability verification is a SEPARATE concern handled upstream by
    the ``verify_parts`` tool — by the time this runs, parts are expected
    to import + build cleanly. If they don't, this raises hard so the
    failure is loud rather than smuggled into a half-rendered scene.
    """
    src_dir = parts_dir.parent.resolve()
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    failures: list[str] = []
    for name in part_names:
        lower = _pascal_to_snake(name)
        try:
            module = __import__(f"parts.{lower}", fromlist=[f"build_{lower}"])
        except Exception as e:
            msg = f"part import failed for parts.{lower}: {e}"
            print(f"[render_wrapper] {msg}")
            failures.append(msg)
            continue
        builder = getattr(module, f"build_{lower}", None)
        if builder is None:
            msg = f"parts.{lower} has no build_{lower}()"
            print(f"[render_wrapper] {msg}")
            failures.append(msg)
            continue
        try:
            obj = builder()
        except Exception as e:
            msg = f"build_{lower}() raised: {type(e).__name__}: {e}"
            print(f"[render_wrapper] {msg}")
            failures.append(msg)
            continue
        if obj is None:
            msg = f"build_{lower}() returned None"
            print(f"[render_wrapper] {msg}")
            failures.append(msg)
            continue
        obj.name = name
        # Optional per-part texture pass (mirrors builder.md template).
        # Texture failures stay non-fatal — missing texture is cosmetic.
        tex_fn = getattr(module, f"texture_{lower}", None)
        if callable(tex_fn):
            try:
                tex_fn(obj)
            except Exception as e:
                print(f"[render_wrapper] texture_{lower}() failed (non-fatal): {e}")
        print(f"[render_wrapper] built part: {name}")
    if failures:
        raise SystemExit(
            f"render_wrapper: {len(failures)}/{len(part_names)} part(s) failed to build:\n  - "
            + "\n  - ".join(failures)
        )


def _run_agent_script(path: str) -> None:
    """Execute the agent's geometry script with its own __main__ scope.

    The script's parent directory is inserted at the front of ``sys.path`` so
    sibling files in the same project (e.g. ``src/parts/<name>.py``) can be
    imported as ``from parts.<name> import build_<name>``. This is required
    for the multi-file part-contract pattern.
    """
    script_path = Path(path)
    if not script_path.is_file():
        raise SystemExit(f"render_wrapper: agent script not found: {path}")
    script_dir = str(script_path.parent.resolve())
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    saved_argv = sys.argv[:]
    sys.argv = [path]
    try:
        runpy.run_path(path, run_name="__main__")
    finally:
        sys.argv = saved_argv


def _strip_non_geometry() -> list:
    """Remove any cameras/lights the agent script may have added; return the
    remaining list of MESH objects (which constitute the model)."""
    for obj in list(bpy.context.scene.objects):
        if obj.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.context.view_layer.update()
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def _bbox(mesh_objs) -> tuple[Vector, float]:
    """World-space bbox center and longest extent across ``mesh_objs``."""
    corners = [o.matrix_world @ Vector(c) for o in mesh_objs for c in o.bound_box]
    if not corners:
        raise SystemExit("render_wrapper: agent script produced no mesh objects")
    xs = [v.x for v in corners]
    ys = [v.y for v in corners]
    zs = [v.z for v in corners]
    center = Vector((
        (min(xs) + max(xs)) / 2,
        (min(ys) + max(ys)) / 2,
        (min(zs) + max(zs)) / 2,
    ))
    extent = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    if extent <= 0:
        extent = 1.0
    return center, extent


def _apply_palette(mesh_objs) -> None:
    """Override each mesh's ``obj.color`` with a palette slot. Larger meshes
    get earlier (and thus most distinct) palette entries."""
    ordered = sorted(mesh_objs, key=lambda o: len(o.data.vertices), reverse=True)
    for i, obj in enumerate(ordered):
        obj.color = PALETTE[i % len(PALETTE)]


def _force_base_color(obj, color_rgba) -> None:
    """Set the Principled BSDF Base Color on ``obj``'s first material to
    ``color_rgba``. If the object has no material, mint a new Principled BSDF
    material. If it has a material but no Principled BSDF (custom node tree),
    leave it untouched."""
    c = tuple(color_rgba)
    if len(c) == 3:
        c = (c[0], c[1], c[2], 1.0)

    if obj.data.materials and obj.data.materials[0] is not None:
        mat = obj.data.materials[0]
        if mat.use_nodes:
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf is not None:
                bsdf.inputs["Base Color"].default_value = c
        return

    # No material — create one
    mat = bpy.data.materials.new(name=f"{obj.name}_auto_pbr")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = c
        rough = bsdf.inputs.get("Roughness")
        if rough is not None:
            rough.default_value = 0.55
    obj.data.materials.append(mat)


def _ensure_pbr_materials(mesh_objs, *, coloring: str) -> None:
    """For EEVEE / Cycles, ensure each mesh renders in the intended color.

    Trust the agent's materials if they already exist (the build_<part>() may
    have set up a proper Principled BSDF with non-trivial PBR params). Only
    override the BSDF's Base Color from ``obj.color`` so palette mode still
    forces visual differentiation."""
    for obj in mesh_objs:
        _force_base_color(obj, tuple(obj.color))


def _configure_engine(args, mesh_objs) -> None:
    scene = bpy.context.scene
    if args.engine == "workbench":
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "OBJECT"  # honors obj.color
    elif args.engine == "eevee":
        # naming varies across Blender versions; try the modern one first
        for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = name
                break
            except TypeError:
                continue
        _add_three_point_lights(mesh_objs)
        _ensure_pbr_materials(mesh_objs, coloring=args.coloring)
        _add_world_background(0.5)
    else:  # cycles
        scene.render.engine = "CYCLES"
        try:
            scene.cycles.samples = 128
        except AttributeError:
            pass
        _add_three_point_lights(mesh_objs)
        _ensure_pbr_materials(mesh_objs, coloring=args.coloring)
        _add_world_background(0.5)


def _add_world_background(strength: float) -> None:
    """Add a neutral white world background so renders aren't black around
    the object. EEVEE/Cycles ignore obj.color but read world.background."""
    if bpy.context.scene.world is None:
        bpy.context.scene.world = bpy.data.worlds.new("World")
    world = bpy.context.scene.world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        bg.inputs[1].default_value = strength


def _add_three_point_lights(mesh_objs) -> None:
    """Three-point area lighting scaled to the object's extent.

    Empirically tuned for ~30cm objects at the framework's default camera
    distance (~1.8× extent). Values are deliberately conservative so EEVEE
    + AgX view transform doesn't blow out to white. Larger objects scale up
    linearly."""
    center, extent = _bbox(mesh_objs)
    r = extent * 1.6
    h = extent * 0.8
    # Single "unit" of light scaled by object size — bigger object, more light.
    # For a 0.3m extent, unit ~5W → key 5W / fill 1.75W / rim 2.5W (matte materials).
    unit = 5.0 * max(0.5, extent / 0.3)

    def light(name, loc, mult, sz):
        bpy.ops.object.light_add(type="AREA", location=loc)
        lt = bpy.context.object
        lt.name = name
        lt.data.energy = unit * mult
        lt.data.size = extent * sz
        lt.rotation_euler = (center - Vector(loc)).to_track_quat("-Z", "Y").to_euler()

    light("Key",  (r * 0.9, -r * 0.7, h * 1.7),  1.0, 0.9)
    light("Fill", (-r * 0.6, -r * 0.4, h * 1.0),  0.35, 1.2)
    light("Rim",  (0.0,        r * 0.9, h * 1.3),  0.5, 0.7)


def _setup_camera(center: Vector, extent: float):
    """Create a camera; caller positions it per-view."""
    bpy.ops.object.camera_add(location=(0, 0, 0))
    cam = bpy.context.object
    cam.name = "ToposEvalCam"
    cam.data.lens = 50
    cam.data.clip_end = max(100.0, extent * 25)
    bpy.context.scene.camera = cam
    return cam


def _place_camera(cam, center: Vector, r: float, az_deg: float, el_deg: float) -> None:
    az = math.radians(az_deg)
    el = math.radians(el_deg)
    cam.location = (
        center.x + r * math.cos(el) * math.cos(az),
        center.y + r * math.cos(el) * math.sin(az),
        center.z + r * math.sin(el),
    )
    cam.rotation_euler = (
        center - Vector(cam.location)
    ).to_track_quat("-Z", "Y").to_euler()


def _apply_wireframe(mesh_objs, extent: float, thickness_frac: float) -> None:
    """Replace each mesh's surface with its wireframe geometry via the
    Wireframe modifier (thickness = ``thickness_frac * extent``). After
    apply, the mesh consists of thin tubes along every edge — every
    polygon boundary becomes visible at render time without needing
    Freestyle or workbench-specific shading. Failures (non-manifold
    edges, zero-thickness faces) are skipped silently so the render still
    proceeds; the affected part renders as its original solid."""
    thickness = max(1e-5, extent * thickness_frac)
    for obj in list(mesh_objs):
        bpy.context.view_layer.objects.active = obj
        mod = obj.modifiers.new(name="_xtopos_wf", type="WIREFRAME")
        mod.thickness = thickness
        mod.use_replace = True
        try:
            bpy.ops.object.modifier_apply(modifier="_xtopos_wf")
        except RuntimeError as e:
            print(f"[render_wrapper] wireframe failed on {obj.name}: {e}; leaving solid")
            try:
                obj.modifiers.remove(obj.modifiers["_xtopos_wf"])
            except KeyError:
                pass


def _apply_cross_section(mesh_objs, axis_idx: int, frac: float) -> None:
    """Cut every mesh with a half-space cutter along ``axis_idx`` (0=X, 1=Y,
    2=Z). The cut plane is at ``min + frac * extent`` along the axis; the
    half-space ``> cut_plane`` is removed via Boolean DIFFERENCE. After
    apply, the cutter is deleted. The resulting renders show interior
    structure across the chosen section.

    Boolean apply can fail on non-manifold meshes (open shells from
    panel-join builders); on failure the cut is skipped for that part and
    a WARN is printed. This is intentional — the visualization is
    diagnostic; not getting a clean cut on one part shouldn't tank the
    whole render."""
    center, extent = _bbox(mesh_objs)
    # World-space cut coordinate along the chosen axis
    axis_centers = [center.x, center.y, center.z]
    cut_world = axis_centers[axis_idx] - extent / 2.0 + frac * extent

    # Cutter box: large pad on the two non-cut axes; on the cut axis it
    # spans from cut_world to cut_world + (extent + pad) — the +half-space
    pad = extent * 1.5
    size = [pad, pad, pad]
    size[axis_idx] = extent + pad
    loc = list(axis_centers)
    loc[axis_idx] = cut_world + size[axis_idx] / 2.0

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=tuple(loc))
    cutter = bpy.context.active_object
    cutter.scale = tuple(size)
    cutter.name = "_xtopos_cutter"
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    for obj in list(mesh_objs):
        if obj is cutter:
            continue
        bpy.context.view_layer.objects.active = obj
        mod = obj.modifiers.new(name="_xtopos_cut", type="BOOLEAN")
        mod.object = cutter
        mod.operation = "DIFFERENCE"
        try:
            bpy.ops.object.modifier_apply(modifier="_xtopos_cut")
        except RuntimeError as e:
            print(f"[render_wrapper] cross-section boolean failed on {obj.name}: {e}; skipped")
            try:
                obj.modifiers.remove(obj.modifiers["_xtopos_cut"])
            except KeyError:
                pass

    bpy.data.objects.remove(cutter, do_unlink=True)
    bpy.context.view_layer.update()


def _render_to(path: str) -> None:
    scene = bpy.context.scene
    scene.render.filepath = path
    scene.render.image_settings.file_format = "PNG"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.render.render(write_still=True)


def _render_one_part_views(part_obj, output_dir: str, n_views: int, resolution: int, view_prefix: str) -> None:
    """Render a single part in isolation: hide every other MESH, frame the
    camera tightly on just this part, render n octant views. Restores
    visibility afterward so the scene is reusable for the next part."""
    # Stash visibility of every mesh; we'll restore at the end.
    saved = {}
    for o in bpy.context.scene.objects:
        if o.type == "MESH":
            saved[o] = o.hide_render
            o.hide_render = (o.name != part_obj.name)
    try:
        # Tight bbox of just this part
        corners = [part_obj.matrix_world @ Vector(c) for c in part_obj.bound_box]
        xs = [v.x for v in corners]; ys = [v.y for v in corners]; zs = [v.z for v in corners]
        center = Vector((
            (min(xs) + max(xs)) / 2,
            (min(ys) + max(ys)) / 2,
            (min(zs) + max(zs)) / 2,
        ))
        extent = max(max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs))
        if extent <= 0:
            extent = 0.1
        # Reframe camera around this part. Distance multiplier 1.8 mirrors
        # the multiview default and works for compact furniture parts.
        cam_r = extent * 1.8
        scene = bpy.context.scene
        scene.render.resolution_x = resolution
        scene.render.resolution_y = resolution
        cam = _setup_camera(center, extent)
        # Render the first N octant views — enough to read silhouette + detail
        # from multiple angles. 4 (front_left_high, front_right_high,
        # back_left_high, back_right_high) is the sensible default.
        views = OCTANT_VIEWS[: max(1, n_views)]
        for az, el, label in views:
            _place_camera(cam, center, cam_r, az, el)
            fname = f"{view_prefix}{label}.png"
            _render_to(os.path.join(output_dir, fname))
            print(f"[render_wrapper] part {part_obj.name}: {label}")
    finally:
        for o, hide in saved.items():
            o.hide_render = hide


def main() -> int:
    args = _parse_args()

    _clean_factory()
    # Scene construction: either run build.py (any mode) OR import parts
    # directly (mode=part with --parts-dir, bypasses chicken-and-egg with
    # the build agent's not-yet-authored build.py).
    if args.mode == "part" and args.parts_dir:
        names = [n.strip() for n in (args.parts or "").split(",") if n.strip()]
        if not names:
            raise SystemExit("render_wrapper: mode=part with --parts-dir also requires --parts")
        _build_parts_scene(Path(args.parts_dir), names)
    else:
        if not args.script:
            raise SystemExit(
                f"render_wrapper: --script is required for mode={args.mode} "
                f"(only mode=part with --parts-dir can omit it)"
            )
        _run_agent_script(args.script)
    mesh_objs = _strip_non_geometry()

    center, extent = _bbox(mesh_objs)
    cam_r = extent * 1.8

    # Diagnostic geometry passes happen BEFORE palette / engine setup so the
    # post-modification meshes get the correct materials and lights.
    if args.mode == "wireframe":
        _apply_wireframe(mesh_objs, extent, args.wire_thickness_frac)
        # Re-collect mesh objects in case any were removed by failed modifiers
        mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    elif args.mode == "cross_section":
        axis_idx = {"x": 0, "y": 1, "z": 2}[args.section_axis]
        _apply_cross_section(mesh_objs, axis_idx, args.section_frac)
        mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        # Re-frame after cutting; the half-removed bbox shifts the center
        center, extent = _bbox(mesh_objs)
        cam_r = extent * 1.8

    if args.coloring == "palette":
        _apply_palette(mesh_objs)

    _configure_engine(args, mesh_objs)

    scene = bpy.context.scene
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = args.resolution

    cam = _setup_camera(center, extent)
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    if args.mode == "single":
        wanted = args.single_view
        match = next((v for v in OCTANT_VIEWS if v[2] == wanted), OCTANT_VIEWS[0])
        az, el, label = match
        _place_camera(cam, center, cam_r, az, el)
        _render_to(os.path.join(output_dir, f"{args.view_prefix}{label}.png"))
        print(f"[render_wrapper] single: {label}")
    elif args.mode == "multiview":
        views = OCTANT_VIEWS[: max(1, args.n_views)]
        for az, el, label in views:
            _place_camera(cam, center, cam_r, az, el)
            _render_to(os.path.join(output_dir, f"{args.view_prefix}{label}.png"))
            print(f"[render_wrapper] multiview: {label} (az={az} el={el})")
    elif args.mode == "turntable":
        n = max(2, args.n_frames)
        for i in range(n):
            angle = 360.0 * i / n
            _place_camera(cam, center, cam_r, angle, 30.0)
            _render_to(os.path.join(output_dir, f"frame_{i:04d}.png"))
            if i % 6 == 0:
                print(f"[render_wrapper] turntable: {i + 1}/{n}")
    elif args.mode == "part":
        # Per-part isolated render: each part written to its own subdirectory
        # under output_dir, so the judge can score per-part shape independently.
        names = [n.strip() for n in (args.parts or "").split(",") if n.strip()]
        if not names:
            raise SystemExit("render_wrapper: mode=part requires --parts <name1,name2,...>")
        present = {o.name for o in mesh_objs}
        missing = [n for n in names if n not in present]
        if missing:
            raise SystemExit(f"render_wrapper: mode=part: requested parts not in scene: {missing}; present: {sorted(present)}")
        for name in names:
            part_obj = bpy.data.objects[name]
            part_out = os.path.join(output_dir, name)
            os.makedirs(part_out, exist_ok=True)
            _render_one_part_views(
                part_obj, part_out, args.part_n_views, args.resolution, args.view_prefix,
            )
    elif args.mode in ("wireframe", "cross_section"):
        # Diagnostic mode: a smaller octant subset is enough to read the
        # interior structure. Default 4 views (front/right/back-right-high
        # + front-left-high); the n_views knob can dial up to 8.
        diag_views = OCTANT_VIEWS[: max(1, args.n_views)]
        prefix_tag = "wireframe" if args.mode == "wireframe" else f"section_{args.section_axis}{int(args.section_frac*100):02d}"
        for az, el, label in diag_views:
            _place_camera(cam, center, cam_r, az, el)
            fname = f"{args.view_prefix}{prefix_tag}_{label}.png"
            _render_to(os.path.join(output_dir, fname))
            print(f"[render_wrapper] {args.mode}: {label} (az={az} el={el})")

    print(f"[render_wrapper] done; mode={args.mode} output_dir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
