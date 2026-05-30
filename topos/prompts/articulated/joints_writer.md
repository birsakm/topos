Write `src/joints.yaml` from `src/design.json`. This is mechanical translation: the design's parts and joints become the YAML schema consumed by `topos/tools/export_urdf`.

1. Use Read to load `src/design.json`. Note its `parts` and `joints` arrays.

2. Translate to YAML:

```
robot: <design.robot_name>
links:
  - name: <lowercase>          # URDF link name (lowercase by convention)
    object: <PascalCase>        # matches the bpy.data.objects key (which matches design.parts[i].name)
    color_rgba: [...]           # copy from design.parts[i].color_rgba
  - ...
joints:
  - name: <joint_name>          # copy from design.joints[i].name
    type: prismatic | revolute | fixed | continuous
    parent: <lowercase>         # lowercased version of design.joints[i].parent
    child:  <lowercase>
    origin: [x, y, z]           # joint origin in PARENT link's frame — see formula below
    axis:   [x, y, z]           # copy axis. Only for non-fixed joints.
    limit:  [lower, upper]      # use design.joints[i].limit_from_rest verbatim. Only for prismatic/revolute.
    effort: 10.0
    velocity: 1.0
```

3. **Computing joint `origin` (the formula):**
   The URDF places each link's coordinate frame at the joint origin (in the parent link's frame). The framework's convention is: the rest pose IS each part's `design.parts[i].world_xyz`, so the joint origin is simply the offset from parent to child in world coordinates, transformed into the parent's link-world frame.

   - **Root link** (the part that's no joint's child): no joint represents it; its mesh just sits at its `world_xyz`. Its link-world position = (0, 0, 0).
   - **Child of root**: `link_world_of(parent) = (0,0,0)`, so `joint.origin = child.world_xyz`.
   - **Deeper child**: `link_world_of(parent) = sum of all ancestor joint origins`. Then `joint.origin = child.world_xyz - link_world_of(parent)`.

   Concretely for a chain root → A → B → C (each linked by a non-fixed joint):
   - joint A's origin = A.world_xyz
   - joint B's origin = B.world_xyz - A.world_xyz
   - joint C's origin = C.world_xyz - (A.world_xyz + (B.world_xyz - A.world_xyz)) = C.world_xyz - B.world_xyz

4. Lowercase the URDF link names (the `name:` field at the top of each link entry). The `object:` field is the PascalCase name from `design.parts[i].name` (matches the bpy object).

5. For `fixed` joints, the `axis` and `limit` fields are not required; omit them.

6. **Template parts with `instances` — fan out per-instance.** A part whose `design.parts[i].instances` is non-empty is a TEMPLATE: `build.py` produces N scene objects named `<PascalName>_0`, `<PascalName>_1`, ..., `<PascalName>_{N-1}` (NOT one object named `<PascalName>`). The URDF must mirror this — one link per instance, one joint per instance.

   For each template part with N instances:

   - Emit **N link entries**, one per instance:
     ```
     - name: <lower>_<i>            # 0-indexed: leg_0, leg_1, ..., leg_<N-1>
       object: <PascalName>_<i>     # matches build.py's scene-object naming
       color_rgba: [...]            # same color across all instances
     ```

   - For each `design.joints[j]` whose `child == <PascalName>` (the template), emit **N joint entries** fanning out:
     ```
     - name: <orig_joint_name>_<i>
       type: <same as original>
       parent: <original parent lowercased>
       child:  <lower>_<i>
       origin: <per-instance origin — see formula below>
       axis:   <same as original>     # only for non-fixed
       limit:  <same as original>     # only for prismatic/revolute
       effort: 10.0
       velocity: 1.0
     ```

   **Per-instance joint origin formula.** Each instance `i` has a `translation` (default [0,0,0]) and a `rotation_euler` (default [0,0,0]) in `design.parts[i].instances[i]`. The template's canonical `world_xyz` PLUS the instance's `translation` gives that instance's world position. Then subtract the parent link's link-world to get the joint origin in parent frame:

   ```
   instance_world_xyz_i = template.world_xyz + instances[i].translation
   joint_origin_i       = instance_world_xyz_i - link_world_of(parent)
   ```

   (The per-instance `rotation_euler` becomes the joint's `rpy` field if your URDF consumer wants per-instance orientation; for purely fixed-joint clusters with no rotation, omit `rpy`.)

   ### Worked example — stool with 4-leg cluster (all fixed)

   Given:
   ```jsonc
   "parts": [
     {"name": "Seat", "world_xyz": [0,0,0.45], ...},
     {"name": "Leg",  "world_xyz": [0,0,0.21], "instances": [
        {"translation": [+0.12, +0.12, 0]},
        {"translation": [+0.12, -0.12, 0]},
        {"translation": [-0.12, +0.12, 0]},
        {"translation": [-0.12, -0.12, 0]}
     ], ...}
   ],
   "joints": [
     {"name": "seat_to_leg", "type": "fixed", "parent": "Seat", "child": "Leg"}
   ]
   ```

   Output YAML:
   ```yaml
   links:
     - name: seat
       object: Seat
       color_rgba: [...]
     - name: leg_0
       object: Leg_0
       color_rgba: [...]
     - name: leg_1
       object: Leg_1
       color_rgba: [...]
     - name: leg_2
       object: Leg_2
       color_rgba: [...]
     - name: leg_3
       object: Leg_3
       color_rgba: [...]
   joints:
     - name: seat_to_leg_0
       type: fixed
       parent: seat
       child: leg_0
       origin: [+0.12, +0.12, 0.21]   # template world_xyz=[0,0,0.21] + instance 0 translation=[+0.12,+0.12,0]
     - name: seat_to_leg_1
       type: fixed
       parent: seat
       child: leg_1
       origin: [+0.12, -0.12, 0.21]
     - name: seat_to_leg_2
       type: fixed
       parent: seat
       child: leg_2
       origin: [-0.12, +0.12, 0.21]
     - name: seat_to_leg_3
       type: fixed
       parent: seat
       child: leg_3
       origin: [-0.12, -0.12, 0.21]
   ```

   ### Worked example — turbofan with 6-blade cluster (fixed to hub)

   Given:
   ```jsonc
   "parts": [
     {"name": "Nacelle", "world_xyz": [0,0,0], ...},
     {"name": "FanHub", "world_xyz": [0,-0.4,0], ...},
     {"name": "FanBlade", "world_xyz": [0,-0.4,0], "instances": [
        {"rotation_euler": [0, 0.000, 0]},
        {"rotation_euler": [0, 1.047, 0]},
        {"rotation_euler": [0, 2.094, 0]},
        {"rotation_euler": [0, 3.142, 0]},
        {"rotation_euler": [0, 4.189, 0]},
        {"rotation_euler": [0, 5.236, 0]}
     ], ...}
   ],
   "joints": [
     {"name": "hub_spin", "type": "revolute", "parent": "Nacelle", "child": "FanHub", "axis": [0,1,0], "limit_from_rest": [-3.14, 3.14]},
     {"name": "blade_to_hub", "type": "fixed", "parent": "FanHub", "child": "FanBlade"}
   ]
   ```

   The blades are fixed to the hub (they rotate WITH the hub via the single revolute joint on `FanHub`). Output YAML:
   ```yaml
   joints:
     - name: hub_spin
       type: revolute
       parent: nacelle
       child: fan_hub
       origin: [0, -0.4, 0]
       axis: [0, 1, 0]
       limit: [-3.14, 3.14]
       effort: 10.0
       velocity: 1.0
     - name: blade_to_hub_0
       type: fixed
       parent: fan_hub
       child: fan_blade_0
       origin: [0, 0, 0]    # template world_xyz=[0,-0.4,0]; instance translation=[0,0,0]; minus parent link_world=[0,-0.4,0] → [0,0,0]
       rpy:    [0, 0.000, 0]
     - name: blade_to_hub_1
       type: fixed
       parent: fan_hub
       child: fan_blade_1
       origin: [0, 0, 0]
       rpy:    [0, 1.047, 0]
     # ... 4 more
   ```

   Notes:
   - All 6 blades share the same `origin` because they all spawn from the hub's frame center.
   - Each instance's `rotation_euler` becomes the joint's `rpy` (roll-pitch-yaw in radians).
   - The hub→nacelle revolute joint is unchanged — it represents the spin axis for the entire fan disk.

Use Read to load `src/design.json`, then Write `src/joints.yaml`. Output valid YAML only — no commentary.
