Write `src/build.py` — the Blender entry point. This is mechanical glue: import each part's builder, run it, validate the world bbox against the design contract.

1. Use Read to load `src/design.json`. Use Glob to confirm `src/parts/*.py` exist.

2. Write `src/build.py` with this structure (adapt the `BUILDERS` dict to the actual parts in `design.json`):

```
import bpy
import json, sys
from pathlib import Path
from mathutils import Vector

HERE = Path(__file__).parent.resolve()
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

bpy.ops.wm.read_factory_settings(use_empty=True)

DESIGN = json.loads((HERE / "design.json").read_text())

# Import each part's builder. There is one Python file per part under parts/,
# each exposing a build_<lowercase-name>() that returns the bpy object.
from parts.frame import build_frame
from parts.drawer import build_drawer
from parts.handle import build_handle
# ... add one import per part in design.json

BUILDERS = {
    "Frame": build_frame,
    "Drawer": build_drawer,
    "Handle": build_handle,
    # ... add one entry per part. The key MUST match design.json parts[i].name exactly.
}

def _attach_fallback_material(obj, spec):
    """If no material was assigned by build_* / texture_*, attach a flat
    Principled BSDF from spec['color_rgba'] so the GLB ships with at
    least a baseColorFactor (otherwise viewers render default gray)."""
    if obj.data.materials:
        return
    fallback_mat = bpy.data.materials.new(name=f"{obj.name}_default")
    fallback_mat.use_nodes = True
    bsdf = fallback_mat.node_tree.nodes.get("Principled BSDF")
    rgba = tuple(spec.get("color_rgba") or (0.7, 0.7, 0.7, 1.0))
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Roughness"].default_value = 0.6
    obj.data.materials.append(fallback_mat)
    print(f"[MATERIAL_FALLBACK] {obj.name}: no explicit material; attached flat BSDF {rgba}")


def _run_texture_pass(obj, spec, builder_fn):
    """Optional: call texture_<lower>(obj) if defined in parts/<lower>.py.
    Non-fatal — log + continue on failure.

    Derive the texture function name from the builder's __name__ (strip the
    ``build_`` prefix) rather than ``spec['name'].lower()``. PascalCase names
    like ``DrawerTop`` lowercase to ``drawertop`` (no underscore), but the
    part agent writes ``texture_drawer_top`` to match its file name
    ``parts/drawer_top.py``. Using the builder's actual name keeps these in
    lockstep for any multi-word part."""
    builder_name = getattr(builder_fn, "__name__", "")
    lower_name = builder_name[len("build_"):] if builder_name.startswith("build_") else spec['name'].lower()
    tex_fn = getattr(sys.modules[builder_fn.__module__],
                     f"texture_{lower_name}", None)
    if not callable(tex_fn):
        return
    try:
        tex_fn(obj)
    except Exception as e:
        print(f"[TEXTURE_WARN] {spec['name']}: texture pass failed: {e}")


# Build each part. For parts with an `instances` list, build once and
# duplicate per-instance with the given rotation/translation. For
# single-instance parts, just call the builder once.
for spec in DESIGN["parts"]:
    builder = BUILDERS[spec["name"]]
    instances = spec.get("instances")
    if instances:
        # Template + instances: call builder once for the canonical shape,
        # then copy + transform per entry. Each instance becomes its own
        # scene object named <PascalName>_<i> (matches part_judge ids).
        canonical = builder()
        canonical.name = f"{spec['name']}_template"   # temp name; we'll delete or rename below
        for i, inst in enumerate(instances):
            obj = canonical.copy()
            obj.data = canonical.data.copy()
            bpy.context.collection.objects.link(obj)
            obj.name = f"{spec['name']}_{i}"
            # Apply per-instance transform (rotation_euler + translation, in order)
            rpy = inst.get("rotation_euler") or [0.0, 0.0, 0.0]
            txyz = inst.get("translation") or [0.0, 0.0, 0.0]
            obj.rotation_euler = tuple(rpy)
            obj.location = tuple(
                (obj.location[a] if hasattr(obj.location, "__getitem__") else 0.0) + txyz[a]
                for a in range(3)
            )
            _run_texture_pass(obj, spec, builder)
            _attach_fallback_material(obj, spec)
        # Remove the now-unused template object
        bpy.data.objects.remove(canonical, do_unlink=True)
        bpy.context.view_layer.update()
        print(f"[INSTANCES] {spec['name']}: built {len(instances)} instance(s)")
    else:
        # Single instance — original flow.
        obj = builder()
        obj.name = spec["name"]
        # Place canonical-mode parts after construction. Default ("baked") is a no-op.
        if spec.get("place_method", "baked") == "canonical":
            obj.location = tuple(spec["world_xyz"])
            if spec.get("world_rpy"):
                obj.rotation_euler = tuple(spec["world_rpy"])
            bpy.context.view_layer.update()
        _run_texture_pass(obj, spec, builder)
        _attach_fallback_material(obj, spec)

# Validate every part's world bbox against the design contract. For instance
# parts, validate ONE instance (the first) — extents are per-instance, not
# per-cluster; rotation can shift the world bbox shape, so report per-instance
# extents and don't fail on the rotated-bbox-changes case. Prints OK / WARN
# per part with mm-precision error. Does NOT raise — downstream tools should
# still render so the judge can evaluate.
TOL = 0.005


def _world_bbox(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs, ys, zs = zip(*[(v.x, v.y, v.z) for v in corners])
    bmin = Vector((min(xs), min(ys), min(zs)))
    bmax = Vector((max(xs), max(ys), max(zs)))
    return (bmax + bmin) * 0.5, bmax - bmin


print("=== bbox contract validation ===")
for spec in DESIGN["parts"]:
    name = spec["name"]
    instances = spec.get("instances")
    if instances:
        # For instance parts: validate the first instance (canonical pose) +
        # report instance count. Per-instance bbox check uses world_xyz /
        # world_extents from spec (which describe ONE canonical instance).
        first_id = f"{name}_0"
        if first_id not in bpy.data.objects:
            print(f"[MISSING] {name}: instance {first_id!r} not in scene")
            continue
        obj = bpy.data.objects[first_id]
        center, extents = _world_bbox(obj)
        exp_e = Vector(spec["world_extents"])
        err_e = (extents - exp_e).length
        # Center check is loose for instance parts — instance 0's rotation
        # may shift its center off spec["world_xyz"]. Only check extents.
        tag = "OK" if err_e < TOL * 3 else "WARN"  # 15mm tolerance for rotated bbox
        print(f"[{tag}] {name} (×{len(instances)} instances): instance_0 extents=({extents.x:.3f},{extents.y:.3f},{extents.z:.3f}) err_extents={err_e*1000:.1f}mm")
        continue
    # Single-instance part — original check.
    if name not in bpy.data.objects:
        print(f"[MISSING] {name}: builder did not produce an object")
        continue
    obj = bpy.data.objects[name]
    center, extents = _world_bbox(obj)
    exp_c = Vector(spec["world_xyz"])
    exp_e = Vector(spec["world_extents"])
    err_c = (center - exp_c).length
    err_e = (extents - exp_e).length
    tag = "OK" if (err_c < TOL and err_e < TOL) else "WARN"
    print(f"[{tag}] {name}: center=({center.x:+.3f},{center.y:+.3f},{center.z:+.3f}) extents=({extents.x:.3f},{extents.y:.3f},{extents.z:.3f}) err_center={err_c*1000:.1f}mm err_extents={err_e*1000:.1f}mm")
```

3. The `BUILDERS` dict must list every part by name in `design.json`. List every part — none can be skipped.

4. Validation prints WARN/OK but **must NOT raise** — let downstream render/export/judge proceed so the judge gets visual feedback.

5. **If validation prints any `[WARN]` tags, investigate and fix them before finishing.** WARN tags indicate geometry contract failures (parts out of position, wrong size, collisions, floating attachments). Each WARN describes the specific error and often suggests the fix (e.g. "shift Pelvis +6.0mm"). Run `build.py` again after fixing to verify all parts show `[OK]`. Ignoring WARNs leads to visible defects the judge penalizes.

6. The script will be invoked as `blender --background --python src/build.py` with cwd = workspace root. Paths inside the script resolve relative to `Path(__file__).parent`.

7. **Geometry contracts beyond bbox.** The bbox contract above only validates each part's outer AABB. It silently passes several common geometry failures the judge then complains about (e.g. "frame looks solid, not hollow"; "drawer doesn't fill the cavity"; "handle is floating in front of the drawer"). If any of the following apply to this project, read the `topos_geometry_contracts` skill and append its drop-in validation blocks after the bbox loop:

   - Any part in `design.json` has a `"cavity"` field → add the **fill-ratio** check.
   - Any part has a `"cavity"` whose face is coincident with an outer face → add the **cavity-opening** check.
   - `design.json` has 2+ parts → add the **inter-part collision** check.
   - Any part has `"cavity"` AND is the parent of a joint → add the **cavity-fit** check.
   - Any joint has `"type": "fixed"` → add the **fixed-joint attachment** check (catches floating sub-parts like handles that don't actually touch their parent).

   Each block is ~20-30 lines, prints its own `[OK]`/`[WARN]` lines, and does not raise.

Use Read + Glob to inspect, then Write to create `src/build.py`.
