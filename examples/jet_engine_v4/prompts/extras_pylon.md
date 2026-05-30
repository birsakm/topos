**Reference anchor.** Airbus A320neo wing-to-engine pylon (the airfoil-section strut that bolts the PW1100G/LEAP-1A nacelle to the wing underside). Look up images of an A320neo engine pylon — it's a deep, narrow, airfoil-shaped fairing that tapers from a wider wing-attach root down to a narrower engine-attach foot, with two visible aluminum service-access panels on the side and a thermal-blanket dark patch on the underside near the engine.

- Geometry strategy: extruded airfoil profile (NACA 0021-ish symmetric section, ~60 cm chord, ~15 cm max thickness) lofted vertically over ~120 cm, with a chord taper from ~70 cm at the wing-attach top to ~55 cm at the engine-attach bottom. Add a small fillet/blend at both ends.
- Mandatory features: airfoil cross-section (NOT a rectangular box), visible fwd/aft fairing seam line running vertically along the leading and trailing edges, two recessed service-panel rectangles (~25 cm × 15 cm each) on the outboard side, a small thermal-blanket inset on the underside surface near the engine end.
- Bevel: 8 mm radius on the wing-attach top corners.
- Color: aluminum-gray matching the nacelle; underside fairing slightly darker (shadowed).
- ANTI-pattern: do NOT model as a rectangular box. The pylon must be a recognizable airfoil-section strut.
