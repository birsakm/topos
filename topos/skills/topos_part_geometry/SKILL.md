---
name: topos_part_geometry
description: bbox-contract pattern + Blender geometry strategies for writing build_<name>() part functions
when_to_use: Any AgentTask that implements src/parts/<name>.py for an articulated or rigid project
provides:
  - bbox contract semantics (±5mm world center + extents)
  - 5-panel-join strategy for hollow bodies (with the transform_apply trick)
  - bmesh-boolean strategy for cut-out cavities
  - bevel modifier for soft edges
  - recessed-inset technique for drawer-style faces
  - sized-primitive arithmetic (cube_add size vs scale)
related_tools:
  - blender_run
related_skills:
  - topos_design_articulated
  - topos_joints_creator
  - topos_furniture_hardware     # detailed handle/knob/hinge patterns; load this in addition for hardware parts
---

# Topos: Part Geometry

This skill teaches you how to write a single part's `build_<part>()` function correctly. Every part agent should consult this before writing geometry code.

## The bbox contract (always in force)

After your `build_<name>()` returns, the framework computes the produced object's **world bbox** (axis-aligned bounding box, applying all transforms) and compares it to the part's spec in `design.json`. The build will print `[OK]` or `[WARN]` per part. You must satisfy:

- `obj` world bbox center within **5mm** of `spec["world_xyz"]`
- `obj` world bbox extents (full width / depth / height along X / Y / Z) within **5mm** of `spec["world_extents"]`
- `obj.name` must equal the spec's `name` (PascalCase) exactly

The framework prints WARN but does NOT raise — your render will still run. But the judge sees bbox mismatches as visual defects (drawers that don't fit, oversize handles, etc.), so meeting the contract matters.

## Strategies

Pick the strategy that matches the part's `geometry_strategy` field (advisory) or invent your own — what matters is satisfying the contract.

### Strategy: solid primitive

For solid blocks (rare — most parts deserve detail):

```python
import bpy

def build_<lower>():
    spec = ...  # read from src/design.json
    cx, cy, cz = spec["world_xyz"]
    sx, sy, sz = spec["world_extents"]
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(cx, cy, cz))
    obj = bpy.context.active_object
    obj.scale = (sx, sy, sz)              # NOTE: with size=1, scale = FULL extents (not half!)
    bpy.ops.object.transform_apply(scale=True)
    obj.name = "<PascalName>"
    obj.color = tuple(spec["color_rgba"])
    return obj
```

**Critical arithmetic bug to avoid:** `bpy.ops.mesh.primitive_cube_add(size=1.0)` creates a unit cube spanning ±0.5. Then `obj.scale = (sx, sy, sz)` gives final extents `(sx, sy, sz)` — **not** `(2*sx, 2*sy, 2*sz)`. Using `obj.scale = (sx/2, sy/2, sz/2)` gives HALF the intended size — a common mistake that the bbox validator catches as `err_extents > 0`. With `size=2.0` (default), the cube is ±1, and `obj.scale = (sx/2, sy/2, sz/2)` is correct.

### Strategy: 5-panel-join (hollow body, e.g. cabinet frame, drawer body)

Create 5 wall panels (bottom / top / back / left / right), then join into one mesh:

```python
def build_<lower>():
    spec = ...
    t = spec["wall_thickness"]
    cx, cy, cz = spec["world_xyz"]
    sx, sy, sz = spec["world_extents"]
    panels = [
        ("bottom", (cx, cy, cz - sz/2 + t/2), (sx, sy, t)),
        ("top",    (cx, cy, cz + sz/2 - t/2), (sx, sy, t)),
        ("back",   (cx, cy + sy/2 - t/2, cz), (sx, t, sz)),
        ("left",   (cx - sx/2 + t/2, cy, cz), (t, sy, sz)),
        ("right",  (cx + sx/2 - t/2, cy, cz), (t, sy, sz)),
        # for an open-top drawer, omit "top"
    ]
    created = []
    for name, loc, ext in panels:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
        obj = bpy.context.active_object
        obj.name = name
        obj.scale = ext
        # CRITICAL: apply scale BEFORE join to avoid stretched local-frame verts
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        created.append(obj)
    bpy.ops.object.select_all(action='DESELECT')
    for o in created: o.select_set(True)
    bpy.context.view_layer.objects.active = created[0]
    bpy.ops.object.join()
    obj = bpy.context.active_object
    obj.name = "<PascalName>"
    obj.color = tuple(spec["color_rgba"])
    return obj
```

**Critical bug to avoid (the z=39 trap):** if you skip `transform_apply(scale=True)` before joining, and the active object has a tiny axis scale (like `t=0.0075`), the other panels' world coordinates get inverse-transformed into the active's local frame. A vertex at world z=0.3 becomes z=40 in local. The mesh data is then bizarrely stretched (only correct after the surviving node scale is applied). Per-part export to OBJ/GLB then writes the stretched vertices and viewers render a 38-meter-tall part. **Always `transform_apply(scale=True)` on each panel before joining.**

**Critical bug to avoid (the floating-handle trap):** if your part has a
`fixed` joint to another part in design.json (e.g. handle → drawer), the
spec's `world_xyz` + `world_extents` only constrain the OUTER AABB. The bbox
contract is satisfied even if every vertex is clustered at one end of the
bbox leaving "empty air" between this part and the parent it's supposed to
attach to. Concretely: if design says Drawer front at y=-0.28 and Handle
back at y=-0.28 (touching), your build_handle() MUST actually emit vertices
that reach y=-0.28 — not stop short at y=-0.25 because the grip cylinder
sits at the front of the handle bbox. **For fixed-joint child parts, audit
the mesh you produce so it has at least a few vertices within ~2mm of the
parent's contact surface.** The `topos_geometry_contracts` skill's
fixed-joint attachment check (Check 4) catches the failure post-build, but
fix it at write-time: place posts/stems/mounting features so they reach the
attachment plane, even if that means a separate sub-mesh for the contact.

### Strategy: bmesh-boolean (alternative for cavity)

When you want a true CSG-style cavity:

```python
import bpy, bmesh
def build_<lower>():
    spec = ...
    # 1. create solid outer cube
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=spec["world_xyz"])
    outer = bpy.context.active_object
    outer.scale = spec["world_extents"]
    bpy.ops.object.transform_apply(scale=True)
    # 2. create inner "cavity" cube — IMPORTANT: along the OPENING axis (where
    #    the cavity is meant to break through to the outside), make the cutter
    #    OVERSHOOT the outer body by ≥5mm. If the cutter face is exactly
    #    coincident with the outer face, Blender's Boolean modifier becomes
    #    numerically unreliable and may produce a CLOSED BUBBLE inside the
    #    body — the cavity volume is removed but the opening is sealed by a
    #    thin sliver. Externally the cabinet looks solid.
    cav = spec["cavity"]
    cav_xyz = list(cav["world_xyz"])
    cav_extents = list(cav["world_extents"])
    OVERSHOOT_M = 0.010  # 10mm of overshoot; conservative + safe across runs
    # Detect which axis the cavity is supposed to open on (cavity face is
    # coincident with outer face on that axis) and extend the cutter past it.
    outer_min = [spec["world_xyz"][i] - spec["world_extents"][i]*0.5 for i in range(3)]
    outer_max = [spec["world_xyz"][i] + spec["world_extents"][i]*0.5 for i in range(3)]
    for axis_i in range(3):
        cmin = cav_xyz[axis_i] - cav_extents[axis_i]*0.5
        cmax = cav_xyz[axis_i] + cav_extents[axis_i]*0.5
        if abs(cmin - outer_min[axis_i]) < 0.005:
            cav_extents[axis_i] += OVERSHOOT_M
            cav_xyz[axis_i]      -= OVERSHOOT_M * 0.5
        if abs(cmax - outer_max[axis_i]) < 0.005:
            cav_extents[axis_i] += OVERSHOOT_M
            cav_xyz[axis_i]      += OVERSHOOT_M * 0.5
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=tuple(cav_xyz))
    inner = bpy.context.active_object
    inner.scale = tuple(cav_extents)
    bpy.ops.object.transform_apply(scale=True)
    # 3. boolean DIFFERENCE — bevel BEFORE this, not after. Bevel-after-boolean
    #    produces fragmented topology around the cavity rim and can make the
    #    coincident-face problem worse.
    mod = outer.modifiers.new(name="cut", type='BOOLEAN')
    mod.object = inner
    mod.operation = 'DIFFERENCE'
    bpy.context.view_layer.objects.active = outer
    bpy.ops.object.modifier_apply(modifier="cut")
    bpy.data.objects.remove(inner, do_unlink=True)
    outer.name = "<PascalName>"
    outer.color = tuple(spec["color_rgba"])
    return outer
```

**Two non-obvious rules for boolean modifiers** (the second is a real, observed bug — see `outputs/cab_a3_imgtex` for an example where the cabinet looked solid externally despite a correct hollow `cavity` spec):

1. **Bevel BEFORE Boolean, not after.** Beveling a post-boolean mesh with `limit_method='ANGLE'` hits the internal cavity-rim edges too and fragments the topology.
2. **Cutter must overshoot the outer body on the opening axis.** If the cutter face plane is exactly coincident with the outer body's face plane, Blender's boolean may produce a closed bubble inside the body (cavity volume removed but opening sealed). 5-10mm overshoot is enough.

### Strategy: composite (handles, hardware — NOT a single cube)

For parts that should NOT be a primitive (handles especially), build multiple sub-meshes with bmesh or primitive ops, then join. Example for a D-handle:

```python
import bpy, bmesh, math
from mathutils import Matrix, Vector
def build_handle():
    spec = ...
    grip_radius = 0.008
    grip_length = spec["world_extents"][0] * 0.9
    grip_center = Vector(spec["world_xyz"])

    bm = bmesh.new()
    # main grip cylinder (rotated to lie along X)
    bmesh.ops.create_cone(bm, segments=32, radius1=grip_radius, radius2=grip_radius,
                           depth=grip_length, cap_ends=True,
                           matrix=Matrix.Translation(grip_center) @ Matrix.Rotation(math.radians(90), 4, 'Y'))
    # two stubs connecting to drawer face — left as exercise to the implementor
    ...
    mesh = bpy.data.meshes.new("Handle_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new("Handle", mesh)
    bpy.context.collection.objects.link(obj)
    obj.color = tuple(spec["color_rgba"])
    return obj
```

## Placement: baked vs canonical (opt-in)

By default, your `build_<part>()` constructs the part **at its final world position** — `world_xyz` and `world_rpy` baked into the `bpy.ops.primitive_*_add(location=...)` calls. This is the simplest pattern. The bbox contract then asserts the produced object's world bbox center is at `world_xyz`. Almost every example uses this mode.

**Alternative: canonical-pose mode.** When `design.parts[i]` has `place_method: "canonical"`, the part should be built **at the origin with no rotation**, regardless of where it ends up in the assembled scene. `src/build.py` then applies the world transform after calling your builder. The bbox contract for canonical parts asserts: bbox center is at `(0,0,0)`, extents match `world_extents`.

When to pick canonical:

- You're authoring a **reusable** part that should drop into any project (a generic handle / knob / drawer pull — placement varies, shape doesn't)
- The project wants to **render at multiple joint poses** (e.g. drawer at 0%/50%/100% open) — canonical parts can be re-placed at runtime via `place_<part>(obj, ...)`
- Decoupling shape from pose makes your `build_<part>()` cleaner — no per-coord arithmetic mixed with primitive ops

Canonical implementation pattern:

```python
def build_handle():
    spec = ...
    # Read spec but IGNORE world_xyz / world_rpy — build at origin
    ex, ey, ez = spec["world_extents"]
    color = tuple(spec["color_rgba"])

    # Construct at (0, 0, 0):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.scale = (ex, ey, ez)
    bpy.ops.object.transform_apply(scale=True)
    obj.name = spec["name"]
    obj.color = color
    return obj
```

`src/build.py` is responsible for applying the transform after every canonical build call:

```python
for spec in DESIGN["parts"]:
    obj = BUILDERS[spec["name"]]()
    obj.name = spec["name"]
    if spec.get("place_method", "baked") == "canonical":
        obj.location = tuple(spec["world_xyz"])
        if spec.get("world_rpy"):
            obj.rotation_euler = tuple(spec["world_rpy"])
        bpy.context.view_layer.update()
```

For the bbox validator: when `place_method == "canonical"`, validate that the FINAL world bbox (after placement) matches the contract — same as baked mode. The validator code itself doesn't change.

## Adding detail with modifiers (after building the rough shape)

### Bevel for soft edges

```python
bevel = obj.modifiers.new(name="OuterBevel", type='BEVEL')
bevel.width = spec.get("outer_bevel_radius", 0.003)
bevel.segments = 2
bevel.limit_method = 'ANGLE'
bevel.angle_limit = math.radians(30)
bpy.context.view_layer.objects.active = obj
bpy.ops.object.modifier_apply(modifier="OuterBevel")
```

### Recessed inset panel (for drawer fronts)

Build the front face as TWO panels: an outer "window frame" + a recessed inner panel sitting `front_inset_depth` further inside than the outer plane.

## Hard rules

- bpy only, no third-party packages
- NO cameras, NO lights, NO `scene.world.*`, NO `scene.render.*`, NO `bpy.ops.render.render()`
- NO top-level code beyond imports and the build function definition
- Deterministic (no random)
- File must be importable as `from parts.<lower> import build_<lower>`
- Set `obj.name` to exactly the PascalCase spec name (the BUILDERS dict in build.py keys by this)
- Set `obj.color` from `spec["color_rgba"]` so render_multiview's PBR auto-material picks it up
