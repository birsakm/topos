# ADR 0002 — Blender runs stateless by default; hot pool is cache-only

- **Date:** 2026-05-10
- **Status:** Accepted

## Context

Blender can run as `blender --background --python script.py` (cold subprocess, ~1–3s startup) or as a persistent process accepting RPCs (fast, stateful). Performance and reproducibility pull in opposite directions.

## Decision

Default execution is **stateless subprocess per build**: each call boots a fresh Blender, runs the script, exits. A hot process pool (`topos/blender/hotproc.py`) is available as an opt-in cache for iterative loops only; results must reproduce identically when the pool is disabled.

## Alternatives considered

1. **Persistent Blender server always.** Rejected: cross-task state contamination, harder parallelization, single point of failure.
2. **Pure stateless always.** Rejected: tight iteration loops (agent → render → judge → fix) pay startup cost N times; degrades UX for the most common path.

## Consequences

- Agents and tools must not rely on cross-call Blender state (open files, in-memory data blocks).
- Hot pool processes self-kill when dirty or idle past `blender.hot_pool.idle_kill_s`.
- Crash/OOM in hot pool path automatically falls back to a stateless retry.
- Reproducibility tests (`pytest -k integration`) always run with `hot_pool: false` to verify no implicit dependency.
