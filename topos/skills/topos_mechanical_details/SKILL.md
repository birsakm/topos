---
name: topos_mechanical_details
description: Worked code patterns for mechanical / vehicle parts — cranksets, pedals, chainrings, sprockets, derailleurs, spoked wheels, and insertion-style connections (seat post, steerer). Elevates these beyond "single primitive" into recognizable mechanism.
when_to_use: Any part agent implementing a mechanical drivetrain / running-gear part (crankset, pedal, chainring/sprocket/cog, cassette, derailleur, spoked wheel, hub, axle, spindle, pulley) OR a part that plugs into a tube (seat post, fork steerer, stem). Read this in addition to topos_part_geometry whenever the part is a mechanism.
provides:
  - crankset (bottom-bracket axle + 2 offset crank arms + chainring + 2 pedals) as ONE multi-primitive build
  - pedal (spindle + platform body)
  - chainring / sprocket / cog (thin disc + radial teeth, cheap)
  - spoked wheel (hub + rim + tire + N radial spokes)
  - derailleur / parallelogram linkage (cage + 2 jockey pulleys + link plates)
  - insertion-style connection (seat post / steerer / stem plugging into a tube with overlap + clamp)
  - a fixed-primitive-count budget so the part is built in ONE pass, not explored
related_skills:
  - topos_part_geometry
  - topos_design_articulated
  - topos_joints_creator
---

# Topos: Mechanical Details

Drivetrain and running-gear parts (cranksets, pedals, sprockets, derailleurs, spoked wheels) are what visually separate "a few cylinders pretending to be a bicycle" from a machine that reads as mechanical. This skill gives worked Blender Python for each, **plus a budget discipline** so the part is built in one decisive pass.

All examples assume you've read `spec` from `src/design.json` and want to produce a single `bpy` object whose world bbox satisfies the contract (within 5mm of `spec["world_xyz"]` and `spec["world_extents"]`). Each strategy is a complete `build_<name>()` implementation. The `spec`-loading preamble is the same as `topos_part_geometry`:

```python
import bpy, bmesh, math, json, os
from mathutils import Matrix, Vector

def _load_spec(name):
    HERE = os.path.dirname(__file__)
    return next(p for p in json.load(open(os.path.join(HERE, "..", "design.json")))["parts"]
                if p["name"] == name)
```

## Budget discipline — build it ONCE (read this first)

A mechanism is a **fixed, small** number of primitives. Decide the count up front from the recipe below, place them by arithmetic, emit one mesh. **Do not** iteratively render-inspect-nudge a mechanical part dozens of times — that is what makes a single crankset burn 15+ minutes and a fistful of dollars while landing at the same place a recipe reaches in one pass.

| Part            | Primitive count | What they are |
|-----------------|-----------------|---------------|
| Crankset        | 8–12            | axle + 2 arms + chainring disc + (8–16 teeth) + 2 pedals |
| Pedal           | 2–3             | spindle + platform (+ optional cage) |
| Chainring/cog   | 1 disc + N teeth | thin cylinder + radial thin cubes |
| Spoked wheel    | 3 + N spokes    | hub + rim torus + tire torus + 16–32 spokes |
| Derailleur      | 5–7             | cage plate(s) + 2 pulleys + mounting bolt |
| Seat post / steerer | 1–2         | shaft cylinder (+ clamp ring) |

If you've placed the recipe's primitives and the bbox validates, **you are done.** Resist "one more refinement."

## Strategy: Crankset (the highest-value, most-failed bicycle part)

A crankset reads as mechanical only when these co-exist: a **central axle** through the bottom bracket, **two crank arms offset 180°** (one toward +X, one toward −X — i.e. opposite sides AND opposite rotation), a **toothed chainring** coaxial with the axle, and a **pedal at the end of each arm**. The axle runs left-right (**X**), same as a wheel axle (see `topos_design_articulated`).

```python
def build_crankset():
    spec = _load_spec("Crankset")          # use the actual spec name
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]     # ex = left-right (axle) span; ey,ez ≈ chainring + arm reach
    color = tuple(spec["color_rgba"])

    bm = bmesh.new()
    X = lambda: Matrix.Rotation(math.radians(90), 4, 'Y')   # cylinder axis → X

    # 1) Bottom-bracket axle (spans most of ex, thin)
    axle_r = min(ey, ez) * 0.05
    bmesh.ops.create_cone(bm, segments=16, radius1=axle_r, radius2=axle_r,
                          depth=ex * 0.9, cap_ends=True,
                          matrix=Matrix.Translation((cx, cy, cz)) @ X())

    # 2) Chainring — a thin disc coaxial with the axle, near the +X (drive) side
    ring_r = min(ey, ez) * 0.48
    ring_x = cx + ex * 0.30
    bmesh.ops.create_cone(bm, segments=40, radius1=ring_r, radius2=ring_r,
                          depth=axle_r * 1.2, cap_ends=True,
                          matrix=Matrix.Translation((ring_x, cy, cz)) @ X())
    # 2b) Teeth — thin cubes around the rim (cheap, but it's what says "sprocket")
    n_teeth = 12
    tooth = ring_r * 0.10
    for i in range(n_teeth):
        a = 2 * math.pi * i / n_teeth
        ty, tz = cy + ring_r * math.cos(a), cz + ring_r * math.sin(a)
        bmesh.ops.create_cube(bm, size=1.0,
            matrix=Matrix.Translation((ring_x, ty, tz))
                   @ Matrix.Rotation(a, 4, 'X')
                   @ Matrix.Diagonal(Vector((axle_r * 1.2, tooth, tooth, 1.0))))

    # 3) Two crank arms, 180° apart, on opposite ends of the axle
    arm_len = min(ey, ez) * 0.45            # crank arm reaches out from the axle
    arm_w   = arm_len * 0.14
    for sign, x_end, angle in ((+1, cx + ex * 0.45, 0.0),
                               (-1, cx - ex * 0.45, math.pi)):   # opposite side + opposite phase
        ay = cy + arm_len * 0.5 * math.cos(angle)
        az = cz + arm_len * 0.5 * math.sin(angle)
        bmesh.ops.create_cube(bm, size=1.0,
            matrix=Matrix.Translation((x_end, ay, az))
                   @ Matrix.Rotation(angle, 4, 'X')
                   @ Matrix.Diagonal(Vector((arm_w, arm_len, arm_w, 1.0))))
        # 4) Pedal spindle stub at the far end of each arm
        py = cy + arm_len * math.cos(angle)
        pz = cz + arm_len * math.sin(angle)
        bmesh.ops.create_cone(bm, segments=12, radius1=arm_w * 0.5, radius2=arm_w * 0.5,
                              depth=ex * 0.14, cap_ends=True,
                              matrix=Matrix.Translation((x_end + sign * ex * 0.06, py, pz)) @ X())

    mesh = bpy.data.meshes.new("Crankset_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(spec["name"], mesh)
    bpy.context.collection.objects.link(obj)
    obj.color = color
    return obj
```

If the spec models the **pedals as separate parts** (their own joints), drop step 4's stubs and let the Pedal part agent build them at the arm ends — coordinate via the world positions in `design.json`.

## Strategy: Pedal

A platform body on a short spindle. The spindle is the axle the pedal spins on; the platform is what a foot rests on.

```python
def build_pedal():
    spec = _load_spec("Pedal")             # or "LeftPedal" / "RightPedal"
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]
    bm = bmesh.new()
    # spindle along X (inboard toward the crank arm)
    sp_r = min(ey, ez) * 0.18
    bmesh.ops.create_cone(bm, segments=12, radius1=sp_r, radius2=sp_r,
                          depth=ex, cap_ends=True,
                          matrix=Matrix.Translation((cx, cy, cz)) @ Matrix.Rotation(math.radians(90), 4, 'Y'))
    # platform body (flat box) at the outboard end
    plat_x = cx + ex * 0.18
    bmesh.ops.create_cube(bm, size=1.0,
        matrix=Matrix.Translation((plat_x, cy, cz))
               @ Matrix.Diagonal(Vector((ex * 0.5, ey * 0.95, ez * 0.55, 1.0))))
    mesh = bpy.data.meshes.new("Pedal_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(spec["name"], mesh)
    bpy.context.collection.objects.link(obj); obj.color = tuple(spec["color_rgba"])
    return obj
```

## Strategy: Spoked wheel (hub + rim + tire + spokes)

A wheel reads as a *spoked* wheel, not a disc, only with visible spokes spanning hub→rim. The whole thing lies in the **Y-Z plane** (thin along X), spinning about **X** — see the axis convention in `topos_design_articulated`. Use `bmesh.ops.create_circle` extruded, or two tori + cylinders:

```python
def build_front_wheel():
    spec = _load_spec("FrontWheel")
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]     # ex thin (e.g. 0.04); ey≈ez≈diameter
    R = min(ey, ez) * 0.5                   # outer (tire) radius
    bm = bmesh.new()
    YZ = lambda: Matrix.Rotation(math.radians(90), 4, 'Y')   # disc in Y-Z plane, axle along X
    # tire (outer torus) and rim (slightly inner torus)
    for major, minor in ((R * 0.93, ex * 0.5), (R * 0.80, ex * 0.28)):
        bmesh.ops.create_circle  # (kept simple: approximate tori as thin cylinders if create_torus unavailable)
    # robust approach: rim + tire as thin cylinders (annulus look comes from the spokes + hub gap)
    for rad, depth in ((R, ex), (R * 0.82, ex * 0.7)):
        bmesh.ops.create_cone(bm, segments=48, radius1=rad, radius2=rad, depth=depth,
                              cap_ends=False, matrix=Matrix.Translation((cx, cy, cz)) @ YZ())
    # hub
    hub_r = R * 0.12
    bmesh.ops.create_cone(bm, segments=16, radius1=hub_r, radius2=hub_r, depth=ex * 1.1,
                          cap_ends=True, matrix=Matrix.Translation((cx, cy, cz)) @ YZ())
    # spokes: thin cylinders from hub to rim, in the Y-Z plane
    n_spokes = 24
    spoke_r = R * 0.012
    for i in range(n_spokes):
        a = 2 * math.pi * i / n_spokes
        my, mz = cy + (R * 0.45) * math.cos(a), cz + (R * 0.45) * math.sin(a)
        bmesh.ops.create_cone(bm, segments=6, radius1=spoke_r, radius2=spoke_r, depth=R * 0.82,
                              cap_ends=True,
                              matrix=Matrix.Translation((cx, my, mz))
                                     @ Matrix.Rotation(a + math.pi / 2, 4, 'X'))
    mesh = bpy.data.meshes.new("FrontWheel_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(spec["name"], mesh)
    bpy.context.collection.objects.link(obj); obj.color = tuple(spec["color_rgba"])
    return obj
```
(If `bmesh.ops.create_torus` exists in your Blender, prefer two tori for tire+rim — cleaner than open cylinders. Either way: hub + spokes + rim is the minimum that reads as a wheel.)

## Strategy: Derailleur / parallelogram linkage

A cage holding two small jockey pulleys, hung off a mounting bolt. The two pulleys stacked vertically + the cage plates are the recognizable signature.

```python
def build_derailleur():
    spec = _load_spec("Derailleur")
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]
    bm = bmesh.new()
    X = lambda: Matrix.Rotation(math.radians(90), 4, 'Y')
    pulley_r = min(ey, ez) * 0.22
    # two jockey pulleys, stacked along Z
    for sz in (+0.5, -0.5):
        pz = cz + sz * ez * 0.55
        bmesh.ops.create_cone(bm, segments=20, radius1=pulley_r, radius2=pulley_r,
                              depth=ex * 0.5, cap_ends=True,
                              matrix=Matrix.Translation((cx, cy, pz)) @ X())
    # cage plates: two thin boxes front/back spanning the pulleys
    for sx in (+0.5, -0.5):
        bmesh.ops.create_cube(bm, size=1.0,
            matrix=Matrix.Translation((cx + sx * ex * 0.28, cy, cz))
                   @ Matrix.Diagonal(Vector((ex * 0.08, ey * 0.3, ez * 1.1, 1.0))))
    # mounting bolt up to the frame
    bmesh.ops.create_cone(bm, segments=10, radius1=pulley_r * 0.4, radius2=pulley_r * 0.4,
                          depth=ez * 0.5, cap_ends=True,
                          matrix=Matrix.Translation((cx, cy, cz + ez * 0.6)))
    mesh = bpy.data.meshes.new("Derailleur_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(spec["name"], mesh)
    bpy.context.collection.objects.link(obj); obj.color = tuple(spec["color_rgba"])
    return obj
```

## Strategy: Insertion-style connection (seat post, steerer, stem)

A part that **plugs into a tube** (seat post into the seat tube; fork steerer into the head tube; stem into the steerer) must **physically overlap the host tube** so it reads as inserted, not floating above it. This is the #1 reason a seat post or stem scores low: it hovers with a visible gap.

Rules:
- **Overlap ≥ 1–2 cm** into the host. The spec's `world_xyz`/`world_extents` should already place the shaft's lower end *inside* the host tube — verify against the host part's bbox in `design.json` and, if there's a gap, extend your shaft downward to close it (staying within your bbox tolerance; if the contract itself leaves a gap, flag it for the build agent rather than floating).
- **Min diameter:** a seat post / steerer is a slim cylinder — radius ≈ 1.0–1.6 cm, never a fat box.
- **Add a clamp ring** at the insertion seam — a short, slightly larger-radius cylinder. It hides the seam and is what real bikes have.

```python
def build_seat_post():
    spec = _load_spec("SeatPost")
    cx, cy, cz = spec["world_xyz"]
    ex, ey, ez = spec["world_extents"]     # tall + slim: ez >> ex,ey
    shaft_r = max(ex, ey) * 0.45
    bm = bmesh.new()
    # vertical shaft (along Z). It should already extend down into the seat tube.
    bmesh.ops.create_cone(bm, segments=20, radius1=shaft_r, radius2=shaft_r,
                          depth=ez, cap_ends=True, matrix=Matrix.Translation((cx, cy, cz)))
    # clamp ring near the lower (insertion) end
    clamp_z = cz - ez * 0.5 + ez * 0.12
    bmesh.ops.create_cone(bm, segments=24, radius1=shaft_r * 1.45, radius2=shaft_r * 1.45,
                          depth=ez * 0.06, cap_ends=True, matrix=Matrix.Translation((cx, cy, clamp_z)))
    mesh = bpy.data.meshes.new("SeatPost_mesh")
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(spec["name"], mesh)
    bpy.context.collection.objects.link(obj); obj.color = tuple(spec["color_rgba"])
    return obj
```

## Decisional heuristics — which strategy to pick

- Spec name contains `crank` / `crankset` / `chainset` → **Crankset** (with chainring + arms + pedal stubs unless pedals are separate parts)
- `pedal` → **Pedal**
- `chainring` / `sprocket` / `cog` / `cassette` / `gear` → thin disc + radial teeth (crankset's step 2)
- `wheel` (with spokes implied) → **Spoked wheel**
- `derailleur` / `mech` → **Derailleur**
- `seat_post` / `seatpost` / `steerer` / `stem` / anything that plugs into a tube → **Insertion-style connection**

## Anti-patterns

- **Single cylinder as "crankset"** — the most common failure; a crankset MUST show arms + chainring, ≥8 primitives.
- **Chainring with no teeth** — a smooth disc reads as a plate, not a sprocket. The radial teeth are cheap and load-bearing for recognition.
- **Crank arms not 180° apart** — both arms on the same side / same phase looks broken. Opposite X end AND opposite rotation.
- **Floating seat post / stem** — any insertion part with a visible gap to its host tube. Overlap + clamp ring.
- **Wheel as a solid disc** — no hub/spoke gap. A bike wheel is mostly empty space crossed by spokes.
- **Iterating a mechanism by eye** — burns time and money for no gain over the recipe. Place the fixed primitive set, validate bbox, stop (see Budget discipline).

## When to invoke this skill

Any part agent whose part name/role indicates a drivetrain, running-gear, or insertion mechanism:
- `Crank`, `Crankset`, `Chainset`, `Pedal`, `Chainring`, `Sprocket`, `Cog`, `Cassette`, `Gear`
- `Wheel`, `Hub`, `Spoke`, `Axle`, `Spindle`, `Pulley`
- `Derailleur`, `Mech`
- `SeatPost`, `Steerer`, `Stem` (insertion-style connection)

For purely structural parts (Frame, Fork blades, body panels), use `topos_part_geometry` alone — this skill is overkill for those.
