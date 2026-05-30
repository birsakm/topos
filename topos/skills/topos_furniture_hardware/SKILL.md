---
name: topos_furniture_hardware
description: Worked code patterns for furniture hardware — handles, knobs, drawer pulls, hinges. Designed to elevate parts beyond "single primitive cube" into recognizable industrial-design hardware.
when_to_use: Any part agent implementing a handle, pull, knob, hinge, or other small mechanical/decorative hardware affordance. Read this in addition to topos_part_geometry whenever the part is hardware.
provides:
  - D-handle (cylindrical grip + two vertical stubs + optional flange)
  - Cylinder-with-caps handle (shaft + flared endcaps)
  - Recessed pull (subtractive finger hollow in the drawer face)
  - Knob (mushroom / spherical / faceted top on a short stem)
  - Hinge / piano hinge (multi-knuckle revolute fixture)
  - Drawer slide rail (visible side runner)
related_tools:
  - blender_run
related_skills:
  - topos_part_geometry
  - topos_design_articulated
---

# Topos: Furniture Hardware

Hardware (handles, knobs, hinges) is what visually separates "axis-aligned cubes pretending to be a cabinet" from "actual furniture". This skill gives worked Blender Python for each strategy. **Use one of these patterns** for any part the spec calls a handle / pull / knob / hinge.

All examples assume you've read `spec` from `src/design.json` and want to produce a single `bpy` object whose world bbox satisfies the contract (within 5mm of `spec["world_xyz"]` and `spec["world_extents"]`). Each strategy is a complete `build_<name>()` implementation.

## Strategy: D-handle (the most common drawer/cabinet handle)

A horizontal cylindrical grip connected to the drawer face by two short vertical stubs. The negative space between grip and face is what makes it READ as a handle, not a bar.

```python
import bpy
import bmesh
import math
from mathutils import Matrix, Vector

def build_handle():
    # Read spec
    import json, os
    HERE = os.path.dirname(__file__)
    spec = next(p for p in json.load(open(os.path.join(HERE, "..", "design.json")))["parts"]
                if p["name"] == "Handle")
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]
    color = tuple(spec["color_rgba"])

    # Geometry parameters (proportions tuned to look like real D-handle):
    grip_length = ex * 0.85               # grip a touch shorter than full handle width
    grip_radius = max(ez, ey) * 0.30       # round grip — radius ~30% of handle thickness
    stub_height = ey * 0.5                  # stubs span half the protrusion depth
    stub_radius = grip_radius * 0.6        # stubs are thinner than grip

    bm = bmesh.new()

    # Grip cylinder (along X axis, in front of stubs)
    grip_y = cy - ey * 0.5 + stub_height + grip_radius
    grip_center = Vector((cx, grip_y, cz))
    grip_mat = Matrix.Translation(grip_center) @ Matrix.Rotation(math.radians(90), 4, 'Y')
    bmesh.ops.create_cone(bm, segments=32, radius1=grip_radius, radius2=grip_radius,
                           depth=grip_length, cap_ends=True, matrix=grip_mat)

    # Two stubs connecting grip to drawer face
    stub_x_offset = grip_length * 0.42   # stubs near grip ends, slightly inboard
    stub_y = cy - ey * 0.5 + stub_height * 0.5
    for sx in (-stub_x_offset, stub_x_offset):
        stub_center = Vector((cx + sx, stub_y, cz))
        # Stub as a thin upright box; orient it so its length is along Y (depth)
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(stub_center) @ Matrix.Diagonal(Vector((stub_radius*2, stub_height, stub_radius*2, 1.0))))

    mesh = bpy.data.meshes.new("Handle_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new("Handle", mesh)
    bpy.context.collection.objects.link(obj)
    obj.color = color
    return obj
```

Key proportions a D-handle MUST have:
- Grip diameter ~25-35% of overall handle thickness (too thin = wire-like; too thick = bar-like)
- Stubs visibly thinner than the grip
- Visible negative space between grip and the drawer face (the "U" of the D)

## Strategy: Cylinder-with-caps

A cylindrical shaft with two slightly larger flat disk endcaps. Looks like a brass pull or hi-fi knob.

```python
def build_handle():
    spec = ...   # read as above
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]
    shaft_radius = ez * 0.40
    shaft_length = ex - shaft_radius * 0.8   # leave room for endcaps
    cap_radius = shaft_radius * 1.25
    cap_thickness = ex * 0.06

    bm = bmesh.new()
    # main shaft along X
    bmesh.ops.create_cone(bm, segments=24,
                           radius1=shaft_radius, radius2=shaft_radius,
                           depth=shaft_length, cap_ends=True,
                           matrix=Matrix.Translation((cx, cy, cz)) @ Matrix.Rotation(math.radians(90), 4, 'Y'))
    # two endcap discs
    for sx in (-1.0, 1.0):
        cap_x = cx + sx * shaft_length * 0.5
        bmesh.ops.create_cone(bm, segments=32,
                               radius1=cap_radius, radius2=cap_radius,
                               depth=cap_thickness, cap_ends=True,
                               matrix=Matrix.Translation((cap_x, cy, cz)) @ Matrix.Rotation(math.radians(90), 4, 'Y'))
    mesh = bpy.data.meshes.new("Handle_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new("Handle", mesh)
    bpy.context.collection.objects.link(obj)
    obj.color = tuple(spec["color_rgba"])
    return obj
```

## Strategy: Recessed pull (flush handle, finger-hollow)

No protrusion — the "handle" is a hollow scooped into the drawer face. Best when the cabinet aesthetic is modern/flush.

This strategy is implemented as a SUBTRACTIVE operation on the drawer (not as a separate Handle part). When the spec calls for a recessed pull as the Handle, you can either:
(a) Have Handle be a thin negative-space marker mesh and let it bbox-validate; the actual cut is in build_drawer().
(b) Build Handle as a small frame ring + recessed inner panel that visually marks the pull area.

For (b):

```python
def build_handle():
    spec = ...
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]
    rim_thickness = ey * 0.4
    rim_depth = min(ex, ez) * 0.10
    inner_pad = rim_depth + 0.002

    bm = bmesh.new()
    # outer rim (frame around the recess)
    # Built as 4 thin bars forming a rectangle on the drawer face
    half_x = ex * 0.5
    half_z = ez * 0.5
    for side, (loc, dims) in {
        "top":    ((cx, cy, cz + half_z - rim_thickness*0.5), (ex, rim_depth, rim_thickness)),
        "bottom": ((cx, cy, cz - half_z + rim_thickness*0.5), (ex, rim_depth, rim_thickness)),
        "left":   ((cx - half_x + rim_thickness*0.5, cy, cz), (rim_thickness, rim_depth, ez - 2*rim_thickness)),
        "right":  ((cx + half_x - rim_thickness*0.5, cy, cz), (rim_thickness, rim_depth, ez - 2*rim_thickness)),
    }.items():
        bmesh.ops.create_cube(bm, size=1.0,
                               matrix=Matrix.Translation(loc) @ Matrix.Diagonal(Vector((dims[0], dims[1], dims[2], 1.0))))

    # inner recess plate (sits slightly inside, defining the finger pocket)
    bmesh.ops.create_cube(bm, size=1.0,
                           matrix=Matrix.Translation((cx, cy + rim_depth*0.5 - 0.001, cz))
                                  @ Matrix.Diagonal(Vector((ex - 2*rim_thickness, 0.001, ez - 2*rim_thickness, 1.0))))

    mesh = bpy.data.meshes.new("Handle_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new("Handle", mesh)
    bpy.context.collection.objects.link(obj)
    obj.color = tuple(spec["color_rgba"])
    return obj
```

## Strategy: Knob (mushroom / spherical top)

A single mushroom-shaped knob — short cylindrical stem + spherical / hemispherical / faceted top.

```python
def build_knob():
    spec = ...
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]
    # Knob is roughly axially symmetric — assume ex == ez and depth = ey
    head_radius = min(ex, ez) * 0.5
    stem_radius = head_radius * 0.4
    stem_depth = ey * 0.55
    head_depth = ey * 0.45

    bm = bmesh.new()
    # stem cylinder pointing -Y (toward drawer face)
    stem_y = cy - ey * 0.5 + stem_depth * 0.5
    bmesh.ops.create_cone(bm, segments=20,
                           radius1=stem_radius, radius2=stem_radius,
                           depth=stem_depth, cap_ends=True,
                           matrix=Matrix.Translation((cx, stem_y, cz)) @ Matrix.Rotation(math.radians(90), 4, 'X'))
    # head sphere (UV sphere)
    head_y = cy + ey * 0.5 - head_depth * 0.5
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=head_radius,
                               matrix=Matrix.Translation((cx, head_y, cz)))
    mesh = bpy.data.meshes.new("Knob_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(spec["name"], mesh)
    bpy.context.collection.objects.link(obj)
    obj.color = tuple(spec["color_rgba"])
    return obj
```

## Strategy: Piano hinge / barrel hinge (for door joints)

Visible hinges along an edge between two parts. Use when the spec includes a door / lid and you want the hinge to read in renders.

A piano hinge is a series of alternating "knuckles" (cylindrical segments) interlocking along a pin axis. The visible hinge is usually a separate Hinge part with its own joint constraints.

```python
def build_hinge():
    spec = ...
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]
    # hinge runs along Z (vertical door hinge)
    knuckle_radius = min(ex, ey) * 0.5
    n_knuckles = 5
    knuckle_height = ez / n_knuckles
    pin_radius = knuckle_radius * 0.20

    bm = bmesh.new()
    # alternating knuckles attached to part-A / part-B
    for i in range(n_knuckles):
        kz = cz - ez * 0.5 + (i + 0.5) * knuckle_height
        bmesh.ops.create_cone(bm, segments=20,
                               radius1=knuckle_radius, radius2=knuckle_radius,
                               depth=knuckle_height * 0.9, cap_ends=True,
                               matrix=Matrix.Translation((cx, cy, kz)))
    # central pin running through
    bmesh.ops.create_cone(bm, segments=12,
                           radius1=pin_radius, radius2=pin_radius,
                           depth=ez, cap_ends=True,
                           matrix=Matrix.Translation((cx, cy, cz)))
    mesh = bpy.data.meshes.new("Hinge_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(spec["name"], mesh)
    bpy.context.collection.objects.link(obj)
    obj.color = tuple(spec["color_rgba"])
    return obj
```

## Decisional heuristics — which strategy to pick

- Spec says "drawer pull" with no shape detail → **D-handle**
- Spec says "knob" or "round handle" → **Knob (mushroom)**
- Spec says "modern" / "minimalist" / "flush" → **Recessed pull**
- Spec describes a metallic cylindrical fixture → **Cylinder-with-caps**
- Spec includes a "door" or "lid" + emphasizes hinge → **Piano hinge** as a separate visible part

## Anti-patterns

- **Single primitive cube as "handle"** — by far the most common failure. ALWAYS at least 2 sub-meshes (grip + something else).
- **No bevel** on hardware edges — bare cubes read as toy blocks even at the right size. 1-2mm bevel modifier.
- **Same color as the parent part** — handles benefit from being a darker / contrasting color (e.g. dark wood handle on light cabinet, or black metal handle on wood drawer).
- **Bbox satisfied but no recognizable shape** — a bar matching the bbox extents is technically correct but visually a fail. The negative space (the "D" of the D-handle, the recess of a knob, etc.) is what makes the part read as hardware.

## When to invoke this skill

This skill applies whenever any part agent is implementing a part whose name or role indicates a hardware fixture:
- `Handle`, `Pull`, `Knob`, `Grip`, `Drawer_pull`
- `Hinge`, `Piano_hinge`, `Barrel_hinge`
- `Latch`, `Catch`, `Lock`

For purely structural parts (Frame, Drawer body, Door panel, Shade), use `topos_part_geometry` alone — this skill is overkill for those.
