# Nozzle — Geometry Extras

Build a **bell-shaped engine nozzle** with visible convergent-divergent geometry and structural detail.

## Strategy

1. Create a surface of revolution using bmesh or Mesh.from_pydata with a **clearly visible convergent-divergent (CD) profile**:
   - **Convergent section (top):** The nozzle entrance at the top should start at radius ~0.020m and narrow down to the throat. This converging intake section should span roughly the top 30% of the nozzle length.
   - **Throat:** The narrowest point, radius ~0.015m. The throat must be a clearly visible pinch/constriction in the profile — not a subtle inflection.
   - **Divergent bell section (bottom):** From the throat, the profile flares outward to the exit radius ~0.025m over the remaining 70% of the length, following the bell curve below.
   - Total length: ~0.06m.
2. Use ~32 circumference segments and ~16 height segments (more than the minimum 12, to support the ribs and throat detail).
3. Include BOTH inner and outer surfaces (the nozzle is visible from below, so the interior matters).
4. Wall thickness: ~2mm.
5. **Circumferential stiffening ribs on the outer surface (mandatory):** Add 3 raised rings on the outside of the nozzle bell, evenly spaced along the divergent section. Each rib should be ~1mm proud of the outer surface and ~2mm tall along the Z axis. Use loop cuts on the outer shell + scale outward, or extrude ring faces outward. These simulate the structural stiffeners visible on real engine bells.
6. **Throat ring detail (mandatory):** On the inner surface, the throat ring should be slightly raised/proud (~0.5mm inward from the inner wall profile) to represent the throat insert. This means at the throat Z-height, the inner wall radius is ~0.5mm smaller than the smooth inner profile would be, creating a visible ridge when viewed from below.
7. Smooth shade the nozzle. Mark rib edges and the throat ring as sharp.

## Bell Profile

Approximate a Rao contour or simple parabolic flare for the divergent section:
```
r(z) = r_throat + (r_exit - r_throat) * (z / L)^0.7
```
where z goes from 0 (throat) to L (exit). The exponent < 1 gives the characteristic rapid initial flare that levels off.

For the convergent section above the throat:
```
r(z) = r_inlet - (r_inlet - r_throat) * (z / L_conv)^2
```
where z goes from 0 (inlet top) to L_conv (throat), and r_inlet ~0.020m. The quadratic gives a smooth contraction.

## Important

- Nozzle center at (0, 0, -0.04). The throat (top) meets the bottom of the Body.
- The nozzle can gimbal, so it's a separate object with its own origin at the throat center (0, 0, -0.01).
- Keep inner surfaces — the nozzle should look hollow when viewed from below.

## Mandatory geometry detail requirements

These features are **required**, not optional. The critic judges geometry_detail separately:

- **Convergent-divergent profile:** The nozzle must show a clearly visible convergent intake narrowing to a distinct throat, then diverging into the bell. A simple straight cone or monotonic flare is not acceptable.
- **Outer stiffening ribs:** 3 circumferential raised rings on the outer surface of the bell section, each ~1mm proud.
- **Throat insert ring:** The inner surface at the throat must have a slight raised ridge (~0.5mm proud inward), visible when viewed from below.

A simple flared cone without a visible throat constriction or surface ribs will score below 0.4 on the geometry_detail criterion.

## Texture

Write `texture_nozzle(obj)` with a metallic material (Base Color: #8C7B6B, Roughness: 0.35, Metallic: 0.8). The UV atlas tool replaces this later.
