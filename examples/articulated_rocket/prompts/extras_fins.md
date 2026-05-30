# Fins — Geometry Extras

Build **4 swept delta fins** symmetrically arranged around the rocket body base.

## Strategy

1. Create ONE fin as a trapezoidal plate using `bmesh` or `Mesh.from_pydata`:
   - Root chord: ~0.08m (along Z, attached to body)
   - Tip chord: ~0.03m
   - Span: ~0.06m (radial distance from body surface)
   - Sweep angle: ~30° (leading edge swept back)
2. **Airfoil cross section (mandatory):** The fin must NOT be a flat slab of uniform thickness. Give each fin a proper airfoil-like profile:
   - Root thickness: ~4mm (thickest at the root chord center)
   - Tip thickness: ~1.5mm (tapers thinner toward the tip)
   - **Rounded leading edge:** The front edge of each fin should be rounded/bullnosed, not a sharp knife edge. Use a bevel (~1mm radius) or shape the leading-edge vertices into a smooth arc.
   - **Tapered trailing edge:** The rear edge tapers to ~1mm thickness.
   - Build this by extruding the 2D trapezoid, then scaling/moving vertices to create the thickness gradient, or by defining the airfoil cross-section profile directly and lofting it along the span.
3. **Fillet at body junction (mandatory):** Add a smooth blend/fillet where each fin's root meets the body cylinder surface. Target ~2mm radius fillet. Use a bevel on the root edge vertices or add a small bridging geometry strip. This prevents the unrealistic sharp right-angle junction.
4. **Structural spar channel:** Add a shallow groove running along each fin's center span (from root to ~80% of tip), ~1mm deep and ~3mm wide. This simulates the internal spar visible on real model rocket fins. Use a loop cut along the fin center + inset, or a boolean cut.
5. Bevel remaining sharp edges ~0.5mm for realism.
6. Duplicate and rotate at 0°, 90°, 180°, 270° around Z axis.
7. Join all 4 fins into a single mesh object (`bpy.ops.object.join()`).
8. Center the combined fins part at world (0, 0, 0.04).

## Important

- Each fin root sits flush against the body cylinder surface at radius ~0.04m.
- Fin tip extends outward to ~0.08m from center.
- The combined 4-fin part's bbox should be roughly 0.16×0.16×0.10m.
- Smooth shade the fin surfaces, sharp edges on the leading/trailing edges.

## Mandatory geometry detail requirements

These features are **required**, not optional. The critic judges geometry_detail separately:

- **Airfoil cross section:** Root ~4mm thick tapering to ~1.5mm at tip, with a rounded leading edge and tapered trailing edge. No uniform-thickness slabs.
- **Body-fin fillet:** ~2mm radius blend at the root-body junction on each fin.
- **Spar channel:** Shallow groove (~1mm deep, ~3mm wide) along the center span of each fin.

Flat rectangular plates without thickness variation will score below 0.4 on the geometry_detail criterion.

## Texture

Write `texture_fins(obj)` with a dark material (Base Color: #1A1A1A, Roughness: 0.5, Metallic: 0.1). The UV atlas tool replaces this later.
