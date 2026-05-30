# ADR 0005 — Separate modeling from rendering; agent writes pure geometry

- **Date:** 2026-05-10
- **Status:** Accepted

## Context

In the Stage 0 smoke and the first chair iteration, the agent's `src/build.py` had to do everything: build geometry, place a camera, add lighting, set render engine, call `bpy.ops.render.render`. This produced four problems:

1. Agent prompts had to spell out camera/render config — these are concerns the agent shouldn't have to relearn for every object.
2. Eval rendering was inconsistent across projects (different cameras, different angles, different lighting), making judge scores less comparable.
3. Single-view rendering gave the judge limited information (a chair from one angle may hide a missing leg).
4. The framework couldn't standardize a presentation-grade render (turntable GIF, multi-view sheet) without rerunning Blender with different scripts.

Infinigen's `data_pipeline_operators/renderer.py` solves a similar problem cleanly: a separate Blender script `runpy`s the user's geometry script, then takes over camera/lighting/render with framework-owned logic.

## Decision

The agent's `src/build.py` is **pure geometry**: it places mesh objects in the scene, sets `obj.color` or builds materials, and stops. It must not add cameras or lights, set `scene.render.*`, or call `bpy.ops.render.render`.

The framework owns rendering through:

- `topos/tools/render/wrapper.py` — a standalone Blender script (no `topos` imports; runs in Blender's bundled Python). Reads the agent's geometry script via `runpy`, strips any cameras/lights the agent accidentally adds, computes the bbox, places a framework-owned camera, configures the engine, renders.
- `topos/tools/render.py` — three registered tools (`render`, `render_multiview`, `render_turntable`) that spawn `blender --background --python render_wrapper.py -- <args>`. These replace `blender_run` for the eval/presentation path.

Two coloring modes:

- `as_authored` — preserves the agent's own `obj.color` / materials (used by default; what most production renders want).
- `palette` — overrides with a 5-color research palette so distinct parts read clearly (good for articulated objects and structure-check views).

Three render modes:

- `render` — one octant view (cheapest iteration).
- `render_multiview` — eight standard octant views (the canonical judge eval set).
- `render_turntable` — N-frame rotation around the object (PNG frames at v1; GIF/MP4 assembly arrives when Pillow / imageio are added).

## Alternatives considered

1. **Keep mixed concerns; agent writes camera + render too.** Rejected for the four reasons above.
2. **Two `build.py` files per project: `geometry.py` + `render.py`.** Rejected: still asks the agent to maintain rendering logic, and the framework can't enforce eval consistency.
3. **Use Blender's CLI-level `--render-anim` flags.** Rejected: too inflexible for multi-view; we want code-level camera control.

## Consequences

- Agent prompts shrink. The chair example drops from ~50 lines (with camera/render) to ~30 lines of pure geometry.
- Judge sees 8 angles by default → score variance drops and recognition of missing/hidden parts improves.
- `blender_run` remains for ad-hoc scripts that *do* manage their own render (smoke_hello_blender) and for non-render Blender tasks (URDF export, mesh validation).
- **Standalone-output invariant (ADR 0001) impact:** A frozen project's `src/build.py` alone does not produce a render — it produces geometry. To make frozen projects fully self-contained, `topos freeze` will copy `render_wrapper.py` (and any required helpers) into `outputs/<slug>/vendored/blender/`, and emit a small entrypoint `outputs/<slug>/render.py` that invokes the vendored wrapper. This is consistent with ADR 0004's "heavy machinery is vendored at freeze".
- Workbench remains the default engine for both eval and quick iteration (deterministic, no GPU). Cycles is opt-in for final presentation and requires a GPU detection branch (TODO; pattern in infinigen's `_setup_gpu_cycles`).
- The wrapper file lives under `topos/tools/` (host) + `topos/tools/{render,export,verify}/wrapper.py` (Blender-side) but must not `import topos.*` — it runs in Blender's bundled Python, which has no access to the host venv.
