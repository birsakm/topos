- **Open-top 5-panel box (mandatory):** bottom + 4 side walls. NOT a solid block, NOT a closed box — the top must be open so a viewer looking down can see into the drawer's interior.
- **Recessed front-face inset border (mandatory):** the front face of the drawer (the -Y face) must have a visible inset panel — a rectangular recess `spec["front_inset_depth"]` deep, `spec["front_inset_margin"]` in from each edge of the front face. This makes the drawer face read as a real cabinet panel.
  * Simple strategy: build the front wall as TWO panels — an outer "window-frame" rectangle plus a recessed inner panel sitting `front_inset_depth` further inside. Join with the other drawer walls.
  * Or use `bmesh.ops.bevel` after defining a face inset.
- **Visible wall thickness:** drawer walls of `spec["wall_thickness"]` (~8mm) must be physically thick so the interior is bounded by visible material, not a paper-thin shell.
