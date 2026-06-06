"""Blender-run: import a GLB, set up a lit turntable, render N transparent frames.

    blender --background --python scripts/glb_turntable.py -- \
        --glb <object.glb> --out <frames_dir> [--frames 30] [--res 960] [--engine cycles|eevee]

Standalone (no topos imports) — runs in Blender's bundled Python.
"""
import argparse
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument("--glb", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--frames", type=int, default=30)
ap.add_argument("--res", type=int, default=960)
ap.add_argument("--engine", default="cycles", choices=["cycles", "eevee"])
ap.add_argument("--elev", type=float, default=10.0, help="camera elevation degrees")
args = ap.parse_args(argv)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=args.glb)
scene = bpy.context.scene
meshes = [o for o in scene.objects if o.type == "MESH"]
if not meshes:
    print("NO MESHES IN GLB"); sys.exit(1)

# world bbox
mn = Vector((1e18, 1e18, 1e18)); mx = Vector((-1e18, -1e18, -1e18))
for o in meshes:
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        for i in range(3):
            mn[i] = min(mn[i], w[i]); mx[i] = max(mx[i], w[i])
center = (mn + mx) / 2.0
size = mx - mn
m = max(size.x, size.y, size.z)

# turntable target at center; camera + lights track it; rotating target spins them
target = bpy.data.objects.new("Target", None)
scene.collection.objects.link(target)
target.location = center

def _track(obj):
    c = obj.constraints.new("TRACK_TO")
    c.target = target; c.track_axis = "TRACK_NEGATIVE_Z"; c.up_axis = "UP_Y"

cam_data = bpy.data.cameras.new("Cam"); cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam); scene.camera = cam
cam_data.lens = 70
dist = m * 2.4
elev = math.radians(args.elev)
cam.location = center + Vector((0.0, -dist * math.cos(elev), dist * math.sin(elev)))
_track(cam); cam.parent = target

# lighting: soft world + key/fill/rim area lights, scaled to object size
world = bpy.data.worlds.new("W"); scene.world = world; world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (1, 1, 1, 1); bg.inputs[1].default_value = 0.5

def add_area(off, energy, sz):
    ld = bpy.data.lights.new("L", "AREA"); ld.energy = energy; ld.size = sz
    lo = bpy.data.objects.new("L", ld); scene.collection.objects.link(lo)
    lo.location = center + Vector(off); _track(lo); lo.parent = target

e = 90.0 * m * m
add_area((-m * 1.2, -m * 1.2, m * 1.6), e * 1.0, m)       # key
add_area((m * 1.4, -m * 0.6, m * 0.8), e * 0.45, m * 1.2)  # fill
add_area((0.0, m * 1.4, m * 1.4), e * 0.5, m)              # rim/back

# render config
scene.render.resolution_x = args.res; scene.render.resolution_y = args.res
scene.render.film_transparent = True
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
# Standard (not Filmic/AgX) so the authored per-part colors stay vivid and
# distinct instead of being desaturated/flattened by a film tone-map.
try:
    scene.view_settings.view_transform = "Standard"
except Exception:
    pass

if args.engine == "cycles":
    scene.render.engine = "CYCLES"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for dt in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = dt; prefs.get_devices()
                if any(d.type != "CPU" for d in prefs.devices):
                    for d in prefs.devices:
                        d.use = True
                    scene.cycles.device = "GPU"
                    print(f"cycles GPU via {dt}")
                    break
            except Exception:
                continue
    except Exception as ex:
        print("GPU setup skipped:", ex)
    scene.cycles.samples = 48
    scene.cycles.use_denoising = True
else:
    for n in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = n; break
        except TypeError:
            continue
    try:
        scene.eevee.taa_render_samples = 128
    except Exception:
        pass

os.makedirs(args.out, exist_ok=True)
N = args.frames
for i in range(N):
    target.rotation_euler[2] = math.radians(360.0 * i / N)
    scene.render.filepath = os.path.join(args.out, f"frame_{i:04d}.png")
    bpy.ops.render.render(write_still=True)
    print(f"[glb_turntable] {i + 1}/{N}", flush=True)
print("[glb_turntable] done")
