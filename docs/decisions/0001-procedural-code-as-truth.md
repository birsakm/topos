# ADR 0001 — Procedural Python code is the authoritative form

- **Date:** 2026-05-10
- **Status:** Accepted

## Context

Generated 3D objects can be represented as static assets (meshes, URDFs, .blend files) or as procedural code that reconstructs them. The framework needs one canonical representation so agents, judges, freeze, and version control all agree on what an "object" is.

## Decision

A produced object/scene is represented as a **multi-file Python project** in `outputs/<slug>/src/`. Meshes, materials, URDFs, renders, and .blend files are **derivative artifacts** under `outputs/<slug>/artifacts/`, treated as cache and gitignored.

## Alternatives considered

1. **Assets as truth, code as scratch.** Rejected: assets are opaque to diff, hard to parameterize, encourage agents to overwrite freely instead of reasoning compositionally.
2. **Both as first-class.** Rejected: doubles state to keep in sync; manifest pointers add complexity without clear benefit at this stage.

## Consequences

- Reproducibility: deleting `artifacts/` and re-running `python src/build.py` must yield equivalent output (within stochastic seed bounds).
- Diff and review: `git diff` on `src/` is meaningful; on artifacts is not.
- Storage: artifacts can be discarded between runs.
- Agents must always edit code, never paste binary mesh data.
- Freeze: only `src/`, `vendored/`, and `manifest.json` ship.
