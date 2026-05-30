---
name: topos_blender_pitfalls
description: Cross-cutting catalog of Blender Python API traps that produce silently-wrong geometry — bug patterns that don't fit cleanly inside any single per-task skill (part geometry / joints / textures) but bite agents writing bpy code regardless of which part they're working on. Each entry is a concrete before/after recipe distilled from a real Topos run failure.
when_to_use: Any AgentTask that writes Blender Python code (build_<part>(), texture_<part>(), src/build.py). Especially relevant when the agent is composing primitives via repeated bpy.ops.* calls and chaining transform_apply / modifier_apply on the resulting objects. Single-primitive single-call tasks rarely hit these; multi-step composite parts hit several.
provides:
  - transform_apply default-bake trap: passing only one keyword causes the others (especially location) to default to True and silently rewrite mesh data
  - selection vs active object trap: bpy.ops operates on the operator context (selection / active), not on the python variable you have a reference to
  - bevel modifier order trap: applying bevel before join produces incoherent results; applying after join can swallow small features
  - Y-up vs Z-up trap: Topos authors in Z-up; glTF viewers default to Y-up; URDF / ROS / RViz expect Z-up
  - primitive_add side-effect trap: primitive_*_add selects + activates the new object AND deselects previously-active, breaking active-driven loops
  - L/R asymmetric fix-loop trap: judge stochasticity on mirror-symmetric pairs gives the two sides different scores; fix-loop only refines the failing side; final output has visibly asymmetric L/R parts. Workaround: mirror-via-delegate recipe with obj.location.x negation + view_layer.update().
related_skills:
  - topos_part_geometry     # the bbox contract / coordinate convention; these are how you author. This SKILL is how the author trips itself.
  - topos_geometry_contracts # detects the symptoms (fill-ratio / inter-part collision / attachment / cavity-opening)
  - topos_mesh_islands       # detects within-part outliers — the most common surface symptom of the traps below
---

# Topos: Blender API Pitfalls

A focused catalog of bugs that emerge from misusing Blender's Python API in plausible-looking ways. Each entry follows the same shape: **symptom → cause → fix → diagnostic**.

This SKILL is **agent-facing** (not a debugging log for humans — that's `docs/lessons.md`). The traps cataloged here are ones we've seen agents trip into across multiple Topos runs. Read this **before writing any composite-part build code**.

---

## Trap 1 — `transform_apply` defaults silently bake `location`

**Symptom.** A composite part (typically the last sub-primitive in a long `build_<part>()` function) ends up at the wrong world position — typically way out along one axis, while every other sub-primitive in the same part is fine. The `topos_mesh_islands` contract flags it. Renders show a small floating clump dozens of cm from the part's main mass.

**Root cause.** `bpy.ops.object.transform_apply` defaults all three keyword args to `True`. Code like:

```python
bpy.ops.object.transform_apply(rotation=True)              # ❌ silently bakes location too
bpy.ops.object.transform_apply(scale=True)                 # ❌ ditto
bpy.ops.object.transform_apply(rotation=True, scale=True)  # ❌ ditto
```

…passes `location=True` by default. After the call, `obj.location` is reset to `(0, 0, 0)` and the previous translation is folded into mesh vertex coordinates. If a *later* `obj.rotation_euler = (angle, 0, 0)` + `transform_apply` happens, the rotation now spins the mesh around the world origin (not the object pivot you mentally placed at `loc`), because the mesh data sits at world-displaced coordinates and the object's local origin is at (0, 0, 0). For a vertex at world Z = 1.625, rotating 30° around X about the world origin yields Y = -0.81 — exactly the failure mode observed in `pelvis.py` (ridge bars at world Y=0 → Y=-0.86 after the unintended location-bake).

**Fix.** Always pass all three keyword args explicitly:

```python
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)   # bake only scale
bpy.ops.object.transform_apply(location=False, rotation=True,  scale=False)  # bake only rotation
bpy.ops.object.transform_apply(location=False, rotation=True,  scale=True)   # bake rotation + scale (the most common)
bpy.ops.object.transform_apply(location=True,  rotation=True,  scale=True)   # full bake (do this once you're done positioning AND you intentionally want world coords baked in)
```

**Pre-write check.** Grep your draft: `grep -nE "transform_apply\(" parts/<your>.py | grep -vE "location\s*=\s*(True|False)"` — any line that hits is a default-bake bomb.

**Diagnostic.** If `topos_mesh_islands` reports a `[FLOATING_WARN]` and you can't find a wrong coordinate in source, look for a bare `transform_apply(...)` call.

---

## Trap 2 — `bpy.ops.*` operates on the operator context, not your variable

**Symptom.** Code adds objects in a loop. Each iteration calls `bpy.ops.object.transform_apply(...)` or `bpy.ops.object.modifier_apply(...)` on what you think is the current object. The result: the wrong object gets modified, or `modifier_apply` errors with "context is incorrect: active object not in mode 'OBJECT'", or earlier objects in the loop get re-transformed.

**Root cause.** `bpy.ops.*` reads `bpy.context.active_object` and `bpy.context.selected_objects`. After `primitive_cube_add`, the new cube is the active object AND the only selected object. But operations like `bpy.data.objects.new(...)` or `bm.to_mesh(...)` do NOT change selection or active — so subsequent `bpy.ops.*` runs against whatever was selected/active before, not the newly-created data.

**Fix.** Either set selection + active explicitly before every `bpy.ops.*`:

```python
obj = bpy.data.objects.new("skirt", mesh)
bpy.context.collection.objects.link(obj)
bpy.ops.object.select_all(action='DESELECT')
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
```

…or use direct mesh edits (`obj.data.transform(matrix)`) instead of operators when you have a direct python reference.

**Diagnostic.** "modifier_apply error: object not in OBJECT mode" or "context is incorrect" → almost always means active object is something other than what you expect. Add a `print(bpy.context.active_object.name)` right before the failing call to confirm.

---

## Trap 3 — Bevel modifier on the JOINED object swallows fine detail

**Symptom.** A composite part has small bolts / pins / rivets at the end. After applying a bevel modifier to the JOINED part, those small features appear smaller, fuzzed, or vanish entirely in renders.

**Root cause.** A bevel of width `w` removes geometry within `w` of each beveled edge. For a 3mm bolt cap (1.5mm half-extent) and a bevel width of 3mm, the bevel consumes the entire cap. The reverse-order pattern (bevel each sub-primitive before join) gives clean results but creates many more polygons.

**Fix.** Either:
- Tighten bevel width to ~1-2mm (default for furniture-grade hardware), OR
- Use `bevel.limit_method = 'ANGLE'` with `angle_limit = math.radians(30)` so only sharp edges get bevelled (small bolt edges are usually 90° but the contact between bolt and panel is shallow, and angle-limited bevel often ignores it), OR
- Bevel sub-primitives individually before join when you have ≤ 4 sub-pieces, OR
- Mark small features' edges with `e.use_edge_bevel_weight = 0.0` and use `bevel.use_edge_bevel_weight = True` to exclude them.

**Diagnostic.** Render the part WITHOUT the bevel modifier first to confirm the small features are there. Then add bevel and compare.

---

## Trap 4 — Z-up authoring, Y-up viewer

**Symptom.** The exported `object.glb` opens in a glTF viewer (Three.js / online-gltf-viewer / Blender's own GLB import) tipped onto its back — what should be "up" points sideways, what should be "front" points at the floor.

**Root cause.** Topos / robotics convention is **Z-up, -Y front**. glTF specification convention is **Y-up, -Z front**. The Blender glTF exporter has `export_yup` to do the Z-up → Y-up conversion. If you set `export_yup=False`, the file remains Z-up but every Y-up viewer treats incoming axes as Y-up regardless of metadata, so the model lies on its back.

**Fix.** In the export wrapper, the **whole-scene** GLB (the user opens this in a viewer) uses `export_yup=True`. The **per-part** GLBs that ship with the URDF use `export_yup=False` because URDF / ROS / RViz expect Z-up. Same exporter, two different consumers.

**Diagnostic.** If `object.glb` looks horizontal when it should be vertical, check the `export_yup` flag for the file you actually loaded.

---

## Trap 5 — `primitive_*_add` re-shuffles active + selection mid-loop

**Symptom.** A loop creates 4 sub-features each iteration. After the loop, only the LAST iteration's piece has the right material / modifier; the rest are unmaterial'd or unmodified. Or: a `bpy.ops.object.transform_apply` after the loop bakes only the last cube.

**Root cause.** `primitive_*_add` makes the new object active AND deselects everything else. If your loop relies on a previously-active object remaining active across iterations, that assumption breaks. If your post-loop code calls a `bpy.ops.*`, it runs against only the LAST iteration's active.

**Fix.** Capture the python reference explicitly inside the loop and use direct data access (not `bpy.context.active_object`) outside:

```python
created = []
for _ in range(4):
    bpy.ops.mesh.primitive_cube_add(...)
    obj = bpy.context.active_object              # capture the reference NOW
    obj.scale = (...)
    bpy.ops.object.transform_apply(              # explicit location=False
        location=False, rotation=False, scale=True,
    )
    obj.data.materials.append(mat)               # direct, no operator
    created.append(obj)                          # store reference

# After loop — operate on each via python reference, not bpy.ops on selection:
for o in created:
    bevel = o.modifiers.new(name="b", type='BEVEL'); bevel.width = 0.002
    # If applying modifier: deselect-all + select-this + active-this BEFORE bpy.ops.object.modifier_apply
```

---

## Trap 6 — L/R asymmetric fix-loop on mirror-symmetric parts

**Symptom.** A model whose anatomy is supposed to be left/right mirror-symmetric (humanoid robot, chair with two armrests, vehicle with two wheels) ships with the two sides looking obviously different. One side has 4 PBR materials + 3 sub-features + bevels; the other side is a bare primitive. Renders look "half-finished" even though every per-part judge ultimately passed.

**Root cause.** Per-part judges are LLM critics with stochastic scoring. A pair like `LeftShin` / `RightShin` is built by **two independent agent tasks** in iter 0 — same prompt, same SKILL set, but different LLM samples. They produce structurally similar but not identical geometry. At iter-0 judge time, the two sides get *different scores* (e.g. LeftShin 0.63 → pass, RightShin 0.53 → fail). The fix-loop builds a fix task **only for the failing side**, which spends 2-3 more iters adding detail (PBR materials, sub-features, decals). The passing side stays at its iter-0 form. End state: structurally divergent L/R pair on a model whose anatomy demanded symmetry.

A real Topos example (optimus_prime_bay_v1, May 2026): inverse asymmetric pattern across `Foot` and `Shin` pairs:

```
left_foot.py   9.3 KB  (iter-0 score 0.59 → iter-1 fix → toe cluster + heel spur)
right_foot.py  4.7 KB  (iter-0 score 0.63 → no fix → basic boot box)
left_shin.py   3.9 KB  (iter-0 score 0.63 → no fix → frustum shell only)
right_shin.py  9.9 KB  (iter-0 score 0.53 → iter-1 + iter-2 fixes → 4 PBR mats + knee pad + pistons + flame decals)
```

Final render: every leg sub-feature visible on exactly one side of the body. From the front the model looks like two characters fused together.

**Fix.** **Mirror-via-delegate** — rewrite the simpler side as a thin shim that calls the refined side's build function and mirrors the result across the X=0 plane. The mirror operation must do **all three** of:

1. Negate every mesh vertex's local X coordinate.
2. Flip face winding (`face.normal_flip()` for every face), so the mirrored mesh's normals continue pointing outward — otherwise shading goes inside-out and the part renders transparent.
3. **Negate `obj.location.x`** AND call `bpy.context.view_layer.update()`. Steps 1+2 only mirror in OBJECT-LOCAL space. Baked-mode parts often have a non-zero object pivot (built via `primitive_*_add(location=...)` which the original code never zeroed). Without step 3 the mirrored mesh ends up at the SAME world position as the source — observed symptom is `LeftFoot` and `RightFoot` z-fighting on the same side of the body, so one of them appears to "disappear" in the GLB. Without `view_layer.update()` Blender's depsgraph caches the pre-mirror `matrix_world`, and downstream contract checks (collision / attachment) report spurious 100% overlap.

Canonical recipe (drop into the simpler side's `parts/<lower>.py`, replacing all prior build code):

```python
"""LeftShin — X-mirror delegate of RightShin (see topos_blender_pitfalls Trap 6)."""
import bpy
import bmesh


def _mirror_x(obj):
    """Mirror obj across world X=0: negate vertex X + flip face winding +
    negate obj.location.x + flush depsgraph."""
    mesh = obj.data
    for v in mesh.vertices:
        v.co.x = -v.co.x
    bm = bmesh.new()
    bm.from_mesh(mesh)
    for face in bm.faces:
        face.normal_flip()
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj.location.x = -obj.location.x
    bpy.context.view_layer.update()


def build_left_shin():
    from parts.right_shin import build_right_shin
    obj = build_right_shin()
    obj.name = "LeftShin"
    _mirror_x(obj)
    return obj
```

This guarantees bit-for-bit symmetry by construction. Every refinement the refined side receives is inherited automatically. Materials are shared via Blender's name-clash auto-suffix (`RightShin_red.001`) — visually identical, slight memory waste; rename if cosmetics matter.

**Diagnostic.** Two cheap checks:

1. `ls -la src/parts/{left,right}_<part>.py` — file sizes >2× different on a pair that's anatomically symmetric = asymmetric fix likely happened.
2. `ls trajectories/ | grep 99_agent_fix_part_<base> | sort` — count fix iterations per side. Imbalance (e.g. left has 2, right has 0) confirms asymmetric refinement.

The user-visible smoke test after applying the recipe: in any front T-pose render, both sides of the body should look identical. Run `obj.matrix_world.to_translation()` on both `LeftX` and `RightX` after build — locations should be exactly `(±a, b, c)` with mirrored X sign.

**Note: this is a tactical workaround, not a root-cause fix.** The root cause is that the runner's fix-loop has no notion of "mirror-pair groups". A durable framework fix would either (a) detect `LeftX`/`RightX` name pairs and auto-propagate fix-task changes via mirror, or (b) have the design agent emit mirror pairs as a single canonical part with `instances: [{scale: [-1, 1, 1]}]`. The mirror-via-delegate recipe is what an agent applies in `parts/<lower>.py` *until* one of those framework fixes lands.

---

## When this SKILL grows

This file is **append-only catalog** style. When a new trap is found in the wild (some agent's run mysteriously produced wrong geometry, root-caused to a Blender API quirk that isn't the agent's actual semantic error), add a Trap entry following the same `Symptom → Root cause → Fix → Diagnostic` format.

**Inclusion criteria** (raise the bar — keep this SKILL crisp, not a junk drawer):
- The trap has been observed in a Topos run (not theoretical)
- The fix is a small, mechanical code change (not "rethink the design")
- The Blender API surface is the proximate cause (otherwise it belongs in `topos_part_geometry` or similar)

**Exclusion criteria** (these go elsewhere):
- Generic "use bevel for chamfers" tips → `topos_part_geometry`
- Specific geometry recipes (NACA grille, fluted column) → `topos_furniture_hardware`
- Cross-session debugging notes for humans → `docs/lessons.md`
