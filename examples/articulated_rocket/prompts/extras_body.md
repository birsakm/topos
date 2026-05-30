# Body — Geometry Extras

Build a cylindrical fuselage tube with realistic surface detail.

## Strategy

1. `bpy.ops.mesh.primitive_cylinder_add(vertices=32, depth=0.32, radius=0.04)` for the main body.
2. Add **at least 3 horizontal panel lines** using loop cuts along the body length, each with a slight inset (~0.5mm inward from the outer surface). These simulate riveted panel seams on a real rocket fuselage.
3. Add a **circumferential rib/ring near the top** (within 15mm of the top edge) and another **near the bottom** (within 15mm of the bottom edge). Each rib should be a raised ring ~1mm proud of the body surface and ~3mm tall along the Z axis. Use a loop cut + scale outward, or extrude a ring face outward.
4. Apply a **slight taper on the bottom 20%** of the body: scale the lower vertex rings to ~0.95× the nominal radius. This is mandatory, not optional.
5. Add a **small rectangular access panel indent** on one side of the body: approximately 15mm wide × 25mm tall, inset ~1mm deep into the surface. Use a knife cut or boolean to create the recessed rectangle.
6. Smooth shade the cylinder, then mark the panel-line edges and rib edges as sharp (use Auto Smooth or edge split) so they read clearly.

## Important

- The cylinder is open at both ends (no cap faces needed — the NoseCone and Nozzle cover them visually).
- Body center at (0, 0, 0.18), height 0.32m, radius 0.04m.
- Keep vertex count reasonable (~32 circumference segments).

## Mandatory geometry detail requirements

These features are **required**, not optional. The critic judges geometry_detail separately:

- **Panel lines:** At least 3 horizontal seam lines with visible inset (~0.5mm). Evenly spaced along the body length.
- **Structural ribs:** Raised circumferential rings near top and bottom, ~1mm proud of the surface.
- **Base taper:** Bottom 20% of the body tapers to ~0.95× radius.
- **Access panel:** One rectangular recessed panel on the body side (~15mm × 25mm, 1mm deep).

A plain untapered cylinder with no panel lines or detail features will score below 0.4 on the geometry_detail criterion.

## Texture

Write `texture_body(obj)` with a white Principled BSDF (Base Color: #F0F0F0, Roughness: 0.4). The UV atlas tool replaces this later.
