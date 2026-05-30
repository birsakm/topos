TEMPLATE PART — 40 instances. Tell the design agent to add an `instances` field with rotation_euler values spaced 9° apart around +Y: 40 entries of the form (0, i*0.157, 0) for i in 0..39. The part agent writes ONE canonical `build_outlet_guide_vane()` at the canonical orientation; the build agent copies and rotates per instance.

**Reference anchor.** Modern turbofan outlet guide vane (OGV) — a thin cambered aerofoil stator vane sitting just aft of the fan in the bypass duct, straightening the swirling bypass airflow. On a PW1000G these are aluminum/composite vanes mounted between the inner core casing and the outer fan case.

- Geometry strategy: cambered NACA 4-digit aerofoil cross-section (e.g. NACA 4412) extruded radially with NO twist (stator, unlike the rotor blades). Span ~30 cm, chord ~15 cm, max thickness ~2 cm.
- Mandatory features: cambered (asymmetric) aerofoil cross-section, mounting flange at both ends (inner end mates to engine core casing, outer end mates to FanCase ID), set at a stagger angle of ~30° relative to the engine axis.
- Color: brushed aluminum.
- Bbox: 40 instances arranged in a ring at radius ~80 cm from engine axis, axially positioned ~70 cm aft of FanDisk.
- ANTI-pattern: do NOT model as a flat plate. The camber is what makes it function as a flow straightener.
