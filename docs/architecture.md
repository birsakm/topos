# Architecture

Topos generates 3D content by orchestrating coding agents that write Python projects, executing them in Blender, and judging the visual output. This document is the current-state reference, last refreshed 2026-05-11.

## Mental model

Picture a small assembly line. You input intent (a natural-language description or a structured `spec.yaml`). The framework decomposes it into a DAG of tasks, each task is either an LLM "agent" writing some files, or a deterministic "tool" running a Blender subprocess. Agents collaborate by writing into a shared workspace (`outputs/<slug>/`); they don't talk to each other directly. The output is a multi-file Python project that, when run, reconstructs the 3D content — plus baked artifacts (multi-view renders, GLB, URDF).

## The layers (bottom up)

### L0 — Substrate (`topos/workspace.py`, `topos/process.py`, `topos/config.py`, `topos/doctor.py`, `topos/tools/_blender_subprocess.py`)

- **Workspace** — every produced project lives at `outputs/<slug>/` with canonical subdirs `src/` (agent-written code), `artifacts/` (renders, GLB, URDF, gitignored), `trajectories/<task>_iter<N>/` (per-task logs).
- **Blender runtime** — `run_blender(script, cwd=...)` invokes `blender --background --python script.py`. Stateless by default. A hot-pool socket-server is stubbed for future iteration speedups.
- **Process helper** — subprocess wrapper with timeout, env-var injection, bounded stdout/stderr capture.
- **Config** — layered: built-in defaults < `~/.config/topos/config.yaml` (user-global) < `./topos.config.yaml` (repo-local) < `TOPOS__SECTION__KEY=...` env vars. `topos doctor` probes the environment and points at the right knob when something's missing.

### L1 — Agent backends (`topos/backends/`)

`AgentBackend` is a Protocol: `run(prompt, workspace, allowed_tools, mcp_servers, timeout_s, ...) -> AgentRunResult`.

`ClaudeCLIBackend` is the default. It spawns `claude -p <prompt>` with `--output-format json --no-session-persistence --permission-mode bypassPermissions --add-dir <workspace> --allowed-tools <list> --model <pinned>`, parses the JSON envelope, and surfaces `total_cost_usd` + `usage` (per-token cache hit/miss) on the result. Auth is either subscription mode (no API key) or `api_key` mode (requires `ANTHROPIC_API_KEY`).

`CodexCLIBackend` (codex-cli 0.128+) and `GeminiCLIBackend` (gemini-cli 0.41+) are also implemented — they spawn the respective CLI as a subprocess, capture stdout/stderr to the trajectory dir, and surface cost where the CLI emits it. Both honor the same `AgentBackend.run(...)` protocol as ClaudeCLIBackend. MCP server config and `allowed_tools` are honored by `ClaudeCLIBackend` only; the codex and gemini backends warn-and-ignore those parameters (their CLIs manage MCP and tool policy via persistent config, not per-call args).

### L2 — Tools (`topos/tools/`)

`tool_registry` is a decorator-driven dict of deterministic capabilities, each invoked directly by a `ToolTask` in the DAG. They are NOT exposed to the coding-agent backends (agents are launched with `mcp_servers=[]`; an agent's tools are only its `allowed_tools` — Read/Edit/Write/Glob/Bash). Current tools:

| Tool | What it does | Where the heavy lift is |
|---|---|---|
| `render_multiview` | 8 standard octant views (eval set for the judge) | `topos/tools/blender_render/wrapper.py` |
| `render_part` | Per-part isolated views for the component critic | same wrapper, mode flag |
| `verify_parts` | Each `parts/<name>.py` builds (buildability gate) | `topos/tools/blender_verifier/` |
| `generate_texture_image` | Per-part Gemini Nano Banana texture PNG | `topos/tools/generate_texture_image.py` |
| `export_glb` | Bake all transforms, export whole-scene GLB | `topos/tools/export/wrapper.py` |
| `export_urdf` | Per-part GLB + URDF that references them | `export_wrapper.py` + `topos/urdf.py` |
| `judge` | Load rubric, dispatch to `Critic.evaluate` | `topos/agents/visual_critic/` |

Tools are MCP-ready (each is a registered function with JSON Schema input/output) but not yet wired as a separate MCP server process — agent backends can call them via the existing CLI tools (Read/Edit/Bash) and the framework dispatches `ToolTask`s directly. MCP wiring is on the roadmap once the agent needs to invoke tools mid-conversation.

### L3 — Knowledge layers

Two coexisting forms — both **agent-invoked** (the agent decides when to load each), neither auto-injected:

- **Skills** at `topos/skills/topos_<name>/SKILL.md` — task-shaped capability bundles. The shipped set covers: `topos_part_geometry`, `topos_joints_creator`, `topos_design_articulated`, `topos_furniture_hardware`, `topos_bpy_docs`, `topos_texture_creator`, `topos_geometry_contracts`. Each SKILL.md has YAML frontmatter (`name`, `description`, `when_to_use`, `provides`, `related_tools`) + an instructional body with worked code examples. Plan.json declares `skills: [...]` per agent task; the runner materializes the SKILL files into `workspace/.topos_skills/` and gives the agent a soft hint listing them (the agent uses `Read` to pull a SKILL body only if it's relevant).
- **Blender API RAG** at `topos/bpy_docs/` — code that introspects the installed Blender via `--background --python` and writes a flat JSON index (~2,600 symbols across `bpy.ops`, `bmesh.ops`, `mathutils`). Agents query via `topos bpy-docs search "<query>"` from Bash (the `topos_bpy_docs` SKILL teaches them when). Cross-version curated notes live in `topos/bpy_docs/version_notes/`.

### L4 — Critic (`topos/agents/visual_critic/`, `topos/rubrics/`)

`Critic` is a Protocol: `evaluate(inputs, rubric) -> CriticResult`. `ClaudeVisionCritic` is the default: it copies images to a scratch dir, builds a structured prompt from a YAML rubric, calls `ClaudeCLIBackend` with `--allowed-tools Read` so the agent reads images via the Read tool, then parses the structured JSON response (with balanced-brace fallback for unwrap edge cases).

Rubrics are YAML files: criteria with weights + descriptions, plus a pass threshold. Current: `articulated_object_v1` (assembly), `part_shape_v1` (per-part critic). The `geometry_detail` and `fit_quality` criteria added to v1 force the judge to look beyond "is something visible" into "is it actually furniture-grade".

### L5 — Orchestrator (`topos/orchestrator/`)

- **Tasks** (`tasks.py`): `AgentTask`, `ToolTask`, and `SubgraphTask` dataclasses. SubgraphTask is the runtime fan-out primitive — its children are synthesized after deps complete, not enumerated up-front (ADR-0008, `docs/architecture-recursive-dag.md`).
- **Plan schema** (`plan_schema.py`): Pydantic models for the plan JSON. Validates and resolves task references. Three goal source options for an agent task: `goal` (inline), `goal_file` (path to a file, resolved via `topos:` URI scheme or relative), `goal_template + goal_params` (Jinja2 rendering, with `*_file` params auto-resolved to file contents).
- **Runner** (`runner.py`): topo-sorts the tasks, runs in order, collects results. When a `SubgraphTask` becomes ready, the runner inline-expands it by reading the parent agent's design doc and calling the registered strategy in `expand.py`; children are spliced into the live DAG with namespaced ids `<subgraph>__<child>` and a `plan.expanded.json` snapshot is persisted. Honors `iter_policy.max_global_iters` — after a judge fail, the runner constructs a `99_agent_fix` AgentTask whose goal is rendered from `topos/prompts/system/fix_loop.md.j2` (per-criterion feedback + suggested fixes). Re-runs ToolTasks; preserves all prior iters' trajectories under `trajectories/<id>_iter<N>/`. Tracks accumulated cost across iters in `run_report.json`.
- **Expand** (`expand.py`): expansion-strategy registry. Each strategy reads a parent's design doc (e.g., `design.json`) and emits a list of dynamic child tasks. Slice A ships `articulated_parts` (one part-agent + texture + judge_part per design part, plus batched verify/render). Future strategies: `scene_objects`, `assembly_edges`.

### L6 — Domains (`topos/prompts/<domain>/`, `topos/rubrics/`, `examples/`)

A "domain" is shorthand for "kind of object" (rigid / articulated / scene / city). Each domain has its own prompt templates (`topos/prompts/<domain>/`) and rubric (`topos/rubrics/<domain>_object_v*.yaml`). Examples in `examples/<slug>/` are concrete configurations — a `spec.yaml` (NL description) + a `plan.json` (the DAG) + a `prompts/` folder (per-example intent + extras).

Plan.json templating: agent tasks reference shared `topos/prompts/<domain>/*.md.j2` templates and pass per-task params via `goal_params: {part_name, lower_name, extras_file}`. Same template renders for Frame, Drawer, Handle.

### L7 — CLI (`topos/cli.py`)

`typer` app exposing `doctor`, `config {init/get/set/show/edit}`, `init`, `run`, `cost`. The `topos init <slug> --from-example <name> --base <outputs_dir>` flow copies the entire `examples/<name>/` (including the prompts/ subfolder) into a fresh workspace.

## The "design.json contract" pattern (Stage 2 onwards)

For multi-part objects the parts are written by separate agent tasks running in topo wave order (parts have no deps on each other, only on the design step). They never see each other's code. They stay aligned by all programming against `design.json` — a frozen, machine-readable contract written by the design agent first.

```
01_agent_design               → src/design.json (parts: [...], joints: [...])
02_agent_part_frame           → src/parts/frame.py (read design.json, implement build_frame() to the spec)
03_agent_part_drawer          → src/parts/drawer.py (same; different spec entry)
04_agent_part_handle          → src/parts/handle.py
05_agent_build                → src/build.py (import each builder, call it, bbox-assert vs design)
06_agent_joints               → src/joints.yaml (compute joint origins from design's world_xyz field)
07_tool_render_multiview      → artifacts/object_render/view_*.png × 8
08_tool_export_glb            → artifacts/object.glb (transforms baked)
09_tool_export_urdf           → artifacts/object.urdf + parts/*.glb
10_tool_judge                 → trajectories/10_tool_judge_iter0/score.json
```

`build.py` validates each `build_<part>()` returned object's world bbox center+extents against `design.parts[i].world_xyz` and `world_extents` with 5mm tolerance. Bbox WARN prints but doesn't raise — render proceeds so the judge sees the visual mistake (the bbox WARN itself isn't fed to the agent loop yet; future Skill-v2 might).

## Multi-file outputs and runpy sys.path

`src/parts/*.py` are imported as `from parts.frame import build_frame` from `src/build.py`. Topos's `render_wrapper.py` and `export_wrapper.py` insert `Path(script).parent.resolve()` at the front of `sys.path` before calling `runpy.run_path(script)`, so the imports resolve. Python 3 namespace packages mean no `__init__.py` is needed inside `parts/`.

## What's standalone-friendly

After `topos freeze` (not yet implemented), a project should be portable:

- `src/` — pure bpy + stdlib; no `topos*` imports
- `vendored/` — copies of any `topos/` modules the frozen project still needs at runtime (e.g. `topos/urdf.py` if the project re-emits URDF post-freeze). `topos freeze` is responsible for selecting + rewriting import paths in `src/`.
- `manifest.json` — pin Blender version + any other deps
- The result runs in any Blender environment regardless of whether Topos is installed

Skill content stays in `topos/skills/` (framework side); skills inform agent behavior at code-write time, but don't get embedded in the produced project's runtime.

## What's NOT here yet (cross-link)

- Image-conditioned per-part textures (the procedural-bake path via Gemini Nano Banana is wired; richer image conditioning is the next chunk) — see `memory/project_texture_design_v0.md`
- Scene domain (multi-object, SubgraphTask fanout) — design space in original plan
- `topos make` for non-articulated domains (currently `articulated` only; rigid is next)
- Richer furniture-hardware recipes (hinges, drawer rails) — see `memory/feedback_handle_detail_still_primitive.md`

The cabinet pipeline is the running benchmark — every architectural change is validated against it passing at score ≥ 0.65 in single iter, ~$1.5-2.2 per run.
