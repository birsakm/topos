---
name: topos_design_articulated
description: How to author design.json for articulated objects — parts, joints, clearance, rest pose, and articulated decomposition patterns
when_to_use: Any AgentTask that writes the top-level design.json for an articulated project (rigid-only projects use a simpler subset)
provides:
  - design.json schema for articulated objects
  - rest-pose convention (rendering-friendly)
  - clearance conventions (2-5mm for furniture; 0 for welded assemblies)
  - parts decomposition heuristics
  - joint axis conventions (-Y front, Z up)
related_skills:
  - topos_part_geometry
  - topos_joints_creator
---

# Topos: Design Articulated

This skill teaches the methodology for translating a natural-language description of an articulated object into a frozen `design.json` contract that every downstream task will read.

## What `design.json` is

A machine-readable contract describing the parts, their world placement, and the joints connecting them. **It does not contain code** — code is each part's `build_<name>()` function. design.json is what links the part-agents' parallel work together: every part agent reads the same design and implements its slice.

## Coordinate conventions

- **Coords in meters, Z is up.** A 30 cm cabinet centered at 15 cm above the floor has `world_xyz = [0, 0, 0.15]` and `world_extents = [0.30, 0.30, 0.30]`.
- **-Y is "the front"** by Topos convention. Drawers slide along `[0, -1, 0]` to come forward; doors hinge so opening pulls them toward -Y.
- **Centered**, not corner-anchored: `world_xyz` is the bbox CENTER, `world_extents` is the FULL size along each axis (not half-extents).

## Schema

```jsonc
{
  "robot_name": "<machine-readable slug>",
  "description": "<one-line NL description>",
  "parts": [
    {
      "name": "<PascalCase>",                       // links the bpy object + URDF link
      "role": "<one-line NL role>",
      "geometry_strategy": "<advisory hint>",       // see topos_part_geometry
      "world_xyz": [cx, cy, cz],                    // bbox CENTER in meters
      "world_extents": [w, d, h],                   // full size along X / Y / Z
      "color_rgba": [r, g, b, a],
      // Optional per-part fields:
      "cavity": { "world_xyz": [...], "world_extents": [...], "open_axis": "-Y" },
      "wall_thickness": 0.015,
      "outer_bevel_radius": 0.004,
      "front_inset_depth": 0.004,
      "front_inset_margin": 0.012,
      "instances": [                                 // optional — template + N copies
        {"rotation_euler": [rx, ry, rz]},           //   each entry: rotation_euler and/or translation
        {"rotation_euler": [rx, ry, rz], "translation": [tx, ty, tz]}
      ]
    }
    // ... more parts
  ],
  "joints": [
    {
      "name": "<joint_name>",
      "type": "prismatic|revolute|fixed|continuous",
      "parent": "<PascalCase part name>",
      "child":  "<PascalCase part name>",
      "axis":   [x, y, z],                          // only non-fixed
      "limit_from_rest": [lower, upper]              // only prismatic/revolute. Joint pos 0 == rest.
    }
  ]
}
```

## The `instances` field — template + N placements

For repeated identical features (4 chair legs, 6 fan blades, 12 spokes), DO NOT enumerate as N separate parts. Use ONE part with an `instances` array. The part agent writes ONE canonical builder; the build agent copies + transforms per instance.

```jsonc
{
  "name": "FanBlade",
  "lower_name": "fan_blade",
  "world_xyz": [0.0, -0.4, 0.0],            // bbox of ONE canonical instance
  "world_extents": [0.05, 0.08, 0.45],
  "geometry_strategy": "airfoil-template",
  "instances": [
    {"rotation_euler": [0.0, 0.000, 0.0]},  //   0° around +Y
    {"rotation_euler": [0.0, 1.047, 0.0]},  //  60°
    {"rotation_euler": [0.0, 2.094, 0.0]},  // 120°
    {"rotation_euler": [0.0, 3.142, 0.0]},
    {"rotation_euler": [0.0, 4.189, 0.0]},
    {"rotation_euler": [0.0, 5.236, 0.0]}   // 300°
  ]
}
```

- Each instance dict: `rotation_euler` (XYZ radians) and/or `translation` (XYZ meters). Identity if omitted.
- `world_xyz` / `world_extents` describe the **canonical** instance (one blade), not the cluster.
- build.py constructs the canonical mesh once, then `.copy()` + apply transform per instance. N instances become N scene objects named `<PascalName>_0`, `<PascalName>_1`, ...
- One agent task per template — cost scales O(1) with N instead of O(N).

**When to use:**
- Rotational symmetry: fan/turbine blades, propellers, wheel spokes
- Translational arrays: chair legs (4× translation), shelves, drawer rows, cylinder banks
- Mixed: cylinder bank with per-cylinder rotation (uniform spacing + per-position rotation)

**When NOT to use:**
- Distinct functional roles (fan blade vs turbine blade are different airfoils → separate parts)
- Mirror-symmetric pairs (instances doesn't support scale=-1 reliably)
- Variable details across copies (left side door has a handle, right doesn't → separate parts)

## Material choice — when to emit a `texture` field

The schema above shows `texture` as **optional**. This section tells you **when to populate it** and what to put in it. The default fallback (no `texture` field) attaches a flat Principled BSDF using `color_rgba` — that's a *deliberate* choice for plain plastic / single-tone paint, and a *failure mode* for anything where the surface itself is part of the visual identity (wood grain, hammered metal, woven cloth, glazed ceramic, …).

Decision table — read each part's `role` description and pick:

| Material described in role / prompt | Use | Why |
|---|---|---|
| Wood (any species: oak / walnut / rosewood / pine / bamboo) | **`texture: {kind: image, ...}`** | Grain pattern is non-negotiable for "looks like wood"; flat color reads as "painted MDF". One Gemini call ≈ $0.001. |
| Metal with surface detail (gilded, cast, hammered, engraved, antiqued brass) | **`texture: {kind: image, ...}`** | Ornate metallic hardware needs micro-detail; flat BSDF reads as "painted plastic". |
| Brushed metal (single direction), polished mirror chrome, simple anisotropic | `texture: {kind: procedural}` + procedural shader | Regular structure — `ShaderNodeTexNoise` + anisotropic BSDF nails it without an external image. |
| Fabric, leather, woven straw, carpet, denim, canvas | **`texture: {kind: image, ...}`** | Weave pattern is irreducible to procedural noise. |
| Stone, marble, granite, concrete, terracotta | **`texture: {kind: image, ...}`** | Veining / aggregate / pitting carries the realism. |
| Glass, transparent plastic | `color_rgba` only | Transmission + roughness in BSDF is sufficient; no image needed. |
| Painted single-tone surfaces (lacquer, enamel, gloss paint with no visible grain) | `color_rgba` only | Flat color IS the look. |
| Simple regular pattern (stripes, dots, even rings) | `texture: {kind: procedural}` | Shader nodes (`ShaderNodeTexWave`, `ShaderNodeTexChecker`) without an image. |
| Photoreal or "ornate / palace / luxury" descriptions | **`texture: {kind: image, ...}`** | If the human prompt uses words like *photoreal, ornate, antique, palace, luxury, weathered, rustic, embroidered* — that's a strong image-texture signal regardless of material class. |

**Heuristic shortcut.** Look at the part's `role` field. If you can describe the surface in **3 or fewer words and a number** (`"flat dark grey plastic"`, `"glossy 0.30/0.30/0.30"`), use `color_rgba` only. If the description uses **a material noun + adjectives** (`"rough walnut plank with visible end grain"`, `"polished gilded brass with floral motifs"`), emit an image texture.

When emitting `texture: {kind: image, ...}`:
- `prompt` — a *short* Gemini prompt focused on the surface itself. Start with `"seamless tileable"`, name the material, end with `"4k, top-down"`. Example: `"seamless tileable photorealistic dark walnut wood plank, prominent grain, 4k, top-down"`.
- `image_relpath` — under `src/textures/<part_lower>.png`. The framework's `generate_texture_image` ToolTask (one per part, dispatched after the part agent) will read `prompt` + `image_relpath` from your design.json and materialize the PNG via Gemini Nano Banana 2; the part-geom agent's `texture_<name>(obj)` then binds it.
- `material_hint` — a one-line cue the part-geom agent uses when wiring the Principled BSDF (roughness / metallic / specular tuning). Examples: `"rough walnut wood, semi-matte"`, `"polished brass, metallic=1.0, roughness=0.15"`.

**Cost reasoning.** A typical cabinet has 3–7 parts; if 4 of them get image textures, that's ~$0.004 in Gemini calls per run. This is dramatically cheaper than the per-iter agent cost ($0.50–$1.00 per part-agent), so don't be stingy with image textures when the role implies a real material — the visual return is large.

**Anti-pattern.** Do NOT emit a `texture` block "just to be safe" if `color_rgba` already captures the look (matte single-color paint, plain plastic). The fallback flat-BSDF path is fine and saves a Gemini call. The mistake the decision table fixes is the *opposite* one: omitting `texture` when the role clearly calls for a real material surface.

## Rest-pose choice — this is what gets rendered

Multi-view rendering and the rubric only see the **rest pose** (the configuration encoded by each part's `world_xyz`). Pick a rest pose that **shows the articulation**, not a static configuration:

- Drawer: **half-out the front**, so all 3 parts (frame, drawer, handle) are visible. Y-center of drawer ≈ half its Y-extent forward of cavity front.
- Door: ~30-45° ajar, so hinge axis and door panel both readable.
- Lid: open ~60°, hinge visible.
- Robotic arm: a non-collinear configuration so multiple links read distinctly.

**Anti-pattern**: drawer fully closed → looks like a solid block, judge can't tell it's articulated.

## Clearance conventions

- **Furniture-grade**: 2-5 mm between sliding parts (drawer ↔ cavity walls). Industrial-quality precision.
- **Welded/rigid**: 0 mm (parts touch exactly).
- **Robot/mechanism**: 1-3 mm depending on joint tolerance.

If a spec says "fits with clearance C", apply `C` per side: drawer_extent[axis] = cavity_extent[axis] - 2C for X and Z (Y is the joint axis, free-running).

## Decomposition heuristics

Decompose into the smallest number of parts that:
1. Each part is a **structurally coherent** sub-mesh (one connected blob; can be 5-panel-joined or solid).
2. Each pair of adjacent parts connects via a **single joint** (no multi-joint loops).
3. The decomposition matches the **functional intent**: drawer ≠ cabinet, handle ≠ drawer, even if they're visually attached.

For a drawer cabinet: Frame + Drawer + Handle = 3 parts is canonical. Don't merge Handle into Drawer (loses semantic separation for export_urdf to treat Handle as its own rigid link).

## Sanity-check before writing

- Drawer X+Z extents ≤ cavity X+Z extents − 2× clearance (the drawer fits)
- Rest pose Y-center puts roughly half the drawer's Y-extent inside the cavity, half outside (so renders show both)
- Handle's back face flush with the parent's **mounting surface** (no floating gap). If the parent has `front_inset_depth`, the mounting surface is the **recessed inset face**, not the outer rim — see "Mounting on a recessed inset face" below.
- All `world_extents` strictly positive
- `axis` close to a unit vector (or clearly one of the 6 cardinal directions)
- Every joint's parent and child both appear in `parts[]`
- Root part (whichever isn't a joint's child) is implicit; the framework handles it

## Mounting on a recessed inset face

When a parent declares a `front_inset_depth` (e.g. a drawer with a shallow recessed panel on its front face), any **fixed-joint child mounted on that face must sit on the recessed surface, not on the outer rim around it.** A handle screwed to the frame-ring of a recessed drawer panel reads visually as "floating in front of the inset" — even though the part technically touches the drawer, the alignment is wrong.

Mechanics: the recessed surface is `outer_face − front_inset_depth` along the parent's outward face normal. So the child's mounting-face coordinate must reach that depth, not stop at the outer rim.

Worked example (drawer with handle on -Y front face):

```
Drawer.world_xyz       = [0.0, -0.02, 0.15]      # bbox center
Drawer.world_extents   = [0.266, 0.260, 0.266]   # full size
Drawer.front_inset_depth = 0.004                 # 4 mm recess on -Y face
  → Drawer's outer -Y face plane  = -0.02 - 0.260/2 = -0.150  m
  → Drawer's RECESSED -Y plane    = -0.150 + 0.004 = -0.146  m  ← mount here

Handle.world_extents   = [0.10, 0.025, 0.025]    # 25 mm deep (along Y)
  → Handle's back coord must be at  -0.146  m  (NOT -0.150)
  → Handle.world_xyz[1] = -0.146 - 0.025/2     = -0.1585 m
```

A common mistake (this is exactly the failure the `[ATTACHMENT_INSET_WARN]` contract catches): setting `Handle.world_xyz[1] = -0.1625` so the handle's back lands at the OUTER rim (-0.150) instead of the inset (-0.146). The handle then visually "skims" the rim while the recessed inset gapes empty 4 mm behind it.

Whenever you write a joint with `type: "fixed"` and the parent has any `*_inset_depth` field, do this arithmetic explicitly when choosing the child's `world_xyz`. Document the offset in the part's `role` field if useful (e.g. "mounted flush with Drawer's recessed inset panel, not the outer frame ring").

## Process

1. Read the NL intent (typically in `examples/<slug>/prompts/intent.md`).
2. Pick the part decomposition (typically given in the intent).
3. Solve the geometry: each part's `world_xyz` and `world_extents` consistent with the clearance + rest pose constraints.
4. Solve the joints: `axis` from the intent (front-facing → `[0,-1,0]`); `limit_from_rest` from the design's range.
5. Use the Write tool to create `src/design.json`. Output valid JSON only — no commentary, no comments inside JSON.

The downstream `topos_part_geometry` and `topos_joints_creator` skills handle implementing the parts and writing joints.yaml respectively from this contract.
