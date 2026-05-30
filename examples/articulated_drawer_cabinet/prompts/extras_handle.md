- **NOT a single primitive cube.** The Handle MUST be a composite shape that reads as a recognizable handle. Pick ONE of these strategies and implement it faithfully:
  * **D-handle** (recommended when `spec["geometry_strategy"]` is `"D-handle"`):
    - One horizontal grip bar (cylinder OR rounded box), length matching `spec["world_extents"][0]`
    - Two short vertical stubs connecting the grip to the drawer face — these stubs make the handle visibly protrude from the drawer surface (~10-15mm depth)
    - Join all three pieces into one mesh
  * **Cylinder with end caps** — a main cylindrical shaft + two slightly larger disk endcaps; total bbox matches `world_extents`
  * **Recessed pull** — a thin frame ring on the drawer face with a hollow center (use `bmesh.ops.boolean` to cut into the drawer plane); flush handle look
- Total world bbox must STILL satisfy `world_extents` within 5mm. The composite parts together fill that bbox.
- Add a 2mm bevel to the grip bar edges (Bevel modifier or `bmesh.ops.bevel`).
