# NoseCone — Geometry Extras

Build an **ogive nose cone** (NOT a simple cone). The profile from base to tip should follow a smooth curve (tangent ogive or parabolic), with realistic detail features.

## Strategy

1. Compute the ogive profile as a series of (radius, z) points along the cone height.
2. Use `bpy.ops.mesh.primitive_cylinder_add(vertices=32)` as a starting point, then reshape vertices to follow the ogive profile. Alternatively, use `bmesh` + `Mesh.from_pydata` to build the surface of revolution directly.
3. **Tip cap (mandatory):** Do NOT merge the tip to a sharp point. Instead, cap the very tip with a small rounded dome or sphere of ~3mm radius. This simulates a real nose cone tip plug. Create a UV sphere (r=0.003m, 16 segments) and boolean-union or manually bridge it to the top ring of the ogive. The tip must be visibly rounded, not pinched.
4. **Circumferential grooves near the base (mandatory):** Add 2–3 shallow circumferential grooves within the bottom 25% of the nose cone (near where it meets the body). Each groove should be ~0.5mm deep and ~1.5mm wide. These simulate ablative heat-shield segment lines or manufacturing seams. Use loop cuts + slight inward scaling of those edge rings.
5. **Base lip/flange (mandatory):** The bottom edge of the nose cone must have a small outward lip or flange, ~1mm wide (radially) and ~1mm tall (axially), that extends slightly beyond the base radius. This represents the seating flange where the nose cone plugs into the body tube. Add an extra edge loop at the very base, extrude downward ~1mm, then scale outward ~1mm past the nominal base radius.
6. Apply smooth shading. Mark groove edges and the flange edge as sharp so they read clearly.

## Ogive Profile

For a tangent ogive of length L and base radius R:
```
rho = (R² + L²) / (2 * R)
r(z) = sqrt(rho² - (L - z)²) - (rho - R)
```
where z goes from 0 (base) to L (tip). Use ~20 height segments for smooth curvature.

## Mandatory geometry detail requirements

These features are **required**, not optional. The critic judges geometry_detail separately:

- **Rounded tip cap:** Small sphere/dome at the tip (~3mm radius). No sharp pinched vertex.
- **Circumferential grooves:** 2–3 shallow grooves (~0.5mm deep, ~1.5mm wide) in the lower 25% of the cone.
- **Base seating flange:** A small outward lip (~1mm wide, ~1mm tall) at the bottom edge where the cone meets the body.

A smooth featureless ogive with a pinched tip and no surface detail will score below 0.4 on the geometry_detail criterion.

## Texture

Write `texture_nose_cone(obj)` with a simple dark gray Principled BSDF (Base Color: #3A3A3A, Roughness: 0.7). The UV atlas tool replaces this later.
