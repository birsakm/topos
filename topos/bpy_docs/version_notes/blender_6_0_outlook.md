# Blender 6.0 outlook — known removals/deprecations

This file lists things **already removed or officially announced** for
removal, verified against `docs.blender.org/api/<ver>/` and Blender's
public release notes. We do not speculate about unannounced changes.

As of 2026-05-11, Blender 6.0 has not been released and no firm release
date is published. The notes below are forward-looking for new code we
write today.

## Already removed (do not use)

### `bgl` module — GONE

`bgl` (OpenGL bindings module) was deprecated in 3.x, removed by 4.x.
It is **404** on `docs.blender.org/api/5.1/bgl.html`. Any code using
`import bgl` will fail at import time on any Blender ≥ 4.0.

**Replacement:** `gpu` module
(`https://docs.blender.org/api/5.1/gpu.html`). `gpu` covers shader
binding, batch drawing, framebuffer management. The API shape is
different — it's not a drop-in.

**Topos relevance:** none of our code uses either. Mentioned here so any
future viewport overlay / custom-draw work goes straight to `gpu`.

## Categories worth watching for 6.0

When `docs.blender.org/api/6.0/change_log.html` exists, the categories most
likely to affect Topos are (based on what 5.x kept churning):

1. **Compositor.** The 5.1 rewrite removed ~80 classes. Further changes
   in 6.0 are likely. We don't use it, but if we ever add a post-process
   pass, plan for further movement.
2. **Geometry Nodes (`bpy.types.GeometryNode*`).** Active development;
   parameter sets and node types churn every release. We don't use them
   yet; if a future part uses GN, write code defensively and pin a
   version.
3. **EEVEE.** 5.1 added scene-level intensity multipliers. 6.0 may
   rename or split the EEVEE settings further (Cycles vs EEVEE-Next
   parity is an ongoing project). The `render_wrapper.py` defaults we set
   today are conservative; revisit when we upgrade.

## Categories that are essentially stable

These have been stable for many releases and are safe to lean on:

- `bpy.ops.mesh.primitive_*` — the "add a cube/cylinder/uv_sphere"
  operators. Same names, same default `size` semantics for years.
- `bmesh.ops.bevel`, `bmesh.ops.inset_individual`, `bmesh.ops.bridge_loops`,
  the boolean ops (`bmesh.ops.symmetrize`, etc.) — bmesh has been
  remarkably stable.
- `mathutils.Vector`, `mathutils.Matrix`, `mathutils.Euler`,
  `mathutils.Quaternion` — public API frozen since ~2.8.
- Modifier types via `obj.modifiers.new(name, type)` — surface stable;
  individual modifier properties occasionally tweaked.
- `bpy.data.materials.new`, the shader node graph (`material.node_tree`)
  surface — stable; individual node types may gain/lose inputs (e.g. the
  5.1 `ShaderNodeNormalMap` additions above).

## How to use this file as an agent

If you're about to use an API that you think might be on the way out,
search this file's headings before writing code. If it's not listed here,
assume it's stable. If it IS listed, follow the replacement guidance or
ask the user.

For day-to-day API lookups (signatures, parameter names, return shapes),
use `topos bpy-docs search "<query>"` instead — that hits the live index
of the binary you'll actually run against.
