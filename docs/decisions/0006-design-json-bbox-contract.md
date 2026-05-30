# ADR 0006 — design.json bbox contract for multi-part assemblies

- **Date:** 2026-05-11
- **Status:** Accepted

## Context

Multi-part articulated objects (cabinet with drawer, mechanism with linkage, robot with N joints) need multiple agents to write geometry in parallel — one file per part — and the result must assemble correctly. Without a contract, agents would either need to (a) read each other's code, (b) be authored sequentially, or (c) be coordinated by an "assembly" agent that resolves conflicts post-hoc. Each path is expensive in tokens and brittle when N grows.

## Decision

A `01_agent_design` task produces `src/design.json`: a frozen, machine-readable contract listing every part with `name`, `world_xyz` (bbox center), `world_extents` (full size along X/Y/Z), strategy hints, and per-part sub-specs (e.g. `cavity` for hollow bodies). Every downstream part agent reads `design.json`, finds its entry by name, and implements `build_<part>()` to satisfy: the produced object's world bbox center is within 5mm of `world_xyz` AND extents are within 5mm of `world_extents`.

A `05_agent_build` task writes `build.py` that imports each part's builder, calls it, and validates the result against the contract. Validation prints `[OK]` or `[WARN]` per part with mm-precision error. WARN does NOT raise — downstream tools still run so the judge can see the visual mistake.

Joints are derived from the same design (positions of parts in world give joint origins via `child.world_xyz - parent.world_xyz`); the `06_agent_joints` task writes `joints.yaml` from `design.json` alone.

## Alternatives considered

1. **Sequential authoring (no contract).** Part 1 → Part 2 reads Part 1 → Part 3 reads both. Rejected: serializes the slowest part of the pipeline; the agent has to reverse-engineer earlier files instead of writing fresh.
2. **One mega-agent writes all parts.** Rejected: prompt grows quadratically with part count; agent has to hold the whole geometry in its head; failure of any one part kills the whole task.
3. **Post-hoc assembly agent reconciles conflicting parts.** Rejected: assembly happens too late — by the time the agent sees the mismatch, all parts have been written. Cheaper to prevent than to fix.
4. **Hard assertion in build.py instead of WARN.** Rejected (initially): a part with a bbox error of 6mm would block all downstream rendering, so the judge never sees the actual visual defect. The fix-loop is more effective when it sees a render than when it sees an exception.

## Consequences

- Parts are written truly in parallel by agents that don't see each other — large N scales linearly in agent count, not quadratically.
- Adding a new part = adding an agent task referencing the same `topos/prompts/articulated/part_geom.md.j2` template with different `part_name` / `extras_file` params.
- `design.json` is the single point of edit when refining a design (clearance, dimensions, color); part agents pick up the change on re-run.
- `build.py` becomes mechanical glue (auto-generatable; agent writes the obvious imports + the validation loop).
- bbox WARN doesn't trigger fix-loop in v1; the judge's visual feedback does. Future v2: also feed bbox WARN into the fix-loop prompt so the agent gets a numerical diff alongside the visual feedback.

## Empirical results (cabinet pipeline)

After this change: Frame/Drawer/Handle all hit `[OK]` 0.0mm err on most runs. Score stable around 0.65-0.75 in single iter. The bbox validator caught the `cube_add(size=1) + scale=full_extents` half-extent bug before it propagated downstream — exact behavior the contract was designed to enable.
