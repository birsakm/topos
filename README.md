# OpenTopos

Unified, code-driven 3D object generation — **static/rigid + articulated + texture (incl. UV atlas)** in one framework. Merged from the former `topos3d` `master` and `articulated-objects` branches. The Python package and CLI remain `topos`; the repo and distribution are `opentopos`.

**TODO List**

1: Prototype
- [P0] [Slice A landed] Reoganize the prediction flow, constructing a DAG architecture using graph node to represent each agent step. Articulated single-object: `SubgraphTask` runtime expansion driven by `design.json` (ADR-0008, `docs/architecture-recursive-dag.md`). Scene / city tiers deferred to slices B & C.
- [P0] Trajetories AutoAnalyzer. A Skill or other type to prompt coding agents to automatically analyze the failure mode of the generated 3D objects and potential things that can be improved.
- [P1] Website frontend to visualize the step of each generated objects on how agents collaborate or work together to build a 3D objects.
- [P1] Support image as input to do image-to-3D via procedural blender code.
- [P1] Scale the scope from single objects (single static object or articulated objects) to 3D scene.
- [P1] Support take .blend project file as supports and seek potential way to do reverse engineering for further data curation.
- [P2] Support Three.js programming language.  

**Code-driven, multi-agent 3D content generation — from scratch.**

Topos lets coding agents (Claude via the `claude` CLI is the default; Codex and Gemini CLI backends are also wired up) collaborate with Blender, a VLM judge, and a planner to produce **standalone, multi-file Python projects** that build 3D assets and scenes. No mesh-prior models, no diffusion, no asset libraries — every object is constructed procedurally, one `bpy` call at a time, by an LLM that writes, runs, inspects renders, and fixes its own code in a loop.

The ambition is a single coherent framework that scales along one axis — **complexity of the scene** — while keeping the same primitives:

```
  rigid object  →  articulated object  →  indoor / outdoor scene  →  city / world
  (a chair)     →  (a drawer cabinet)   →  (a furnished room)      →  (a neighborhood)
```

Each level reuses the level below: a scene is a layout of articulated/rigid objects; a city is a layout of scenes. The agents, tools, judge, and fix-loop are shared infrastructure; what changes is the planner's DAG template and the domain-specific knowledge skills.

---

## Why "code is truth"

Most 3D generative pipelines emit meshes as the primary artifact. Topos emits **Python source code** as the primary artifact, and meshes / GLB / URDF / renders are derivative — regeneratable from the code, freely deletable. This buys three things:

- **Editability.** A human (or another agent) can open `src/parts/drawer.py` and change the drawer height by editing one line. No mesh surgery.
- **Composition.** A scene that places ten cabinets just imports `build_cabinet()` ten times with different params. No re-generation of geometry.
- **Standalone outputs.** After `topos freeze` (planned), each `outputs/<slug>/` runs in any Blender environment without the framework. The code is the deliverable; Topos is just the scaffolding that produced it.

---

## Current status (2026-05-11)

| Stage | What | Status |
|---|---|---|
| 0 | Smoke test (`blender --background` + claude CLI plumbing) | ✓ working |
| 1 | **Rigid** single-object (e.g. a chair) | ✓ working, ~$0.30/run |
| 2 | **Articulated** multi-part object (frame + drawer + handle, URDF joints) | ✓ working, ~$1.5–2.2/run, judge score ≥ 0.65 in single iter |
| 3 | **Scene** (multi-object layout: furnished room, garden) | not started |
| 4 | **City** (district-scale layout, repeated typologies) | far future |

The cabinet example (`examples/articulated_drawer_cabinet/`) is the running benchmark. End-to-end it runs 6 agent tasks (design / 3 parts / build / joints) + 4 tool tasks (multi-view render / GLB / URDF / judge) in ~4–7 minutes wall time, producing a parseable URDF that loads cleanly in `trimesh` / `urdfpy` / Blender.

---

## How it works in one screen

```
NL prompt or spec.yaml
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  Planner  →  plan.json (DAG of AgentTask | ToolTask)            │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼  topo-sorted, run in waves
┌─────────────────────────────────────────────────────────────────┐
│  01_agent_design      writes  src/design.json   (frozen contract)│
│  02_agent_part_frame  writes  src/parts/frame.py                │
│  03_agent_part_drawer writes  src/parts/drawer.py               │
│  04_agent_part_handle writes  src/parts/handle.py               │
│  05_agent_build       writes  src/build.py  (asserts bbox vs    │
│                                              design.json ±5 mm) │
│  06_agent_joints      writes  src/joints.yaml                   │
│  07_tool_render_multiview  →  artifacts/object_render/view_*.png│
│  08_tool_export_glb        →  artifacts/object.glb              │
│  09_tool_export_urdf       →  artifacts/object.urdf + parts/    │
│  10_tool_judge             →  trajectories/.../score.json       │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼  score < threshold?  →  auto-build a FIX99 agent task from
                                  judge feedback; re-run downstream
                                  (max_global_iters in iter_policy)
```

Agents **never talk to each other**. They collaborate by writing into a shared `outputs/<slug>/src/` workspace, programming against the frozen `design.json` contract that the design agent wrote first. The bbox-assertion in `build.py` is the cheap consistency check that catches drift between independently-generated parts.

---

## Quickstart

```bash
# Install
pip install -e .

# Check environment (Python / claude CLI / Blender / config)
topos doctor

# Scaffold a workspace from a worked example
topos init my_cabinet --domain articulated --from-example articulated_drawer_cabinet

# Run the DAG
topos run my_cabinet

# Inspect cost & token breakdown
topos cost my_cabinet --by-model

# (Planned) one-shot from a natural-language prompt
topos make "a small wooden nightstand with two drawers and brass pulls"
```

Outputs land under `outputs/my_cabinet/`:

```
outputs/my_cabinet/
├── src/                    # agent-written Python — the deliverable
│   ├── design.json         # frozen part/joint contract
│   ├── parts/
│   │   ├── frame.py
│   │   ├── drawer.py
│   │   └── handle.py
│   ├── build.py            # imports + composes parts in a Blender scene
│   └── joints.yaml         # URDF-style link + joint spec
├── artifacts/              # derivative — view_*.png, object.glb, object.urdf
└── trajectories/           # per-task transcripts, costs, judge scores per iter
```

---

## Architecture in one screen

```
L7  CLI                 topos doctor | config | init | run | cost | make*
L6  Domain workflows    rigid · articulated · scene* · city*
L5  Orchestrator        DAG runner (AgentTask | ToolTask | SubgraphTask*)
                        + iter_policy fix-loop
L4  Critic              Critic protocol · ClaudeVisionCritic (default)
                        · rubric YAML decoupled from code
L3  Knowledge layers    skills/topos_*/SKILL.md (agent-invoked capability bundles)
                        bpy_docs/ (local Blender API RAG index)
L2  Tools               render_multiview · render_part · verify_parts
                        · generate_texture_image · export_glb · export_urdf · judge
L1  Agent backends      AgentBackend protocol
                        · ClaudeCLIBackend (default; pinned model)
                        · CodexCLIBackend, GeminiCLIBackend (implemented)
L0  Substrate           Workspace · Blender runtime (stateless + hot-pool stub)
                        · process · logging · layered config
```

Closed-for-modification core; new backends / critics / rubrics / skills / tools / domains / prompts go in their respective dirs. Full detail in [`docs/architecture.md`](docs/architecture.md).

---

## Roadmap

Near-term (next chunks queued):

- **Texture stage refinement** — `ImageGenBackend` (Gemini Nano Banana) is wired and UV-bake works for procedural materials; image-conditioned per-part textures are next.
- **Furniture-hardware recipes** beyond the current `topos_furniture_hardware` skill (hinges, drawer rails) so parts stop looking like primitive-cube assemblies.

Stage 3 onwards:

- **Scene domain** — `SubgraphTask` fanout, layout constraints, multi-object collision/placement, indoor + outdoor.
- **City domain** — repeated scene typologies, road graphs, far-future.
- **bpy docs RAG** — auto-index the installed Blender's Python docs so agents can look up API surface they don't remember.

`topos freeze` (project portability) is also on the list but blocked on Stage 3 stabilizing first.

---

## Repo layout

```
topos/
├── topos/              # framework package
│   ├── cli.py
│   ├── workspace.py
│   ├── backends/       # claude / codex / gemini agent backends
│   ├── orchestrator/   # plan schema + DAG runner + tasks
│   ├── blender/        # subprocess runtime + render / export wrappers
│   ├── tools/          # tool registry (render_multiview, render_part, export_glb, judge, ...)
│   ├── agents/visual_critic/  # Critic protocol + ClaudeVisionCritic / CLI critics / API critics
│   ├── rubrics/        # articulated_object_v1.yaml, part_shape_v1.yaml
│   ├── prompts/        # system + per-domain Jinja2 templates
│   ├── skills/         # topos_part_geometry / topos_joints_creator / ...
│   └── urdf.py         # URDF writer; vendored into frozen projects on `topos freeze`
├── examples/
│   ├── articulated_drawer_cabinet/
│   ├── articulated_rocket/
│   ├── jet_engine_v4/
│   └── optimus_prime_bay_v1/
├── docs/
│   ├── architecture.md
│   ├── extending.md
│   ├── config.md
│   ├── lessons.md      # append-only running log of gotchas
│   └── decisions/      # ADRs (0001-code-as-truth, 0002-stateless-blender, ...)
├── tests/              # 38 unit + 1 integration (real claude + blender, ~$0.30)
└── CLAUDE.md           # standing context for Claude sessions in this repo
```

---

## Pointers

- **Architecture detail** — [`docs/architecture.md`](docs/architecture.md)
- **Adding a backend / judge / tool / domain** — [`docs/extending.md`](docs/extending.md)
- **Config schema (layered: defaults < user < repo < env)** — [`docs/config.md`](docs/config.md)
- **Architecture decisions** — [`docs/decisions/`](docs/decisions/)
  - 0001 code-as-truth · 0002 stateless-blender · 0003 claude-cli backend
  - 0004 recipe-injection · 0005 modeling-vs-rendering separation
- **Gotchas & version-specific quirks** — [`docs/lessons.md`](docs/lessons.md)

---

## Status disclaimer

This is research-stage software (v0.0.1). The rigid and articulated pipelines work reliably; everything above Stage 2 is design-on-paper. The API surface, plan.json schema, and skill format will change. Don't depend on the standalone-freeze invariant yet — `topos freeze` isn't implemented.

\* = planned, not yet shipped.
