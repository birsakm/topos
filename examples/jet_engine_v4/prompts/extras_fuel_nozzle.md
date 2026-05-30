TEMPLATE PART — 16 instances. Tell the design agent to add an `instances` field with rotation_euler values spaced 22.5° apart around +Y: 16 entries of the form (0, i*0.393, 0) for i in 0..15. The part agent writes ONE canonical `build_fuel_nozzle()` at the canonical position (mounted on the combustor upstream face, projecting axially aft into the chamber); the build agent copies and rotates per instance.

**Reference anchor.** TALON-X or CFM TAPS dual-orifice fuel nozzle — a machined Inconel injector body with a flanged mounting boss, a stem ~8 cm long, and a small flared spray-tip with two concentric orifices (pilot and main). Carbon-blackened at the tip from combustion soot.

- Geometry strategy: a stepped cylinder. (1) Mounting flange disk OD ~6 cm × 1 cm thick at the upstream end. (2) Cylindrical stem ~3 cm OD × ~8 cm long projecting downstream. (3) Slightly flared tip with two concentric annular orifices visible on the downstream face (boolean two ring grooves into a slightly bulbous tip). Add 4 visible Phillips/hex bolt heads on the mounting flange.
- Mandatory features: visible stepped profile (flange / stem / tip — NOT a uniform cylinder), two concentric orifice rings on the tip, 4 bolt heads on the flange.
- Color: machined Inconel silver, transitioning to carbon-black at the tip.
- ANTI-pattern: do NOT model as a smooth featureless cylinder. The stepped flange/stem/tip silhouette is what reads as an injector rather than a peg.
