# Modern high-bypass turbofan jet engine (cutaway)

A maximally realistic cutaway model of a modern high-bypass commercial turbofan engine in the class of the Pratt & Whitney PW1100G-JM or CFM LEAP-1A (the engine fitted to the A320neo / 737 MAX family). The geometry MUST expose internal machinery the way a museum cutaway diagram does — outer nacelle partially open, internal spool sections visible from front fan disk all the way through to the tail cone.

## Research expectations

Coding agents working on this object MUST actively use WebSearch / WebFetch to obtain authoritative numbers before writing geometry. Authoritative sources include Wikipedia articles for PW1000G / CFM LEAP, the manufacturer product pages (pw.utc.com, cfmaeroengines.com), NASA technical memoranda (NTRS), EASA/FAA Type Certificate Data Sheets, and Jane's Aero Engines. Numbers to retrieve and feed into design.json + per-part code: fan diameter, overall length, nacelle barrel diameter, fan blade count, OGV count, LPC/HPC stage counts, HPT/LPT stage counts, fuel-nozzle count, bypass ratio. Realism over speed: spend the research budget.

## Parts and roles

- **Pylon** — wing-mount strut, root link of the URDF; everything else hangs off this.
- **Nacelle** — outer aerodynamic fairing (aluminum-gray), partially cut away on one side to expose the core. Fixed to Pylon.
- **IntakeLip** — chrome-finish inlet cowl ring at the front face of the nacelle. Fixed to Nacelle.
- **FanCase** — containment ring around the fan, slightly larger OD than the core. Fixed to Nacelle.
- **Spinner** — pointed nose cone in front of the fan disk, rotates with the fan. Fixed to FanDisk.
- **FanDisk** — fan hub holding all fan blades. Rotates with the LP spool around the engine longitudinal axis.
- **FanBlade** — TEMPLATE (18 instances) — titanium wide-chord fan airfoils mounted to FanDisk.
- **OutletGuideVane** — TEMPLATE (40 instances) — stator vanes behind the fan, fixed to Nacelle.
- **LPCompressor** — 3-stage low-pressure booster spool drum aft of the fan. Fixed to FanDisk (same LP spool).
- **HPCompressor** — 8-stage high-pressure compressor spool drum. Rotates independently (HP spool, separate continuous joint).
- **CombustorCasing** — annular combustor outer shell, hot-zone discolored. Fixed to Nacelle.
- **FuelNozzle** — TEMPLATE (16 instances) — radial fuel-nozzle bosses around the combustor upstream face. Fixed to CombustorCasing.
- **HPTurbine** — 2-stage high-pressure turbine disks downstream of combustor, charcoal/burnt-orange hot-zone tint. Fixed to HPCompressor (same HP spool).
- **LPTurbine** — 3-stage low-pressure turbine drum. Fixed to FanDisk (same LP spool — drives the fan through the through-shaft).
- **ExhaustCone** — matte-black tail plug, tapering aft of the LPT. Fixed to Nacelle.
- **ExhaustNozzle** — rear nozzle ring at the exit plane. Fixed to Nacelle.

## Joints

- LPSpool: **continuous** joint, parent=Nacelle, child=FanDisk, axis=(0, 1, 0) (engine longitudinal axis, +Y). Spinner / FanBlade / LPCompressor / LPTurbine ride on this spool via fixed joints.
- HPSpool: **continuous** joint, parent=Nacelle, child=HPCompressor, axis=(0, 1, 0). HPTurbine fixed to HPCompressor.
- All other connections are **fixed** joints.

## Rest pose

LP spool rotated +15° about engine axis; HP spool rotated -20° about engine axis. This visibly stagger-offsets the two spools relative to each other so the viewer can see they are independent. Spinner front-tip points in the -Y direction (engine front).

## Approximate dimensions (PW1100G-class, in cm)

- Fan diameter: ~206 cm
- Nacelle barrel OD: ~225 cm; cutaway exposes one ~120° azimuthal sector down one side
- Overall length (intake lip to exhaust nozzle exit): ~340 cm
- Core (HPC + combustor + HPT) OD: ~70 cm
- LPC drum OD: ~90 cm
- Tail-cone length: ~60 cm tapering from 50 cm OD at front to 8 cm at the aft tip
- Pylon: ~120 cm long, airfoil cross-section ~60 cm × 15 cm

## Clearance / fit

3-5 mm gap between FanCase ID and FanBlade tips. 2-3 mm gap between rotating spool drums and the adjacent stator casings. The two spools (LP, HP) are concentric and must not overlap geometry.

## Color hints

- Nacelle, IntakeLip, FanCase: aluminum-gray (matte aluminum), IntakeLip slightly more polished
- Spinner: glossy white with a small black spiral marker stripe (bird-strike visibility)
- FanBlade: brushed titanium, faint mid-blue heat tint near root
- FanDisk, LPCompressor, HPCompressor: machined steel / Inconel silver
- CombustorCasing: heat-discolored Inconel with rainbow oxidation
- HPTurbine, LPTurbine: charcoal black with burnt-orange and straw-yellow heat tinting concentrated near disks
- ExhaustCone: matte black, soot-darkened
- ExhaustNozzle: dark gunmetal
- Pylon: nacelle-matching aluminum-gray; underside slightly darker (shadowed fairing)
- OutletGuideVane: brushed aluminum
- FuelNozzle: machined Inconel silver with carbon-blackened tips

## Coordinate convention

Meters, Z up, -Y is the engine front (intake faces -Y). Engine longitudinal axis is Y. Pylon extends upward (+Z) from the nacelle to the wing mount.

## TEMPLATE PARTS

- FanBlade: 18 instances; rotation_euler around +Y axis at 20° spacing — radians [(0,0,0),(0,0.349,0),(0,0.698,0),(0,1.047,0),(0,1.396,0),(0,1.745,0),(0,2.094,0),(0,2.443,0),(0,2.793,0),(0,3.142,0),(0,3.491,0),(0,3.840,0),(0,4.189,0),(0,4.538,0),(0,4.887,0),(0,5.236,0),(0,5.585,0),(0,5.934,0)]
- OutletGuideVane: 40 instances; rotation_euler around +Y axis at 9° spacing — radians [(0, i*0.157, 0) for i in 0..39]
- FuelNozzle: 16 instances; rotation_euler around +Y axis at 22.5° spacing — radians [(0, i*0.393, 0) for i in 0..15]
