---
name: topos_joints_creator
description: URDF joint origin math + axis/limit semantics for writing joints.yaml from design.json
when_to_use: Any AgentTask that writes the joints.yaml articulation spec
provides:
  - URDF joint origin = child world − cumulative ancestor link world
  - axis / limit / type semantics
  - lowercase URDF link names vs PascalCase bpy object names
related_tools:
  - export_urdf
related_skills:
  - topos_design_articulated
---

# Topos: Joints Creator

This skill teaches the URDF joint semantics required to produce a correct `src/joints.yaml` from `src/design.json`. Use any time you write articulation specs.

## The joints.yaml schema (consumed by `topos/tools/export_urdf`)

```
robot: <design.robot_name>
links:
  - name: <lowercase>            # URDF link name, lowercase by convention
    object: <PascalCase>          # matches bpy.data.objects key (= design.parts[i].name)
    color_rgba: [r, g, b, a]      # copy from design.parts[i].color_rgba
  - ...
joints:
  - name: <joint_name>            # copy from design.joints[i].name
    type: prismatic | revolute | fixed | continuous
    parent: <lowercase>           # lowercased parent link
    child:  <lowercase>
    origin: [x, y, z]             # IN PARENT'S FRAME — see formula below
    axis:   [x, y, z]             # only for non-fixed joints
    limit:  [lower, upper]        # only for prismatic/revolute (use design.limit_from_rest)
    effort: 10.0                  # standard default; override only if intent says so
    velocity: 1.0
```

## Computing `origin` — the formula that breaks if you skip a step

URDF places each link's coordinate frame at the joint origin, expressed **in the parent link's frame**. The framework's convention is that **rest pose = each part's `design.parts[i].world_xyz`**, so joints sit where the moving link rests.

Let `link_world(P)` be link `P`'s frame position in world coordinates.

- **Root link** (the part with no incoming joint): `link_world(root) = (0, 0, 0)`. The root has no joint entry; its mesh just sits at its `design.parts[root].world_xyz`.
- **Direct child of root**: `joint.origin = child.world_xyz`  (because subtracting `(0,0,0)` is a no-op).
- **Deeper child**: `joint.origin = child.world_xyz − link_world(parent)` where `link_world(parent) = link_world(grandparent) + parent_joint.origin`. Walk up the chain summing.

### Worked example: drawer + handle (3 links, depth-2 chain)

Suppose `design.json` has:
- `Frame.world_xyz = (0, 0, 0.15)` ← root
- `Drawer.world_xyz = (0, -0.05, 0.15)`
- `Handle.world_xyz = (0, -0.18, 0.15)`
- joint `drawer_slide`: parent=Frame, child=Drawer
- joint `handle_to_drawer`: parent=Drawer, child=Handle

Then:
- `link_world(frame) = (0, 0, 0)` (root)
- `joint drawer_slide.origin = Drawer.world_xyz - (0,0,0) = (0, -0.05, 0.15)`
- `link_world(drawer) = (0, 0, 0) + (0, -0.05, 0.15) = (0, -0.05, 0.15)`
- `joint handle_to_drawer.origin = Handle.world_xyz - link_world(drawer) = (0, -0.18, 0.15) - (0, -0.05, 0.15) = (0, -0.13, 0)`

Notice: it equals `Handle.world_xyz - Drawer.world_xyz`. This shortcut works for ANY joint between two parts that are NOT both root-relative — origin is just the child's world position minus the parent part's world position. (For joints connecting to root, parent_world = 0,0,0, so it's just child.world_xyz.)

## Joint types & axis/limit semantics

| Type | What it allows | Required fields | Limit semantics |
|---|---|---|---|
| `prismatic` | Linear motion along `axis` | axis, limit | `limit_from_rest` is the linear range in meters. `[0, 0.20]` = "from rest, slide 0 to 0.20 m along axis" |
| `revolute` | Rotation about `axis`, bounded | axis, limit | `limit_from_rest` in radians |
| `continuous` | Rotation about `axis`, unbounded | axis | omit limit |
| `fixed` | Rigid attachment | (origin only) | no axis or limit |

`axis` is a 3-vector (will be normalized by URDF parser). Common choices:
- `[0, -1, 0]` = motion along -Y (e.g. drawer pulling out the front of a cabinet facing -Y)
- `[0, 0, 1]` = motion along +Z (e.g. lid lifting upward, or rotational about world Z)
- `[1, 0, 0]` = motion along +X (this is also the axle for a **wheel/roller that rolls forward** — see below)

A `continuous` wheel/disc/roller that rolls along the travel direction spins about the **left-right axis `[1, 0, 0]`** (NOT `[0, 1, 0]`, the travel direction). If the design's wheel axis looks like `[0, 1, 0]`, it's almost certainly a design bug (the wheel would wobble, not roll) — see `topos_design_articulated` › Axis convention.

Pull the design's `axis` and `limit_from_rest` straight through to the YAML — the framework's rest pose contract is set up so they translate 1:1.

## Lowercase / PascalCase

- `links[i].name` — **lowercase** URDF link name (`frame`, `drawer`, `handle`). Convention.
- `links[i].object` — **PascalCase** name (`Frame`, `Drawer`, `Handle`). Matches the bpy object name set in `build_<lower>()` and the `name` field in `design.parts[i]`.
- `joints[i].parent` / `child` — **lowercase**. Reference the `links[i].name` field.

Mismatch here is the most common bug: the export_urdf tool can't resolve the link → it returns an error.

## Process

1. Use Read to load `src/design.json`.
2. For each `design.parts[i]`, emit a `links[]` entry. Lowercase the name for the `name:` field.
3. For each `design.joints[i]`, emit a `joints[]` entry. Compute origin via the formula above. Copy axis + limit_from_rest as-is for non-fixed joints. Skip axis/limit for fixed joints.
4. Set `effort: 10.0` and `velocity: 1.0` defaults unless the design says otherwise.
5. Use Write to create `src/joints.yaml`. Output valid YAML only.
