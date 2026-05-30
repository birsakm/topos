# Articulated Rocket — Intent

Build a **toy/model rocket** with 4 parts and 2 joints. Approximate scale: 50 cm tall, 8 cm body diameter. Z-up, rocket stands vertically with nose cone at the top.

## Parts

1. **NoseCone** — ogive (curved) nose cone at the top. Smooth cone shape tapering to a rounded tip. Sits flush on top of the Body.
   - World center: (0, 0, 0.40)
   - Approximate extents: 6 cm diameter × 12 cm tall

2. **Body** — main cylindrical fuselage. Simple cylinder, open at top and bottom (the nose cone and nozzle cap the ends visually). Add subtle panel-line grooves or a slight taper near the base for visual interest.
   - World center: (0, 0, 0.18)
   - Approximate extents: 8 cm diameter × 32 cm tall

3. **Fins** — 4 swept delta fins symmetrically placed around the base of the body. Each fin is a thin trapezoidal plate with swept-back leading edge. Model all 4 fins as a single part (using array or manual placement).
   - World center: (0, 0, 0.04)
   - Approximate extents: 16 cm wide × 16 cm deep × 10 cm tall

4. **Nozzle** — bell-shaped engine nozzle at the bottom. A truncated cone (wider at bottom, narrower where it meets the body). The nozzle can gimbal (rotate) for thrust vector control.
   - World center: (0, 0, -0.04)
   - Approximate extents: 5 cm diameter × 6 cm tall

## Joints

1. **nose_fixed** — NoseCone to Body, type: fixed. Nose sits directly on top of the body cylinder.
2. **nozzle_gimbal** — Nozzle to Body, type: revolute. Pivot at the bottom face of the Body. Axis: [1, 0, 0] (pitch). Limit: ±8 degrees from rest.

## Rest Pose

Nozzle tilted ~4° off-center (half of max gimbal range) so the articulation is visible in renders. All other parts at their default position.

## Textures

All parts use **UV-atlas image-based textures** (kind: "uv_atlas"). The design agent should write `texture.kind: "uv_atlas"` for every part.

- **NoseCone**: "heat-resistant ablative thermal protection, charcoal gray with subtle hexagonal tile pattern and faint orange heat discoloration near the tip"
- **Body**: "white rocket fuselage with a bold red horizontal stripe band around the middle, small national flag decal, and stenciled mission designation numbers in dark gray"
- **Fins**: "dark carbon fiber composite weave pattern, matte black with subtle fiber direction visible"
- **Nozzle**: "heat-scorched engine bell metal, gradient from clean brushed steel at the throat to oxidized copper-brown tones at the exit rim"

## Geometry Guidelines

- **NoseCone**: Use bmesh or Mesh.from_pydata to create an ogive/parabolic nose. NOT a simple cone — the profile should curve smoothly. ~32 segments around the circumference for smooth silhouette.
- **Body**: Cylinder with 32+ segments. Optionally add a subtle taper or panel lines (loop cuts + slight inset).
- **Fins**: Each fin is a trapezoidal plate ~3mm thick. Swept leading edge angle ~30°. Place 4 fins at 90° intervals using rotation. Bevel fin edges 0.5mm.
- **Nozzle**: Truncated cone / bell curve profile. Wider at exit (bottom), narrower at throat (top). Use 32 segments. Inner surface visible from below.

## Key Constraints

- All dimensions in **meters** (SI). The rocket is ~0.5m tall.
- Z-up coordinate system.
- Part meshes must be manifold where possible.
- Each part's `build_<name>()` creates geometry + applies `texture_<name>(obj)`.
- Texture functions should create a simple flat-color Principled BSDF as fallback — the UV atlas tool will replace it with the generated texture image post-build.
