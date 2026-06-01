---
name: topos_bpy_docs
description: Search the local Blender Python API index (bpy.ops, bmesh.ops, mathutils) via the `topos bpy-docs search` CLI (run from Bash). Use when you need to verify an exact API signature, find a less common operator, or check a parameter name before writing code.
when_to_use: Any agent task that writes Blender Python and is uncertain about an exact API signature or parameter — especially for bmesh ops, advanced mathutils, or rarely-used bpy.ops modules. The framework's installed Blender version is pinned to the index, so signatures match what your code will actually run against.
provides:
  - topos bpy-docs search "<query>" [--top-k N] [--kind op|bmesh_op|class|method|function] — keyword/substring ranked search across the indexed symbols, run via Bash
related_skills:
  - topos_part_geometry
  - topos_furniture_hardware
---

# Topos: bpy docs RAG

## What this skill is

The framework ships a local index of the installed Blender's Python API — every `bpy.ops.<module>.<op>`, every `bmesh.ops.<op>`, plus `mathutils` classes and methods. The index is built by `topos bpy-docs index` (runs Blender once for ~2 seconds) and pinned to the user's actual Blender version (so signatures match runtime).

The `topos bpy-docs search` CLI (call it from Bash) lets you query this index from inside an agent task. Use it when you need to verify:

- The exact parameter names and defaults of a `bpy.ops.*` operator (e.g. `primitive_cube_add` expects `size=2` not `size=1`)
- Which `bmesh.ops.*` operation matches your need (e.g. `bevel` vs `inset_individual` vs `subdivide_edgering`)
- The signature of a `mathutils` method (e.g. `Matrix.to_track_quat(forward, up)`)

## How to invoke (via Bash → topos CLI)

The index is exposed as a Topos CLI subcommand. Call it with the `Bash` tool:

```bash
topos bpy-docs search "bevel modifier" --top-k 5
```

Output (plain text, one block per match):

```
[op        score=  9.0]  bpy.ops.mesh.bevel
  sig: bpy.ops.mesh.bevel(offset_type='OFFSET', offset=0, ...)
  doc: Cut into selected items at an angle to give a bevelled finish
...
```

Restrict to a single API namespace with `--kind` (one of: op | bmesh_op | class | method | function):

```bash
topos bpy-docs search "uv unwrap" --kind op            # bpy.ops.* only
topos bpy-docs search "intersect" --kind bmesh_op       # bmesh.ops.* only
topos bpy-docs search "rotation matrix" --kind class    # mathutils classes (Matrix, Quaternion, ...)
```

The Bash tool must be in your `allowed_tools` for this to work; it normally is
for tasks that have `topos_bpy_docs` in their `skills` list.

## When to actually call it

Don't call this for symbols you're 99% sure about — `bpy.ops.mesh.primitive_cube_add` you know, `obj.location` you know. Use it when:

- You're about to use an op you've used rarely (anything in bmesh.ops, advanced shader nodes, animation, particles, drivers)
- You're getting an `AttributeError` from a name you guessed
- A parameter name is non-obvious (e.g. is it `axis` or `direction`?)
- You want to discover what operators exist for a concept (search "boolean", "subdivide", "extrude")

A single call returning 5 matches is essentially free relative to the cost of writing broken code that fails at Blender execution time.

## Querying tips

- Multi-word queries score against name AND docstring — use natural language like `"bevel mesh edges"` or `"smart project uv unwrap"`
- Restrict to a kind with `--kind bmesh_op` when you specifically want bmesh ops
- If a top result has the right name but you want more detail, the result includes `short_doc` — usually that's enough; if not, you can grep the index file directly at `~/.config/topos/bpy_docs.json`

## When the index might be stale

The index is built once per Blender version. If the user upgrades Blender, the framework's `topos doctor` will hint at re-indexing. If a search comes back with no matches for something you'd expect to exist, suggest the user re-run `topos bpy-docs index`.

## Cross-version knowledge (deprecations & deltas)

The signature-level information from `topos bpy-docs search` matches the
pinned Blender binary, but it doesn't tell you whether an API is on its
way out, or whether the user might be upgrading soon. For that, see
`topos/bpy_docs/version_notes/`:

- `current_pinned.md` — which Blender Topos uses today + upstream state
- `5_0_to_5_1.md` — verified deltas if upgrading the pinned binary
- `blender_6_0_outlook.md` — announced removals / things to avoid in new code (e.g. **`bgl` is gone — use `gpu`**)

Read those when (a) you're writing code that touches viewport drawing,
compositor, or Geometry Nodes (active areas of churn), or (b) you're
unsure whether an old pattern is still recommended.
