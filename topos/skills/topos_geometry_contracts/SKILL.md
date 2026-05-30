---
name: topos_geometry_contracts
description: Extra build.py validations that catch silent geometry failures the bbox-contract misses — hollow-shell verification and inter-part collision
when_to_use: Any AgentTask that writes src/build.py for a multi-part project, OR a project whose design.json declares one or more parts with a "cavity" field. Skip for single-solid-primitive rigid projects.
provides:
  - fill-ratio check: catches "spec declared cavity but mesh ended up solid" (boolean DIFFERENCE silently failed)
  - inter-part collision check: catches "two parts unintentionally interpenetrate" via AABB overlap on joint-unrelated pairs
  - cavity-fit check: catches "child too small / too big relative to parent's declared cavity" (the finger-wide gap case)
related_tools:
  - blender_run
related_skills:
  - topos_part_geometry      # bbox contract is the baseline; these contracts layer on top
  - topos_design_articulated # design.json's "cavity" and joints fields are what these checks read
---

# Topos: Geometry Contracts

The bbox contract in `topos_part_geometry` only validates each part's **outer AABB**. It cannot see whether a frame is hollow, whether a drawer actually fits the cavity, or whether two parts are unintentionally interpenetrating. This skill provides three drop-in validators for `src/build.py` that run after bbox validation and print `[OK]` / `[WARN]` per check.

All three are **deterministic, no-token, post-build**. They print WARN but do not raise — render still proceeds so the judge can see the visual mistake too. The WARNs are written into the Blender stdout, captured in the run trajectory, and (in future) fed to the fix-loop.

## When to emit each check

| Check | Emit when |
|---|---|
| `fill-ratio`         | any part in design.json has a `cavity` field |
| `cavity-opening`     | any part has a `cavity` whose face is coincident with an outer face |
| `inter-part`         | `len(design.parts) >= 2` |
| `cavity-fit`         | any part has a `cavity` field AND that part is the parent of a joint |

If none of the conditions are met (single-part rigid project), you don't need any of this — just bbox is enough.

---

## Check 1 — Fill-ratio (hollow-shell verification)

**The failure mode this catches.** A part is declared hollow in design.json (`"cavity": {...}`). The builder does a boolean DIFFERENCE between an outer cube and an inner cube. If the boolean modifier silently fails to apply (wrong active object, inner cube not selected by modifier reference, modifier_apply called on the wrong context), the outer cube stays solid. The bbox contract still says `[OK]` because the outer AABB is unchanged. From the 8 octant exterior renders the failure is also invisible — the cabinet looks like a closed wooden cube. The judge then complains "featureless cube" without knowing the real cause.

**The reasoning.** `bm.calc_volume()` is reliable on closed manifold meshes and unreliable on open ones. A correctly-hollowed shell (with an opening on at least one face, as cabinet frames should) is *not* a closed manifold — so calc_volume returns a value we should not trust. But the **failure case** — boolean didn't apply, mesh stays a solid cube — IS a closed manifold, and calc_volume returns the full outer AABB volume. We can therefore reliably catch the failure direction.

```python
# Add inside src/build.py, AFTER the bbox contract validation block.
# Loop over the same DESIGN["parts"] entries.

import bmesh

print("=== fill-ratio contract (hollow-shell check) ===")
for spec in DESIGN["parts"]:
    if "cavity" not in spec:
        continue                              # nothing to check; part is supposed to be solid
    name = spec["name"]
    if name not in bpy.data.objects:
        continue
    obj = bpy.data.objects[name]

    # Outer AABB volume from spec (the closed-failure volume we want to detect)
    sx, sy, sz = spec["world_extents"]
    outer_vol = sx * sy * sz

    # Cavity volume from spec
    cx, cy, cz = spec["cavity"]["world_extents"]
    cavity_vol = cx * cy * cz

    # Actual mesh volume (reliable only if mesh is closed; that's exactly the failure case)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    try:
        actual_vol = abs(bm.calc_volume(signed=False))
    finally:
        bm.free()

    # If the boolean worked, actual is roughly (outer - cavity) ± some non-manifold noise.
    # If the boolean silently failed, actual ≈ outer.
    # Threshold: 80% of outer means "no meaningful cavity was cut".
    fail_threshold = 0.80 * outer_vol
    if actual_vol >= fail_threshold:
        print(
            f"[HOLLOW_WARN] {name}: spec declares cavity "
            f"(expect ~{(outer_vol - cavity_vol):.4f} m³) "
            f"but actual mesh volume is {actual_vol:.4f} m³ "
            f"({100*actual_vol/outer_vol:.0f}% of outer AABB) — "
            f"boolean DIFFERENCE likely failed; mesh is still solid."
        )
    else:
        print(
            f"[OK] {name}: hollow check passed "
            f"(actual {actual_vol:.4f} m³ / outer {outer_vol:.4f} m³ = {100*actual_vol/outer_vol:.0f}%)"
        )
```

**What this does NOT catch:**

- Cavity cut in the wrong direction (e.g. opens at +Z instead of -Y). Mesh is hollow, just opens the wrong way. The volume check passes. → use cavity-fit + visual inspection by the judge.
- Cavity cut but produced as a closed bubble inside the body (cavity didn't reach any outer face). Volume still drops, check passes, but you have an unusable internal void. → relies on visual evidence; not addressed here.

---

## Check 1b — Cavity-opening (the closed-bubble case)

**The failure mode this catches.** A part declares `cavity` with one or more faces coincident with the outer body's faces (cavity touches outer wall on some axis). Logically the cavity should open to the outside through those faces — for a drawer cabinet, the cavity opens on -Y (front). But Blender's Boolean modifier is **numerically unreliable on coincident faces**: when the cutter's face plane is exactly the same as the body's face plane, the boolean may produce a **closed bubble inside** the body instead of a hole through to outside. The `fill-ratio` check still passes (the cavity volume IS removed), but external viewers see every face closed — the cabinet looks solid from outside.

**The reasoning.** For each part with `cavity`, look up which of the 6 outer-AABB faces the cavity touches (cavity.min == outer.min or cavity.max == outer.max on each axis). For each such expected-opening face, measure the actual mesh-face coverage on that face plane. If the cavity actually punched through, coverage should be ~`1 - cavity_face_area / outer_face_area` (just a frame ring). If the cavity is a closed bubble inside, coverage will be ~100% (face still solid).

**Cure (in `topos_part_geometry`):** make the cutter overshoot the outer body by ≥5mm on the opening axis so the boolean works on a clearly-interior region, not at a coincident plane.

```python
# Add after the fill-ratio block.

print("=== cavity-opening contract ===")
TOL_COINCIDENT_M = 0.005   # 5mm: cavity face is "coincident" with outer face within this
TOL_PLANE_M      = 0.015   # 15mm: which mesh faces count as "on this face plane" (allows for bevel)
CLOSED_FAIL_PCT  = 90.0    # coverage > 90% of the outer-face area → opening didn't punch through

for spec in DESIGN["parts"]:
    if "cavity" not in spec:
        continue
    name = spec["name"]
    if name not in bpy.data.objects:
        continue
    obj = bpy.data.objects[name]
    mesh = obj.data
    spec_center  = Vector(spec["world_xyz"])
    spec_extents = Vector(spec["world_extents"])
    cav_center   = Vector(spec["cavity"]["world_xyz"])
    cav_extents  = Vector(spec["cavity"]["world_extents"])

    outer_min = spec_center - spec_extents * 0.5
    outer_max = spec_center + spec_extents * 0.5
    cav_min   = cav_center  - cav_extents  * 0.5
    cav_max   = cav_center  + cav_extents  * 0.5

    # Find which outer faces the cavity touches (= expected openings).
    # (axis_i, direction=+1 or -1)
    expected = []
    for axis_i in range(3):
        if abs(cav_min[axis_i] - outer_min[axis_i]) < TOL_COINCIDENT_M:
            expected.append((axis_i, -1))
        if abs(cav_max[axis_i] - outer_max[axis_i]) < TOL_COINCIDENT_M:
            expected.append((axis_i, +1))
    if not expected:
        print(f"[OPENING_OK] {name}: cavity is a fully enclosed internal void (not designed to open) — skipping")
        continue

    for axis_i, direction in expected:
        axis_name = "XYZ"[axis_i]
        face_plane = outer_min[axis_i] if direction == -1 else outer_max[axis_i]
        # Sum mesh-face area whose centroid lies on this face plane AND
        # whose world-space normal points outward along this axis.
        coverage = 0.0
        for poly in mesh.polygons:
            normal_world = (obj.matrix_world.to_3x3() @ poly.normal).normalized()
            if abs(normal_world[axis_i] - direction) > 0.1:
                continue
            center_world = obj.matrix_world @ poly.center
            if abs(center_world[axis_i] - face_plane) > TOL_PLANE_M:
                continue
            coverage += poly.area
        # Full outer-face area on the perpendicular plane
        other = [spec_extents[i] for i in range(3) if i != axis_i]
        full_face_area = other[0] * other[1]
        # Cavity-opening area
        other_cav = [cav_extents[i] for i in range(3) if i != axis_i]
        cav_face_area = other_cav[0] * other_cav[1]
        coverage_pct = 100.0 * coverage / full_face_area if full_face_area > 0 else 0
        ring_pct = 100.0 * (full_face_area - cav_face_area) / full_face_area
        sign = "-" if direction == -1 else "+"
        if coverage_pct > CLOSED_FAIL_PCT:
            print(
                f"[OPENING_WARN] {name}: cavity should open through {sign}{axis_name} face "
                f"but face is {coverage_pct:.0f}% covered (expected ~{ring_pct:.0f}% frame ring). "
                f"Likely coincident-face boolean failure — cavity is a closed bubble inside. "
                f"Fix: make the inner cutter overshoot the outer body by ≥5mm on {axis_name}."
            )
        else:
            print(
                f"[OPENING_OK] {name}: cavity opens through {sign}{axis_name} face "
                f"({coverage_pct:.0f}% coverage, expected ~{ring_pct:.0f}%)"
            )
```

**What this catches that bbox + fill-ratio do NOT**:
The cavity volume is correctly removed (fill-ratio passes), bbox is correct (outer dimensions unchanged), but the opening that was supposed to connect cavity to outside is sealed by Blender's boolean producing a thin sliver of geometry. Visually undetectable from internal-only checks; very visible the moment you load the GLB in an external viewer.

---

## Check 2 — Inter-part collision

**The failure mode this catches.** Two parts that should not touch each other are interpenetrating. Example: a handle whose stubs extend so deep they punch through the drawer face and stick into the cabinet body. Or a drawer whose back panel extends past the frame's back wall.

**The reasoning.** For each pair of parts, check whether they are connected by a joint in `design.json`. Joint-connected pairs are *expected* to overlap (a prismatic joint child slides into its parent's cavity; a fixed joint child is attached to its parent's surface). Joint-unrelated pairs should not overlap. AABB intersection is conservative (overestimates), but a non-zero AABB intersection on an unrelated pair is a strong signal.

```python
# Add after the fill-ratio block.

from mathutils import Vector

def _aabb_world(o):
    corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
    xs, ys, zs = zip(*[(v.x, v.y, v.z) for v in corners])
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

def _aabb_intersection_volume(o_a, o_b):
    (a_lo, a_hi) = _aabb_world(o_a)
    (b_lo, b_hi) = _aabb_world(o_b)
    vol = 1.0
    for i in range(3):
        lo = max(a_lo[i], b_lo[i])
        hi = min(a_hi[i], b_hi[i])
        if hi <= lo:
            return 0.0
        vol *= (hi - lo)
    return vol

def _aabb_volume(o):
    lo, hi = _aabb_world(o)
    return (hi[0]-lo[0]) * (hi[1]-lo[1]) * (hi[2]-lo[2])

# Build the set of joint-related pairs (unordered)
related = set()
for j in DESIGN.get("joints", []):
    p, c = j.get("parent"), j.get("child")
    if p and c:
        related.add(frozenset((p, c)))

names = [s["name"] for s in DESIGN["parts"] if s["name"] in bpy.data.objects]
print("=== inter-part collision contract ===")
COLLIDE_TOL = 1e-6   # 1 mm³ of AABB overlap considered noise
for i in range(len(names)):
    for k in range(i + 1, len(names)):
        a, b = names[i], names[k]
        if frozenset((a, b)) in related:
            continue                              # joint-related: overlap expected
        oa, ob = bpy.data.objects[a], bpy.data.objects[b]
        vol = _aabb_intersection_volume(oa, ob)
        if vol > COLLIDE_TOL:
            smaller = min(_aabb_volume(oa), _aabb_volume(ob))
            pct = 100.0 * vol / smaller if smaller > 1e-9 else 0.0
            print(
                f"[COLLISION_WARN] {a} <-> {b}: AABB overlap "
                f"{vol*1e6:.1f} cm³ ({pct:.0f}% of smaller part) — "
                f"no joint connects these parts; likely interpenetration."
            )
```

**Limitation.** AABB intersection is an upper bound. Two parts can have overlapping AABBs while their actual meshes don't touch (e.g. an L-shaped part and a small cube tucked into the L's outer corner). For most furniture-grade primitives this is rare; if your project has lots of non-AABB-fillable parts, swap `_aabb_intersection_volume` for a true mesh boolean intersect via bmesh — more accurate, ~10x slower.

---

## Check 3 — Cavity-fit (the finger-wide-gap case)

**The failure mode this catches.** A part declares a `cavity` and is the parent of a joint. The child is supposed to fit *inside* the cavity with a small construction clearance (a few mm). The child ends up much smaller than the cavity → the judge sees a visible gap on multiple sides ("drawer rattles, doesn't ride on rails"). Or much larger → child interferes with cavity walls.

```python
# Add after the inter-part block.

print("=== cavity-fit contract ===")
TIGHT_FIT_MM     = 2.0    # below this, may interfere — flag if negative
LOOSE_FIT_MM     = 30.0   # above this, finger-wide gap — flag

specs_by_name = {s["name"]: s for s in DESIGN["parts"]}
for j in DESIGN.get("joints", []):
    parent_spec = specs_by_name.get(j.get("parent"))
    child_spec  = specs_by_name.get(j.get("child"))
    if not parent_spec or not child_spec:
        continue
    if "cavity" not in parent_spec:
        continue              # parent has no declared cavity; skip
    cav = parent_spec["cavity"]["world_extents"]
    ch  = child_spec["world_extents"]
    for axis_i, axis_name in enumerate("XYZ"):
        clearance_m = cav[axis_i] - ch[axis_i]
        clearance_mm = clearance_m * 1000.0
        if clearance_mm < -TIGHT_FIT_MM:
            print(
                f"[FIT_WARN] {child_spec['name']} interferes with "
                f"{parent_spec['name']}.cavity on axis {axis_name}: "
                f"child is {-clearance_mm:.1f} mm larger than cavity."
            )
        elif clearance_mm > LOOSE_FIT_MM:
            print(
                f"[FIT_WARN] {child_spec['name']} is loose in "
                f"{parent_spec['name']}.cavity on axis {axis_name}: "
                f"{clearance_mm:.1f} mm gap (>{LOOSE_FIT_MM:.0f} mm)."
            )
        else:
            print(
                f"[OK] {child_spec['name']} fits {parent_spec['name']}.cavity "
                f"axis {axis_name}: clearance {clearance_mm:+.1f} mm."
            )
```

**Note on slide-axis loose-fit.** A prismatic joint's slide axis is *expected* to be loose (the drawer slides in and out — the child is shorter than the cavity on that axis). So a loose-fit WARN on the slide axis is a false positive. To suppress, look up the joint's `axis` field and skip that axis. Implementation left out for simplicity; if you find your project tripping on this, add:

```python
if j.get("type") == "prismatic" and j.get("axis"):
    slide_axis_i = max(range(3), key=lambda i: abs(j["axis"][i]))
    if axis_i == slide_axis_i:
        continue
```

just inside the `for axis_i, axis_name in enumerate("XYZ"):` loop.

---

## Check 4 — Fixed-joint attachment (no floating)

**The failure mode this catches.** A part is fixed-jointed to a parent (e.g.
a handle attached to a drawer's front face). Design.json places the child's
bbox flush against the parent's surface — they should touch. But the agent's
implementation in `parts/<child>.py` may place the actual mesh vertices in
the wrong world position, leaving an air gap between child and parent. The
object renders fine geometrically but visually reads as "the handle is
floating in space in front of the drawer", which the judge flags as a fit /
plausibility error AND the user notices immediately in any external viewer.

**Real example (observed in cab_a7_full).** design.json: Drawer front face at
Y=-0.280, Handle back face at Y=-0.280 (touching). After build, mesh-mesh
closest-point distance: **32.6mm** — handle is 3cm away from the drawer.

The agent's bbox-contract check passes (Handle's outer AABB matches the spec),
but contact doesn't follow from AABB alone — you can have correct outer bounds
yet wrong internal vertex positions.

**The reasoning.** For each `fixed` joint, every vertex of the child mesh
should be either inside or near (within a few mm of) the parent mesh's
surface. We use Blender's ``closest_point_on_mesh`` which is O(N log N) per
pair with the BVH. Threshold: child's MINIMUM unsigned distance to parent
should be ≤ 5mm; otherwise the parts don't actually touch.

```python
# Add after the cavity-fit block.

print("=== fixed-joint attachment contract ===")
ATTACH_TOL_M = 0.005             # 5mm: anything closer is "touching"
MIN_CONTACT_VERTS = 4            # need at least this many contact verts

for joint in DESIGN.get("joints", []):
    if joint.get("type") != "fixed":
        continue                  # prismatic/revolute joints can have intentional gaps
    parent_name = joint.get("parent")
    child_name  = joint.get("child")
    if parent_name not in bpy.data.objects or child_name not in bpy.data.objects:
        continue
    parent_obj = bpy.data.objects[parent_name]
    child_obj  = bpy.data.objects[child_name]

    parent_inv      = parent_obj.matrix_world.inverted()
    child_to_world  = child_obj.matrix_world

    contact_verts = 0
    min_dist      = float("inf")
    for v in child_obj.data.vertices:
        v_world = child_to_world @ v.co
        # closest_point_on_mesh operates in the object's LOCAL space
        v_local = parent_inv @ v_world
        ok, closest_local, _, _ = parent_obj.closest_point_on_mesh(v_local)
        if not ok:
            continue
        closest_world = parent_obj.matrix_world @ closest_local
        dist = (v_world - closest_world).length
        if dist < min_dist:
            min_dist = dist
        if dist < ATTACH_TOL_M:
            contact_verts += 1

    if min_dist == float("inf"):
        print(f"[ATTACHMENT_SKIP] {joint['name']}: closest_point_on_mesh "
              f"returned no valid result; cannot evaluate.")
        continue

    if min_dist > ATTACH_TOL_M or contact_verts < MIN_CONTACT_VERTS:
        print(
            f"[ATTACHMENT_WARN] {joint['name']} ({child_name}→{parent_name}, fixed): "
            f"min gap {min_dist*1000:.1f}mm "
            f"(tolerance {ATTACH_TOL_M*1000:.0f}mm); "
            f"{contact_verts}/{len(child_obj.data.vertices)} verts in contact "
            f"(need >= {MIN_CONTACT_VERTS}). "
            f"Check {child_name}'s build_<lower>() — the mesh vertices must "
            f"actually reach the {parent_name} surface, not just sit nearby."
        )
    else:
        print(
            f"[ATTACHMENT_OK] {joint['name']}: {contact_verts} contact verts, "
            f"min gap {min_dist*1000:.2f}mm"
        )

    # ---- Sub-check: mount on RECESSED inset face, not OUTER rim ----
    # When the parent declares ``front_inset_depth`` (e.g. a drawer with a
    # recessed front panel), the child of a fixed joint should sit on the
    # recessed surface, not on the surrounding rim. The closest-point gate
    # above passes in BOTH cases (the parent mesh contains both surfaces),
    # but visually the part reads as misaligned if mounted on the rim.
    parent_spec = next((s for s in DESIGN["parts"] if s["name"] == parent_name), None)
    child_spec  = next((s for s in DESIGN["parts"] if s["name"] == child_name),  None)
    if parent_spec and child_spec and parent_spec.get("front_inset_depth", 0.0) > 0.0:
        inset_depth = float(parent_spec["front_inset_depth"])
        p_center = Vector(parent_spec["world_xyz"])
        p_ext    = Vector(parent_spec["world_extents"])
        c_center = Vector(child_spec["world_xyz"])
        delta    = c_center - p_center
        # Infer the mounting face = axis along which child sits furthest from parent center.
        axis_i   = max(range(3), key=lambda i: abs(delta[i]))
        sign     = 1.0 if delta[axis_i] > 0 else -1.0
        outer_plane = p_center[axis_i] + sign * p_ext[axis_i] * 0.5
        inset_plane = outer_plane - sign * inset_depth
        child_coords = [(child_to_world @ v.co)[axis_i] for v in child_obj.data.vertices]
        if child_coords:
            # Child's mounting-face coord = the extreme vertex pointing INWARD toward parent.
            mount_coord = min(child_coords) if sign > 0 else max(child_coords)
            gap_outer = abs(mount_coord - outer_plane)
            gap_inset = abs(mount_coord - inset_plane)
            axis_name = "XYZ"[axis_i]
            if gap_outer < ATTACH_TOL_M and gap_inset > ATTACH_TOL_M:
                print(
                    f"[ATTACHMENT_INSET_WARN] {joint['name']}: {child_name} mounted on "
                    f"{parent_name}'s OUTER rim (plane {outer_plane*1000:+.1f}mm on {axis_name}) "
                    f"but parent has front_inset_depth={inset_depth*1000:.1f}mm — child "
                    f"should sit flush against the RECESSED INSET face at "
                    f"{inset_plane*1000:+.1f}mm instead. "
                    f"Fix: shift {child_name}.world_xyz on {axis_name} by "
                    f"{(-sign*inset_depth)*1000:+.1f}mm so its mounting face reaches "
                    f"the recessed panel; the rim should frame the child, not support it."
                )
            elif gap_inset < ATTACH_TOL_M:
                print(
                    f"[ATTACHMENT_INSET_OK] {joint['name']}: {child_name} flush with "
                    f"recessed inset face (inset_depth={inset_depth*1000:.1f}mm)"
                )
            else:
                print(
                    f"[ATTACHMENT_INSET_INFO] {joint['name']}: {child_name} mounting "
                    f"coord {mount_coord*1000:+.1f}mm on {axis_name} is neither on the "
                    f"outer rim ({outer_plane*1000:+.1f}mm) nor on the inset face "
                    f"({inset_plane*1000:+.1f}mm); review placement."
                )
```

**What this catches that bbox + spec-fit do NOT.** Outer-AABB checks see only
the extents; they're blind to "all the vertices are clustered in the back of
the bbox, so the front of the bbox is empty air". The closest-point check
looks at actual geometry, not the imaginary outer hull. The inset sub-check
adds a further layer: even when the child *does* touch the parent, it
distinguishes "touches the recessed inset" (correct) from "touches the outer
rim around the inset" (the visual-misalignment failure mode — handle screwed
to the frame ring, floating above the recessed panel).

**Why limit to `fixed` joints.** Prismatic / revolute joints intentionally
allow the child to translate or rotate away from the parent in the rest pose
(e.g. a drawer can be half-open). The "must touch" assumption only holds for
rigid attachment.

---

## Putting it together in build.py

The order in `src/build.py` is:

```
bbox contract               (from topos_part_geometry)
   ↓
fill-ratio contract         (this skill, if any part has cavity)
   ↓
cavity-opening contract     (this skill, if cavity faces are coincident with outer faces)
   ↓
inter-part collision        (this skill, if ≥ 2 parts)
   ↓
cavity-fit contract         (this skill, if any part has cavity and is a joint parent)
   ↓
fixed-joint attachment      (this skill, if any joint is fixed-type)
```

All five print their own `===` header so the trajectory is easy to grep. None of them raise — every check is advisory, and downstream render/export/judge run unconditionally so the judge gets visual data too.
