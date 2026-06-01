---
name: topos_mesh_islands
description: Drop-in build.py validator that catches disconnected mesh islands floating far from a part's main body — the failure mode where bolts / accent pieces / detail features end up at wrong world positions because of a transform bug
when_to_use: EXPERIMENTAL — see STATUS section. Don't wire this into a production build.py without validating no false positives on the specific project. The intent: any AgentTask that writes src/build.py for a project whose parts are COMPOSITE (built from multiple sub-pieces joined together with bmesh.ops.join or bpy.ops.object.join).
provides:
  - mesh-island contract (prototype): catches "spec said 'torso with 14 grille ribs and 24 bolts' but one of the bolts ended up at world origin instead of welded onto the torso" — when it works
related_skills:
  - topos_geometry_contracts  # fill-ratio + inter-part collision + cavity-fit, production-ready
  - topos_part_geometry       # bbox contract (per-part outer AABB) — coarser still
---

## STATUS: experimental — DO NOT use unmodified

Validation 2026-05-11 found two failure modes that make the heuristic unreliable in practice:

1. **"Largest island" baseline fails for composite parts.** Real parts produced by part agents use `bpy.ops.object.join` to merge many small `primitive_cube_add` sub-pieces. `join` doesn't weld, so each sub-cube is its own island (8 verts). There's no single "main mass island" — the bulk is dozens of small islands evenly distributed. Picking the biggest by vertex count returns a 1cm³ corner detail, and the reference bbox is tiny → everything else looks "outside" and gets flagged. Tested on the Optimus run produced 1000+ false positives.
2. **Min cluster verts filter conflicts with positive-case sensitivity.** A truly stray bolt is small (5-15 verts). The aggressive vert floor (50+) needed to suppress (1)'s noise also suppresses real stray-bolt detection.

A robust replacement needs:
- First *cluster* islands spatially (e.g. radius = 20cm), find the densest cluster, use ITS combined bbox as reference (not "largest single island").
- And distinguish "main mass" from "outlier" by spatial isolation, not just vertex count.

Until that lands, this SKILL.md stays as documentation of the intent. **Do not copy the worked code below into a real build.py without re-tuning** — it will produce noise.

In the meantime, the practical coverage is provided by `topos/prompts/system/fix_part.md.j2` instructing fix agents to Read the actual rendered PNGs when judge feedback mentions visual artifacts (floating / stray / intersect / z-fight / ...). The vision LLM judge has already caught such issues — the gap was that fix agents weren't seeing the same evidence. That's closed.

---

# Topos: Mesh-Island Integrity

## The failure mode this catches

A part agent writes `build_torso()` which composes 14 grille ribs + 24 hex bolts + a few panels via independent `primitive_cube_add` / `primitive_cylinder_add` calls, then joins them all into one Object named `Torso`. If ONE of those sub-primitives gets its transform wrong — e.g. `bpy.ops.transform.translate` applied with the wrong active object, or a coordinate computed from a stale variable — that sub-piece sits at world origin (or anywhere far from where it should be) while the rest of the torso is at z ≈ 2m.

The existing contracts miss this:
- **bbox contract** (`topos_part_geometry`): only checks the outer AABB of the joined Object. The stray piece *expands* the AABB, but a generous 5mm tolerance + the AABB being a single box means the warning is small or absent.
- **inter-part collision** (`topos_geometry_contracts`): only compares part-vs-part AABB overlap. A stray sub-piece is still part of `Torso`'s AABB, so this check is blind to within-part issues.
- **fill-ratio** (`topos_geometry_contracts`): about cavity volume, not connectedness.

The visual symptom in renders: "four short floating line segments to the right of the main mesh" — exactly what the judge writes when the bbox-good-but-mesh-bad failure manifests.

## The reasoning

Within a single Object's mesh, group vertices by edge-connectivity. Each group is one **mesh island**. A correctly-authored composite part has many islands (one per joined sub-primitive — that's normal because `join` doesn't weld). What's NOT normal is one island sitting far from the part's mass-weighted centroid.

**Key insight**: the part's own AABB can't reveal outliers, because an outlier always lives inside its own bounding box. The part's AABB *grows* to include the stray geometry, so distance-to-AABB-center is bounded by half-diagonal by definition. The fix: use the **largest island's bbox** as the reference, not the whole-part AABB.

Walk: group islands, take the largest by **AABB volume** (not vertex count — small bolts and screws get heavily subdivided by the bevel modifier and end up with more verts than the body cube, so vertex count picks the wrong "main"). Its world-space AABB defines "where the part's main mass lives". Expand by `MARGIN_FACTOR` (default 1.5×) to allow legitimate edge features (a bolt-strip running along the part's edge has its centroid slightly outside the main mass's bbox; the margin admits it). Any other island whose centroid sits **outside the margin-expanded bbox** is a true outlier — it's geometrically beyond where the bulk of the part is.

Then **cluster** flagged islands by centroid proximity (`CLUSTER_RADIUS_M = 0.05`, i.e. 5cm) — a single misplaced feature usually generates many flagged 4-vertex sub-meshes (each cube-add becomes one island). Without clustering, build stderr would drown in 100+ near-identical warnings. Per-cluster reporting gives the fix agent a small list of distinct locations.

Finally suppress clusters below `MIN_CLUSTER_VERTS = 50` total — most edge-cap noise sub-meshes have ≤ 8 verts each; a real misplaced sub-feature has multiple sub-cubes that aggregate well above this floor.

This is **deterministic, no-token, post-build**. Print `[FLOATING_WARN]` per outlier island. Like the other contracts, do NOT raise — render proceeds so the judge sees the visual mistake too. The WARN goes to Blender stdout, captured in the run trajectory, and (paired with the fix_part prompt instructing fix agents to Read render images) becomes useful fix-loop input.

## When to emit

| Check | Emit when |
|---|---|
| `mesh-island integrity` | any part in design.json describes itself as a COMPOSITE (text hints in design.json `description` mentioning "joined", "composed of", "with N sub-features"), OR the part's mesh has ≥ 2 islands at build time |

Single-primitive parts have 1 island always — the check is a no-op for them. Composite parts with all sub-pieces near the centroid pass silently. Only true outliers get flagged.

## Drop-in code

Add this block to `src/build.py` AFTER the bbox contract validation and AFTER the inter-part collision contract:

```python
# === mesh-island integrity contract (from topos_mesh_islands SKILL) ===
# Catches: a sub-feature (bolt / rib / accent) joined into a composite part
# ends up at the wrong world position. bbox contract is too loose to see
# this; inter-part collision is per-pair and ignores within-part state.
import bmesh
import mathutils

print("=== mesh-island integrity contract ===")
MIN_ISLAND_VERTS = 3       # ignore <3-vert noise islands
MARGIN_FACTOR = 1.5        # expand largest-island bbox by this factor before testing
CLUSTER_RADIUS_M = 0.05    # group nearby outliers into one cluster
MIN_CLUSTER_VERTS = 50     # only report clusters with ≥ N combined verts

for spec in DESIGN["parts"]:
    name = spec["name"]
    if name not in bpy.data.objects:
        continue
    obj = bpy.data.objects[name]
    if obj.type != "MESH" or not obj.data.vertices:
        continue

    # 1. Group verts by edge-connectivity → islands
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)  # world-space positions

    visited = set()
    islands = []  # list of list[vertex_world_coord]
    for v in bm.verts:
        if v.index in visited:
            continue
        stack = [v]
        verts_in_this = []
        while stack:
            cur = stack.pop()
            if cur.index in visited:
                continue
            visited.add(cur.index)
            verts_in_this.append(cur.co.copy())
            for e in cur.link_edges:
                ov = e.other_vert(cur)
                if ov and ov.index not in visited:
                    stack.append(ov)
        if len(verts_in_this) >= MIN_ISLAND_VERTS:
            islands.append(verts_in_this)
    bm.free()

    if len(islands) <= 1:
        continue  # single island = nothing to check

    # 2. Per-island centroid and bbox
    def _bbox(coords):
        return (
            mathutils.Vector((min(c.x for c in coords), min(c.y for c in coords), min(c.z for c in coords))),
            mathutils.Vector((max(c.x for c in coords), max(c.y for c in coords), max(c.z for c in coords))),
        )
    def _centroid(coords):
        n = len(coords)
        return mathutils.Vector((sum(c.x for c in coords) / n, sum(c.y for c in coords) / n, sum(c.z for c in coords) / n))

    island_centroids = [_centroid(c) for c in islands]
    weights = [len(c) for c in islands]

    # 3. Reference frame = largest island's bbox (by VOLUME — vert count is
    #    unreliable because the build's bevel modifier subdivides bolts /
    #    sockets / cylinders heavily, often giving them more verts than the
    #    main body cube; using vert count picks a tiny bolt as "main" and
    #    every other island ends up "outside" its 1cm³ bbox), expanded by
    #    MARGIN_FACTOR. Using the whole-part AABB would tautologically
    #    contain every island.
    def _bbox_volume(coords):
        bmin, bmax = _bbox(coords)
        return (bmax.x - bmin.x) * (bmax.y - bmin.y) * (bmax.z - bmin.z)

    main_idx = max(range(len(islands)), key=lambda i: _bbox_volume(islands[i]))
    main_min, main_max = _bbox(islands[main_idx])
    main_size = main_max - main_min
    pad = main_size * (MARGIN_FACTOR - 1.0) / 2.0
    expected_min = main_min - pad
    expected_max = main_max + pad

    def _outside(c):
        return (c.x < expected_min.x or c.x > expected_max.x or
                c.y < expected_min.y or c.y > expected_max.y or
                c.z < expected_min.z or c.z > expected_max.z)

    # 4. Collect outliers (everything but the main island that lies outside
    #    the expected region).
    outliers = []
    for i, (c, w) in enumerate(zip(island_centroids, weights)):
        if i == main_idx:
            continue
        if _outside(c):
            # how far out (max axis overshoot)
            overshoot = max(
                max(0.0, expected_min.x - c.x), max(0.0, c.x - expected_max.x),
                max(0.0, expected_min.y - c.y), max(0.0, c.y - expected_max.y),
                max(0.0, expected_min.z - c.z), max(0.0, c.z - expected_max.z),
            )
            outliers.append((c, w, overshoot))

    if not outliers:
        continue

    # 5. Cluster outliers by centroid proximity (single-link, greedy)
    clusters = []  # list[ list[(centroid, verts, overshoot)] ]
    for entry in outliers:
        c_new = entry[0]
        for cluster in clusters:
            if any((c_new - m[0]).length <= CLUSTER_RADIUS_M for m in cluster):
                cluster.append(entry); break
        else:
            clusters.append([entry])

    # 6. Emit one [FLOATING_WARN] per cluster meeting the verts floor
    for cluster in clusters:
        total_verts = sum(e[1] for e in cluster)
        if total_verts < MIN_CLUSTER_VERTS:
            continue
        cx = sum(e[0].x * e[1] for e in cluster) / total_verts
        cy = sum(e[0].y * e[1] for e in cluster) / total_verts
        cz = sum(e[0].z * e[1] for e in cluster) / total_verts
        max_overshoot = max(e[2] for e in cluster)
        print(
            f"[FLOATING_WARN] {name}: cluster of {len(cluster)} island(s) "
            f"({total_verts} verts total) centered at world ({cx:.3f},{cy:.3f},{cz:.3f}); "
            f"{max_overshoot*100:.1f}cm OUTSIDE the part's main-mass bbox "
            f"(main bbox X[{main_min.x:.2f},{main_max.x:.2f}] "
            f"Y[{main_min.y:.2f},{main_max.y:.2f}] Z[{main_min.z:.2f},{main_max.z:.2f}], "
            f"margin {MARGIN_FACTOR}×). Likely a misplaced sub-feature "
            f"— check the transform that places this sub-primitive in build_{name.lower()}()."
        )
```

## Tuning

- **`MARGIN_FACTOR` (default 1.5)**: how far past the main-mass bbox an edge feature can legitimately sit. Drop to 1.2 for tightly-bound parts (e.g. tools, mechanism housings) where any meaningful overshoot is suspicious; raise to 2.0+ for parts with intentional outboard accent features (an antenna mast, a hood ornament).
- **`MIN_CLUSTER_VERTS` (default 50)**: floor for ignoring noise from bevel-induced tiny islands. Raise if bevel.segments is high and you see runs of small-cluster FPs; lower if the part's real strays are <50 verts (rare).
- **`CLUSTER_RADIUS_M` (default 0.05)**: distance below which co-located outliers merge into one warning. Increase for parts with multiple closely-spaced misplacements (a row of misplaced bolts); decrease if distinct stray locations are merging too aggressively.
- **Parts with intentional separated features** (e.g. a panel with screws at opposite corners): the volume-based main-island selection handles these correctly because the panel's bbox is far larger than the screws'. Only escalate to per-name skip (`if name in (...): continue`) if the part really has no dominant body — e.g. a fully scattered field of identical small features.

## Why not raise instead of warn?

Same rationale as the other contracts: **render still proceeds** so the judge sees the visual artifact. A stray floating clump is informative to the vision LLM ("there are stray edges to the right of the main mesh") — combining that with the precise `[FLOATING_WARN]` line in stderr gives the fix agent both qualitative (what it looks like) and quantitative (where exactly + how far off) signals.

## Pairing with fix-loop

When this contract fires, `topos/prompts/system/fix_part.md.j2` instructs the part-fix agent to read the relevant `artifacts/parts_render/<part>/view_*.png` AND grep `trajectories/*build*/stderr.log` for `FLOATING_WARN` lines matching the part name. The agent then has: visual evidence (render) + structural evidence (coordinates of the misplaced island) + judge text — all three together let it locate and fix the bug rather than guessing.
