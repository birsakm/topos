# Blender Python API — curated version notes

The `topos bpy-docs` index covers whichever Blender binary you point it at,
so the **signatures** in the index are always correct for the runtime
Blender. These notes cover what the index can't tell you on its own:

- **Cross-version diffs.** What changed between Blender minor versions that
  matters for procedural Python code. Read before upgrading the pinned
  Blender binary.
- **Upcoming removals.** APIs already announced for removal in a future
  Blender version — useful when writing new code so you don't ship dead
  patterns.

## Files

| File | Scope |
|---|---|
| `current_pinned.md` | Which Blender version Topos is currently pinned to + how to upgrade |
| `5_0_to_5_1.md` | Verified Python API deltas between Blender 5.0 and 5.1 |
| `blender_6_0_outlook.md` | Known/announced deprecations for the next major version |

## Sourcing rule

Everything in these notes is **verified against** `docs.blender.org/api/<ver>/change_log.html` or directly against installed binaries. We do not speculate — if a change isn't on the official change_log or in a release note, it doesn't go here. When in doubt, the `topos_bpy_docs` SKILL points agents to run `topos bpy-docs search <name>` against the installed binary, which beats any cached doc.

## When to update

- After upgrading the pinned Blender binary (rebuild the index, write a new `<old>_to_<new>.md`).
- When upstream announces a new major-version deprecation that affects code paths we use (mesh / material / EEVEE / URDF export).
