# Recursive Subgraph DAG — North-Star Architecture

> Status: design sketch · 2026-05-18 · feeds into ADR-0008 (TBD) · supersedes nothing yet
>
> This document is the long-form companion to README TODO P0 #1 ("Reorganize the prediction flow, constructing a DAG architecture using graph node to represent each agent step"). It is **forward-looking** — the current Stage-2 implementation does not yet match this picture. The first slice (articulated single object) will land per the plan in `~/.claude/plans/sync-github-repo-readme-todo-idea-smooth-volcano.md`. Scene / city tiers depend on this primitive but are out of scope for the first implementation pass.

---

## 1. Where we are today

### 1.1 The two files

| File | What | Authored when | Authored by |
|---|---|---|---|
| `plan.json` | DAG of tasks (recipe) | **Before** run | Hand-written example, or generated once by `plan_generator.py` from a spec agent's `ProjectSpec` |
| `design.json` | Spec of the asset being produced (blueprint) | **During** run, as the first task | The `01_agent_design` agent task |

`plan.json` says *how we work* (tasks + deps + tools); `design.json` says *what we are making* (parts + bbox + poses + joints).

### 1.2 The frozen-DAG problem

`plan.json` is frozen the moment the run starts. The DAG never gains a new structural node mid-run — `fix_loop.py` re-runs existing task IDs with new prompts but does not add new slots.

This creates a slot/instance mismatch:

```
plan.json hardcodes:         design.json may declare:
  Frame                        Frame
  Drawer    ◀────  N=3 ──▶    DrawerTop, DrawerMiddle, DrawerBottom
  Handle    ◀────  N=3 ──▶    HandleTop, HandleMiddle, HandleBottom

                              ⇒ same agent slot writes N files internally
                              ⇒ context-bombs at ~10+ parts per slot
                              ⇒ no per-part judge, no per-part fix-loop budget
                              ⇒ scene tier (N objects × M parts) is impossible
```

The "Drawer" agent is asked to write three drawer Python files in a single session. Per the May-2026 Stage-2 wins this is fine for 3 drawers; it falls over fast at scale.

### 1.3 What the runner already does dynamically

`fix_loop.py` (414 LOC) already mutates the task list at runtime:

- After a wave completes, it inspects judge results
- If a part / assembly judge failed, it calls `build_fix_tasks` / `build_runtime_fix_tasks` which **`append` new `AgentTask` instances** to the task list (`fix_loop.py:277`, `308`, `320`, `404`)
- Those tasks reuse the original task's `id` with `is_fix_rerun=True` so downstream deps auto-bind to the fixed version (`tasks.py:29`, `runner.py:392-409`)
- `runner.py:194-310` iterates this fix-loop up to `iter_policy.max_global_iters` times

The mechanism for "compose a new task list at runtime from inspecting prior results" is already there. We just don't use it for **structural** growth — only for fix-rerun. Recursive-subgraph expansion is the same operation in a different mood.

---

## 2. The unified primitive

Every level of the pipeline follows the same shape:

```
            ┌────────────────────────────────────┐
            │  design-doc agent at level L       │
            │  writes a JSON listing children    │
            └──────────────────┬─────────────────┘
                               │ runtime fanout
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
     ┌────────────┐     ┌────────────┐     ┌────────────┐
     │ child 1    │     │ child 2    │ ... │ child N    │
     │ subgraph   │     │ subgraph   │     │ subgraph   │
     │ at L+1     │     │ at L+1     │     │ at L+1     │
     └──────┬─────┘     └──────┬─────┘     └──────┬─────┘
            └──────────────────┼──────────────────┘
                               ▼
              composer at level L (build/place/assemble)
                               ▼
                  verifier at level L (judge)
```

Substitute the level:

- **L0 (scene)**: scene-design-agent writes `scene_design.json` listing objects → N object subgraphs → scene-build (places objects) → scene-judge
- **L1 (object)**: object-design-agent writes `design.json` listing parts → M part subgraphs → object-build (composes parts, current `13_agent_build`) → object-judge
- **L2 (part)**: part-agent writes geometry Python (leaf) — paired with texture tool and judge_part tool, but does not itself fan out (in v1)

The design *is* fractal. We just need one expansion primitive and the rest follows.

```
intent (NL)
   │
   ▼
spec agent ── ProjectSpec ──▶  L0 scene-design (only if scene; bypassed for single-object)
                                  │
                                  ▼ fanout
                  ┌───────────────┼───────────────┐
                  ▼               ▼               ▼
            object subgraph  object subgraph  ...
              │
              ▼
       L1 object-design ─── fanout ──▶ part subgraphs (L2 leaves)
              ▼
       object build + joints + judge
                  ▼
       (back up to L0)
                  ▼
       scene build + scene judge
```

For Stage 2 (single object) only **one level of expansion** fires. For Stage 3+ (scene) two levels fire. The runner is unaware of the level count — it just expands wherever it sees an unexpanded `SubgraphTask`.

---

## 3. Three formulations of the expansion mechanism

### Option A — Generalize `fix_loop`'s append mechanism

Pull the strategy out of `fix_loop.py` into a sibling `expand.py`. Tag each `AgentTask` whose output should drive expansion (e.g., a boolean `expandable=True` on AgentTask itself). The runner's main loop gets a new `_expansion_round()` between iter 0 and the fix loop.

- Reuse: very high — same dispatcher, same dep resolution, same sticky-pass / carry-forward
- Net new code: ~150 LOC for `expand.py` + ~30 LOC runner hook
- Schema: no change (just a bool field on AgentTask)
- Downside: "expansion" lives only at runtime, never as a first-class concept in plan.json. The saved snapshot has to be reconstructed by walking results.

### Option B — `SubgraphTask` as a first-class task kind (recommended)

Promote the reserved `SubgraphTask` (currently a docstring mention in `tasks.py`) to a real dataclass alongside `AgentTask` / `ToolTask`. plan.json gains a third member of the task union: `kind: "subgraph"` with fields:

```python
@dataclass
class SubgraphTask:
    id: str
    deps: list[str]
    expand_from: str        # path to the design doc the parent wrote (e.g., "src/design.json")
    expansion_kind: str     # registry key — "articulated_parts" | "scene_objects" | ...
    kind: Literal["subgraph"] = "subgraph"
```

The runner sees a `SubgraphTask` in topo order. When its dep parent completes, the runner reads `expand_from`, calls `expand.build_children(...)` keyed by `expansion_kind`, splices the returned tasks into the live task list (under a namespaced id prefix), and continues dispatch. Children deps reference the subgraph id; the next task downstream of the subgraph (e.g., `13_agent_build`) treats the subgraph as a single "all children done" gate.

- Reuse: same dispatcher / fix-loop hooks
- Net new code: ~50 LOC plan schema + ~20 LOC tasks.py + ~80 LOC runner hook + ~150 LOC expand.py
- Schema: third member of the task union — declarative, validates statically
- plan.json remains the truth of the DAG *shape*; only the *count* of children inside each subgraph is dynamic
- ADR-able primitive that explains itself in one diagram

### Option C — AgentTask gains `expand_from_output`

Minimal schema change: one new field on `_AgentTaskModel`. When set, the runner reads the file after the agent completes and dispatches an expander.

- Reuse: highest (one if-branch in runner)
- Net new code: ~10 LOC schema, ~30 LOC runner, ~150 LOC expand.py
- Downside: conflates two responsibilities (do work + declare DAG shape). Doesn't scale to scene where the scene-design step itself wants pre-expansion sub-tasks (render-references, web-search, etc.)

### Recommendation — Option B

1. The reserved primitive already names the right thing (`tasks.py` module docstring, CLAUDE.md L5 row). Implementing it pays off a docs debt the framework already incurred.
2. plan.json remains a faithful representation of DAG shape; static validators still work on the unexpanded form.
3. Resume + inspection + trajectory layout all "just work" because the subgraph itself is a `TaskResult` entry whose `output` payload is the list of child ids it spawned.
4. The expansion *strategy* (how to read design.json and emit children) is a separate module mirroring `fix_loop.build_fix_tasks` — same API shape, easy to test, easy to add a new domain.

---

## 4. `design.json` schema evolution

Today `design.json` has `parts: [...]` + `joints: [...]` (see `outputs/cab_a9_palace3/src/design.json` for a real sample). Two forward-looking additions, both optional in v1:

### 4.1 Rename `parts` to `children` (or keep both)

To make the recursive primitive obvious. At L1 these are parts; at L0 they would be objects (each itself a subgraph). The expander reads whichever field is present, schema validates both for backward compat. Concretely:

```json
{
  "robot_name": "...",
  "description": "...",
  "children": [
    { "name": "Frame", "kind": "part", "world_xyz": [...], ... },
    { "name": "DrawerTop", "kind": "part", "world_xyz": [...], ... },
    ...
  ],
  "joints": [...]
}
```

### 4.2 Explicit `assembly_tree`

```json
"assembly_tree": [
  { "parent": null, "child": "Frame", "transform": {...} },
  { "parent": "Frame", "child": "DrawerTop", "transform": {...} },
  { "parent": "DrawerTop", "child": "HandleTop", "transform": {...} }
]
```

This is the parent/child topology that the joint list implies today, but made explicit so the **build step itself becomes decomposable**: instead of one `13_agent_build` placing all parts, a future iteration can emit one `place_<child>` node per assembly edge, traversing the tree in topo order. Stage 2 doesn't need this — keeping the field optional means the v1 refactor can ignore it. Stage 3+ assembly with 50+ parts will need it.

---

## 5. Resume / cost / trajectory under a dynamic DAG

Current invariants (read `runner.py:300`, `_trajectory_dir_for`, `topos/orchestrator/results.py`):

- `RunReport.results` is a dict keyed by `task_id`
- Trajectory dirs are `outputs/<slug>/trajectories/<task_id>_iter<N>/`
- Resume walks `results` and skips already-passed tasks

All three depend on **stable, unique task ids**. Subgraph expansion preserves uniqueness if (and only if) expansion is **deterministic** given the parent's output. Concrete adjustments:

### 5.1 Namespace child ids

Pattern: `<parent_subgraph_id>__<local_child_id>`. Double underscore is rare in current ids and easy to grep. Example: `02_subgraph_parts__03_agent_part_drawer_top`. `_trajectory_dir_for` works unchanged — the dir name is just longer.

### 5.2 Persist the expanded plan

After each expansion round, dump a snapshot of the post-expansion task list to `outputs/<slug>/plan.expanded.json`. This is a runtime artifact (gitignored under `outputs/`). The input `plan.json` is never mutated.

Resume reads `plan.expanded.json` if present, falls back to `plan.json` otherwise. This makes `topos run <slug> --resume` work transparently across an in-flight expansion.

### 5.3 Cost tracking

`topos cost <slug>` walks `RunReport.results` already. With namespaced ids, per-subgraph rollups become a `groupby` on the namespace prefix — a `--by-subgraph` view falls out for free. Example future view:

```
$ topos cost cabinet_v2 --by-subgraph
  01_agent_design             $0.12
  02_subgraph_parts           $1.48  (avg $0.21 per child × 7 children)
    __03_agent_part_frame       $0.18
    __04_agent_part_drawer_top  $0.22
    ...
  13_agent_build              $0.31
  ...
```

### 5.4 `TaskResult` shape for SubgraphTask

Probably:

```python
{
  "task_id": "02_subgraph_parts",
  "kind": "subgraph",
  "output": {
    "children": ["02_subgraph_parts__03_agent_part_frame", ...],
    "expansion_kind": "articulated_parts",
    "expanded_at_iter": 0,
  },
  "success": all(child.success),
  "cost_usd": sum(child.cost_usd),
  "elapsed_s": max(child.elapsed_s),  # max because parallel
}
```

Verify `results.py` doesn't need new fields beyond `output` payload (it should not — `output: dict` already absorbs arbitrary shape today).

---

## 6. Concurrency, fix-loop interaction, teardown

### 6.1 Parallel expansion

The current pool defaults to ~4 concurrent tasks (`runner.py:481`). A scene with 5 objects × 8 parts each = 40 part-agent leaves expanded in one round can burst the queue. We need either:

- A global cap that doesn't care about subgraph identity (simplest)
- A per-subgraph cap that lets sibling subgraphs share the pool fairly (more complex)

v1: keep the global cap, document the risk, revisit if scene-tier work shows starvation.

### 6.2 Fix-loop on dynamically-created tasks

A part-agent created at runtime that then fails its judge must be fixable by `fix_loop` the same way a statically-declared one would. The `is_fix_rerun=True` plumbing (`tasks.py:29`, `runner.py:392-409`) was built for exactly this case (carry-forward of prior results, no id collision). Concretely: fix tasks for dynamic agents are created with the dynamic `id` (already namespaced), so they slot into the dep graph without contortion.

### 6.3 Re-expansion on design-agent fix-rerun

What happens if `01_agent_design` gets fix-rerun and the new `design.json` lists a different number of parts? Two policies, both defensible:

- **Tear down obsolete subgraph children**: walk the prior subgraph result, mark any children whose name disappeared in the new design as obsolete; their results get pruned, their trajectory dirs archived under `trajectories/_archived/`. The new design's children expand fresh.
- **Trust dedup**: re-expand and let id-based dedup handle it; if a child's id is the same (same `name.lower()`) its prior result carries forward; if the id is new, it gets created; if a prior id is missing in the new design, leave its result behind but disconnect deps.

The second is simpler but pollutes `results` with orphans. The first is cleaner but more code. Recommend the second for v1 with an `--archive-orphans` flag for later.

---

## 7. Open questions (for ADR-0008)

1. **Should the design.json schema change be additive (`children` alongside `parts`) or a rename (`parts` → `children` with migration)?** Additive is safer; rename is cleaner long-term. Default to additive.
2. **What's the failure semantics when 1 of N expansion children fails its judge?** Today: the assembly judge has visibility into all parts and can flag composition issues. After the refactor: per-part judge already exists (`07/08/09_tool_judge_part_*`); per-child fix-loop attempts ≤ N times where N is some `max_per_child_fix_attempts`. We need a new policy field (or reuse `max_global_iters` budget split).
3. **Where does the expander registry live?** Proposal: `topos/orchestrator/expand.py` with a dict `_EXPANDERS = {"articulated_parts": ..., "scene_objects": ...}`. Plugin pattern (per CLAUDE.md "plugin paths, not core edits") suggests new domains add their expander under `topos/domains/<domain>/expand.py` and register on import. Defer until we have ≥ 2 domains using it.
4. **Per-subgraph fix budget?** Today's `max_global_iters` applies globally. For a scene with 5 objects, one bad object shouldn't burn the budget for the other four. Add `iter_policy.per_subgraph_max_iters`? Defer to scene-tier work.
5. **`assembly_tree` field — add now or later?** Adding now (optional, ignored by current build agent) is forward-looking but invites schema churn. Adding later (Stage 3+) is cleaner but means a second `design.json` schema rev. Recommend later.
6. **Is `SubgraphTask` allowed to nest in plan.json declaratively, or only via runtime expansion?** I.e., can a hand-written plan.json declare `02_subgraph_objects` whose children are themselves subgraphs (each containing a part subgraph)? Yes — but the dispatcher must support multiple expansion rounds (fixpoint). Easy enough; just don't bound rounds at 1.

---

## 8. Stage gates

This architecture lands in three slices:

| Slice | Scope | Verifies |
|---|---|---|
| **A. articulated single-object** | SubgraphTask + expand.py + articulated_parts expander; cabinet example migrated; per-part fix-loop on dynamic agents | Cabinet smoke ≥ 0.65 unchanged; 5-drawer dynamic test passes; Optimus Prime (15-25 parts) produces a recognizable robot (judge ≥ 0.55 + identity ≥ 0.6) |
| **B. assembly-decomposed build** | Build step itself decomposed into placement nodes traversing `assembly_tree`; per-edge fix-loop | 50-part object (e.g., bicycle, mech) builds without the build-step context blowing up |
| **C. scene-tier** | scene-design-agent + scene_objects expander + scene-build; outdoor garden / interior room domains | Multi-object scene (5-10 objects) renders coherently, layout is sensible |

Slice A is the immediate work covered by the linked plan. B and C are mentioned here so the primitive design doesn't paint us into a corner.

---

## 9. Pointers

- Plan file (slice A execution): `~/.claude/plans/sync-github-repo-readme-todo-idea-smooth-volcano.md`
- ADR (will be written next): `docs/decisions/0008-subgraph-runtime-expansion.md`
- Reserved primitive today: `topos/orchestrator/tasks.py` (module docstring; class not yet defined)
- Mechanism to clone: `topos/orchestrator/fix_loop.py:215-329` (build_fix_tasks pattern)
- Where to hook the runner: `topos/orchestrator/runner.py:194-310` (iter / fix-loop main)
- Today's flat fanout (to delete from generator, lift to runtime): `topos/orchestrator/plan_generator.py:73-194`
- Current plan vs design split: `examples/articulated_drawer_cabinet/plan.json` ↔ `outputs/cab_a9_palace3/src/design.json`
