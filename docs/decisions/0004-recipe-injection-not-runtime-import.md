# ADR 0004 — Reusable components ship via recipe injection; heavy machinery vendored at freeze

- **Date:** 2026-05-10
- **Status:** Accepted (amended 2026-05-11)

## Amendment 2026-05-11 — `topos_runtime/` folded into `topos/`

The two-package split (`topos/` framework + `topos_runtime/` vendorable) was simplified to a single `topos/` package. Reasons: `topos_runtime/` only ever contained one real module (`urdf/writer.py`) — the other three placeholder subpackages (`io/`, `cameras/`, `bmesh_ops/`) sat empty for months. `topos freeze` is not yet implemented anyway, so the eventual "scan + vendor" logic can target any `topos/*` module just as well as a separate package.

The L3 conceptual split — *reuse-via-recipe-injection* vs *vendor-at-freeze* — survives. The mapping just changes: instead of "`topos_runtime/*` → vendored", it becomes "specific `topos/*` modules → vendored by freeze, selected by import-scan". The `urdf.py` content is unchanged; only its location and import path moved (`topos_runtime.urdf.writer` → `topos.urdf`). Body of this ADR below preserved for historical context; treat any `topos_runtime` mention as referring to the corresponding `topos/<module>.py`.

## Context

We want reusable building blocks (chair-leg generator, prismatic-joint helper, PBR wood material, etc.) *and* every produced `outputs/<slug>/` to be standalone-runnable. These goals conflict if reusables live in a shared runtime library that frozen projects must keep importing.

## Decision

Two reuse mechanisms, complementary:

- **A — Recipe injection (primary).** Common components live in `topos/recipes/<category>/<id>.md` as markdown with example code. At `AgentTask` startup, the framework injects the requested recipes into the agent's prompt. The agent **copy-adapts** the code into the project's own files. The frozen project imports nothing from `topos`.
- **B — Heavy runtime (`topos_runtime/`, vendored at freeze).** A small set of components too heavy to copy each time (URDF writer, complex bmesh ops, USD I/O) live in a pip package. Agents may `import topos_runtime.<sub>` during development. The `topos freeze` command scans imports and copies only the used subset into `outputs/<slug>/vendored/`, rewriting imports to be relative. The frozen project ships with its `vendored/` and runs without a `topos_runtime` install.

A separate `topos recipe promote --from <project>:<symbol>` flow lifts proven project code back into the recipe library, **with human review** before merge.

## Alternatives considered

1. **Pure recipe injection (no `topos_runtime`).** Rejected: complex deterministic algorithms (URDF emission, mesh booleans) duplicated and re-derived per project — error-prone.
2. **Pure `topos_runtime` import.** Rejected: violates the standalone-output invariant unless vendored; if we vendor anyway, recipes are still the lighter path for things that don't need to be a function.
3. **RAG over historical project code as primary.** Rejected for now: lower quality without curation; can be added as a recipe-search backend later without changing the contract.

## Consequences

- Recipe library size matters: bad/inconsistent recipes pollute prompts. Promotion stays human-gated.
- Freeze must implement import scanning + selective copy + import rewriting. Tested under `tests/test_freeze.py`.
- A project might bundle a slimmed-down copy of `topos_runtime` rather than the full package — that's intentional.
- Agents must be told via prompt which recipes are available for a task; an `allowed_tools` + `recipes` whitelist controls scope.
