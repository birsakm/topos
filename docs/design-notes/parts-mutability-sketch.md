# Parts-list mutability — design sketch

> Status: design memo · 2026-05-19 · feeds into a future ADR after sedan / Optimus v4 results inform priorities

## What the user asked

> "parts 那部分的确定是可以更改的，比如根据复杂程度，可以 parts 进行补充 or 完善之类的"

Today `design.json` is one-shot: `01_agent_design` writes it once, that's the only chance to decide "what parts exist". Subgraph expansion turns each design.json part into its own agent — but if design.json *itself* under-specifies (missing windshield, missing face plate, missing wheel rims), the framework has no path to add more parts mid-run. Per-part fix-loop only rewrites code for parts that *exist*; it never adds new ones.

Goal: make the parts list a **first-class evolving artifact** that can be augmented (judge-driven reaction) and decomposed (proactive complexity drill-down), without re-running everything from scratch.

## Two complementary mechanisms

The current `SubgraphTask` primitive (ADR-0008) already supports **runtime DAG mutation driven by reading a design doc**. The same primitive can be re-applied in two new ways:

### Mechanism A — Hierarchical decomposition (proactive, *recursive subgraph*)

**When**: design agent declares a top-level part is too complex to model as a single mesh and needs to be itself decomposed into sub-parts.

**How**: extend `design.json` schema with one optional flag:

```json
{
  "name": "Head",
  "world_xyz": [...],
  "world_extents": [...],
  "composite": true,
  "composite_intent": "G1 Optimus helmet — visor band, mouth plate, antenna fins, helmet shell"
}
```

When the runner expands `02_subgraph_parts`, instead of spawning an `agent_part_head` that writes `src/parts/head.py`, it spawns a **second-level design agent** `agent_design_head` whose only job is to write `src/parts/head/design.json` describing the helmet's sub-parts. That second-level design then triggers another SubgraphTask (`subgraph_parts_head`) that expands one level deeper.

Build agent reads the tree:

```
src/
  design.json                    # top-level: lists Head as composite
  parts/
    head/
      design.json                # head sub-design: helmet_shell, face_plate, visor, antenna_fins
      helmet_shell.py
      face_plate.py
      visor.py
      antenna_fins.py
    torso.py                     # leaf parts still flat
    ...
```

The build agent composes leaf meshes recursively, applying composite-level bbox check (the head's sub-mesh's total bbox must still fit the Head's design.json `world_extents` ±5mm).

**Reuse**: this is literally the recursive primitive `SubgraphTask` was reserved for. A new `expansion_kind: "composite_subdesign"` strategy in `expand.py` (~50 LOC) is all it takes; runner needs no change because subgraph fixpoint already supports multi-round expansion.

**Cost discipline**: composite parts cost ~3-5× a leaf (extra design call + N child agents). Use only when the part's `geometry_strategy` is genuinely complex enough that one agent can't fit it in 2-3k output tokens.

### Mechanism B — Judge-driven augmentation (reactive, *design-level fix-loop*)

**When**: assembly judge says "the render is missing X" (windshield wipers, hood ornament, Autobot insignia, exhaust pipe…) — a structural omission, not a per-part quality issue.

**How**: a new fix-loop branch in `fix_loop.py`:

- After assembly judge fails AND judge feedback matches a "missing-part" pattern (regex on `feedback` strings containing "no visible", "missing", "lacks a"…)
- Synthesize a `99_agent_design_augment` task whose goal is "read current design.json, read judge feedback, append the missing parts to the parts list, keep all existing parts intact"
- After it lands, re-trigger the SubgraphTask expansion — existing parts carry forward (their `agent_part_*` results are in the prior iter's success set), new parts get new agents
- Build re-runs (no carry-forward, because the build needs to import the new parts)

**Reuse**: same `is_fix_rerun` machinery as today's part-code fix-loop. The novel bit is that the fix targets `design.json` instead of `src/parts/<lower>.py`. The runner needs the SubgraphTask to **support a "re-expand" round** when its source doc changes (currently it expands once). One line in the runner.

**Identity preservation**: when re-expanding, child ids must be deterministic given the design doc. Existing part ids stay stable; new ids appear for new parts. This is already the design (per ADR-0008 §5.1) — just need to enforce ordering.

## Why two, not one

| | Mechanism A (composite) | Mechanism B (augment) |
|---|---|---|
| Timing | Decided at design time | Decided after seeing a render |
| Driven by | Design agent's own judgment | Vision judge's reaction |
| Failure mode | None — agent didn't see a need | Pre-design under-specification got past the spec agent |
| When useful | Optimus head (multiple iconic sub-features in one bbox) | Missed a windshield wiper / hood ornament / Autobot badge |
| Cost on cabinet | ≈ same (no composite parts) | ≈ same (no missing parts) |
| Cost on detailed humanoid | +30-50% (more parts) | +15% (additional iter) |

They compose — same Optimus run can have **both** a composite Head (Mechanism A spawns helmet_shell + face_plate + visor + antennae sub-agents) **and** judge-driven augmentation if final assembly reveals "missing chest grille". The mechanisms don't conflict because both produce new `parts` entries in design.json under deterministic ids.

## Schema additions (forward-compatible)

`design.json` parts entries gain two optional fields (current parts continue to validate):

```json
{
  "name": "<PascalCase>",
  "world_xyz": [...],
  "world_extents": [...],
  // ...existing fields...
  "composite": false,              // optional, default false (leaf part)
  "composite_intent": null          // required iff composite=true; brief NL for the sub-design agent
}
```

`expand.py` gains:

```python
@register_expander("composite_subdesign")
def _expand_composite_subdesign(subgraph, *, workspace_root, design_doc) -> list[Task]:
    """For a parent part flagged composite=true, emit:
       - one sub-design agent task that writes parts/<lower>/design.json
       - one inner SubgraphTask whose expand_from points at that sub-design
    Trade-off vs. flat: 1 extra agent call + N grandchildren per composite,
    but the parent agent doesn't have to compose all sub-features in one
    session."""
    ...
```

`fix_loop.py` gains:

```python
def build_design_augment_fix_task(results, original_tasks) -> AgentTask | None:
    """If the assembly judge failed with a missing-part-shaped complaint,
    emit a 99_agent_design_augment task that appends the missing entries
    to src/design.json. Returns None if no missing-part complaint."""
    ...
```

## Risks / open questions

1. **Cycle prevention**: if Mechanism B keeps re-augmenting design.json every iter (judge always finds more "missing" stuff), we burn budget. Cap: `iter_policy.max_design_augments` (default 2).
2. **Bbox drift**: composite sub-parts must still respect the parent's bbox contract. Build's existing ±5mm assert covers leaves; need a recursive variant.
3. **Build agent prompt**: needs to know about the directory tree (`src/parts/head/*.py` vs `src/parts/head.py`). Either prompt change or build template emits the right `from parts.head.helmet_shell import build_helmet_shell` style imports.
4. **When does spec agent know to flag composite?** Probably never directly — the design agent at top level decides, looking at `world_extents` × `geometry_strategy` complexity. Add a paragraph to designer.md.j2.
5. **Cost ceiling**: a 3-level deep recursion (scene → object → composite-part → sub-part) could go wild. Hard cap depth at 3 in the runner: `max_subgraph_depth` config.

## What to implement first

After sedan / Optimus v4 results come in, prioritize by what hurts most:

- If Optimus v4 score is stuck at 0.65 because parts are missing iconic features → Mechanism A first (composite head, composite torso)
- If sedan needs windshield wipers / antenna / hood ornament that judge spotted → Mechanism B first
- Both probably needed eventually; A is the higher-leverage mid-term win because it makes the architecture **fully recursive** (scene → object → composite-part — one primitive).

## Out of scope

- Mid-run NL prompt edits from the user (would change intent.md)
- Spec agent re-runs (the user-prompt → ProjectSpec stage stays one-shot)
- Stripping parts from design.json (only additive)

---

This is a sketch, not a plan. Concrete ADR + implementation plan after we see what Optimus v4 + sedan stress tests reveal as the actual bottleneck.
