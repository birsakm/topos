# ADR 0008 — SubgraphTask as the runtime DAG-expansion primitive

- **Date:** 2026-05-18
- **Status:** Accepted (slice A — articulated single-object); slice B (assembly-decomposed build) and slice C (scene tier) deferred
- **Companion:** [`docs/architecture-recursive-dag.md`](../architecture-recursive-dag.md) (long-form sketch)

## Context

`plan.json` is frozen the moment a run starts. It is either hand-authored (`topos init --from-example`) or generated once by `topos/orchestrator/plan_generator.py` from a `ProjectSpec`. The DAG never grows new structural nodes mid-run — `topos/orchestrator/fix_loop.py` re-runs *existing* task ids with new prompts but does not add slots.

`design.json`, in contrast, is written *during* the run by `01_agent_design` and can declare more parts than the upfront plan has slots for. The mismatch is absorbed inside a single agent: the "Drawer" agent writes three drawer files (`drawer_top.py`, `drawer_middle.py`, `drawer_bottom.py`) in one session (see `outputs/cab_a9_palace3/src/parts/`). This was acceptable at Stage 2 with ≤ 8 parts. It falls over at:

- objects with 15-25 parts (e.g., Optimus Prime humanoid)
- scenes with N objects × M parts (Stage 3+)
- recursive composition (city of scenes)

`tasks.py` and CLAUDE.md L5 reserved `SubgraphTask` for exactly this case but never defined it.

`fix_loop.py:215-329` already mutates the task list at runtime: it inspects judge results, synthesizes new `AgentTask` instances with `is_fix_rerun=True`, and appends them to the live task list which the runner re-dispatches (`runner.py:202-310`). The mechanism is there; structural expansion is the same operation applied to a different signal (design output, not judge failure).

## Decision

Promote `SubgraphTask` to a first-class task kind. Runner expands it at runtime by reading the parent's design output and splicing per-child tasks into the live DAG.

### Schema

```python
@dataclass
class SubgraphTask:
    id: str
    deps: list[str]
    expand_from: str             # path relative to workspace, e.g. "src/design.json"
    expansion_kind: str          # registry key, e.g. "articulated_parts"
    timeout_s: int = 60          # expansion itself is deterministic Python; this is just safety
    kind: Literal["subgraph"] = "subgraph"
```

`plan.json` gains a third member of the task union, validated by `plan_schema._SubgraphTaskModel`.

### Mechanics

1. Runner dispatches the parent agent (e.g., `01_agent_design`) as a normal `AgentTask`.
2. When all of the SubgraphTask's `deps` succeed, the runner reads `<workspace>/<expand_from>`, looks up the strategy by `expansion_kind`, calls `expand.build_children(subgraph_task, workspace, design_doc) -> list[Task]`.
3. Returned tasks have ids namespaced as `<subgraph_id>__<local_child_id>` (double-underscore separator, rare in current ids, greppable).
4. Children get spliced into the live task list. The SubgraphTask itself records a `TaskResult` whose `output` is the list of spawned child ids.
5. Downstream tasks (e.g., `13_agent_build`) reference the SubgraphTask id in their `deps`; they unblock when the SubgraphTask's success condition is satisfied — defined as **all children succeeded**.
6. `outputs/<slug>/plan.expanded.json` is persisted after each expansion round as a runtime artifact, alongside the input `plan.json` (which is never mutated).

### Expansion strategy module

A new module `topos/orchestrator/expand.py` houses the strategy registry. Same API shape as `fix_loop.build_fix_tasks`:

```python
def build_children(
    subgraph: SubgraphTask,
    workspace: Workspace,
    design_doc: dict,                          # already-parsed JSON
    parent_result: TaskResult,
) -> list[AgentTask | ToolTask]: ...
```

The first registered strategy is `articulated_parts`: reads `design.json["parts"]`, emits per-part `<NN>_agent_part_<lower>` + `<NN>_tool_texture_<lower>` + `<NN>_tool_judge_part_<lower>` (the same triplet that `plan_generator.generate_plan_articulated` emits at plan-gen time today, just lifted to run-time).

Future strategies (deferred): `scene_objects`, `assembly_edges`.

### Resume + cost

- `RunReport.results` keyed by task id continues to work because child ids are deterministic given the parent's output. Re-expansion produces the same ids unless the parent re-ran and chose differently — in that case stale results are left in `results` but disconnected from the new dep graph (acceptable for v1; revisit if it produces orphan trajectory bloat).
- `topos cost` aggregates by walking `results` already; per-subgraph rollup falls out by `groupby` on the namespace prefix.
- Trajectory dirs become `trajectories/02_subgraph_parts__03_agent_part_drawer_top_iter0/`. No code change in `_trajectory_dir_for`.

## Alternatives considered

1. **Generalize `fix_loop`'s append mechanism with a boolean `expandable` field on AgentTask.** Rejected as the primary path. Cheapest implementation (≈ 30 LOC runner hook + 150 LOC expand.py + 0 schema change), but loses the declarative property — plan.json no longer represents DAG shape, and inspection / validation become weaker. Kept as an internal *implementation* shortcut though: the runner's expansion round borrows the fix-loop's dispatcher hook style.
2. **Add `expand_from_output` field to AgentTask.** Rejected. Conflates two responsibilities (do work + declare DAG structure). Scene tier wants pre-expansion sub-tasks (web-search, reference render) under the same parent, which doesn't compose well with this shape.
3. **Make every agent task implicitly expandable.** Rejected. Most agents are leaves; making expansion implicit makes plan.json harder to read and inflates runner code with conditional branches per task.
4. **Recursive plan templates: a SubgraphTask references a sub-plan.json file.** Rejected for now. Adds a layer of file IO and makes static validation harder. Reconsider for slice C (scene) once the expander registry is proven.

## Consequences

This is a **hard cutover, not an additive change.** Per user mandate 2026-05-18: legacy fan-out code is deleted, not preserved behind a flag. The articulated domain only works through SubgraphTask after this lands. Examples are rewritten in place.

- `plan_generator.py` shrinks: per-part fan-out (current lines 73-194) is **deleted**. `generate_plan_articulated` becomes ~ 80 LOC emitting design + one `SubgraphTask` + build/joints/asm-tools.
- `examples/articulated_drawer_cabinet/plan.json` is **rewritten** (~ 140 lines, down from 345). The old shape is not preserved.
- Other examples that used the old per-slot fan-out (chair, bicycle, etc.) are migrated to SubgraphTask in the same pass. Examples that can't be migrated cleanly are deleted, not left broken.
- Per-part fix-loop budget is now real: a failing drawer agent gets its own fix attempts without burning the budget for healthy siblings (each child has its own task id, `fix_loop` already operates per-task).
- Adding a new articulated domain (e.g., humanoid) does not require touching `plan_generator.py` for the part fan-out — just reuse `articulated_parts` or register a sibling expander.
- `assembly_tree` field on design.json is *not* added in slice A. Deferred to slice B (assembly-decomposed build).
- Per-subgraph parallelism cap is *not* added in slice A. Global cap inherited from current `runner.py:481` dispatcher. Revisit if scene tier sees starvation.

## Empirical results (slice A)

To be filled in after implementation:

- Cabinet smoke: judge ≥ ___ (target: ≥ 0.65, baseline 0.65-0.75)
- Cabinet cost: $ ___ (target: ±15% of baseline $1.5-2.2)
- 5-drawer dynamic test: ___ part-agents in `plan.expanded.json`
- Optimus Prime (15-25 parts): judge ___, identity ___, render at `outputs/optimus_prime_validation/artifacts/object_render/`
- Net LOC delta: +___ (framework) / -___ (boilerplate)

## Open questions for slice B/C

- Design re-rerun teardown policy (cascade-delete obsolete subgraph children vs leave orphans). Slice A: leave orphans.
- `iter_policy.per_subgraph_max_iters` — needed when one bad subgraph shouldn't burn global budget.
- Nested `SubgraphTask` in hand-written plan.json (declarative recursion). Mechanism supports it (multi-round fixpoint); validation needs to allow it explicitly.
- Cross-subgraph deps: can a part in object A depend on a part in object B? For v1 no (children are namespaced under their subgraph and dep resolution is scoped). For scene-level assemblies this may need to relax.
