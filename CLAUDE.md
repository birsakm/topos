# OpenTopos

Multi-agent orchestrated, code-driven 3D object generation framework. Coding agents (claude CLI is default; codex and gemini CLI backends are also implemented) + Blender + VLM judge + planner collaborate to produce **standalone, multi-file Python projects** that build 3D content from scratch.

This is the unified line: a single framework generates **both static/rigid objects and articulated objects** (a static object is an articulated object whose joints are all `fixed` — a connected single-root joint tree, never a jointless multi-root design), with a **texture subsystem** (`design.json` `texture.kind = procedural | image`; `uv_atlas` exists as an experimental rocket-example-only path, not wired into the default plan) and **trajectory analysis** for failed/low-quality runs. It merges the former `topos3d` `master` (rigid + articulated base) and `articulated-objects` (UV-atlas texture, image texture, trajectory analysis, reference-image support) branches. The Python package and CLI are still named `topos`; the repository and distribution are named `opentopos`.

Detailed architecture: `docs/architecture.md`. This file is the standing context for any Claude session in this repo.

## 12 working rules (apply to every task)

Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

1. **Think before coding.** State assumptions explicitly. If uncertain, ask rather than guess. Present multiple interpretations when ambiguity exists. Push back when a simpler approach exists. Stop when confused — name what's unclear.
2. **Simplicity first.** Minimum code that solves the problem. Nothing speculative. No features beyond what was asked. No abstractions for single-use code. Test: would a senior engineer say this is overcomplicated? If yes, simplify.
3. **Surgical changes.** Touch only what you must. Clean up only your own mess. Don't "improve" adjacent code, comments, or formatting. Don't refactor what isn't broken. Match existing style.
4. **Goal-driven execution.** Define success criteria. Loop until verified. Don't follow steps mechanically — define success and iterate. Strong success criteria let you loop independently.
5. **Use the model only for judgment calls.** Use the LLM for: classification, drafting, summarization, extraction. Do NOT use it for: routing, retries, deterministic transforms. If code can answer, code answers.
6. **Token budgets are not advisory.** Per-task: 4,000 tokens. Per-session: 30,000 tokens. If approaching budget, summarize and start fresh. Surface the breach — do not silently overrun.
7. **Surface conflicts, don't average them.** If two patterns contradict, pick one (more recent / more tested). Explain why. Flag the other for cleanup. Don't blend conflicting patterns.
8. **Read before you write.** Before adding code, read exports, immediate callers, shared utilities. "Looks orthogonal" is dangerous. If unsure why code is structured a way, ask.
9. **Tests verify intent, not just behavior.** Tests must encode WHY behavior matters, not just WHAT it does. A test that can't fail when business logic changes is wrong.
10. **Checkpoint after every significant step.** Summarize what was done, what's verified, what's left. Don't continue from a state you can't describe back. If you lose track, stop and restate.
11. **Match the codebase's conventions, even if you disagree.** Conformance > taste inside the codebase. If you genuinely think a convention is harmful, surface it — don't fork silently.
12. **Fail loud.** "Completed" is wrong if anything was skipped silently. "Tests pass" is wrong if any were skipped. Default to surfacing uncertainty, not hiding it.

## Core invariants (don't break)

- **Standalone outputs.** Every `outputs/<slug>/` must run without depending on this framework after `topos freeze`. Zero `from topos...` imports in frozen output.
- **Code is truth.** Procedural Python is the authoritative form. Meshes / URDF / GLB / renders are derivative artifacts under `artifacts/`, freely deletable.
- **Stateless Blender by default.** `blender --background --python script.py` per build. Hot process pool is a cache only; results must reproduce identically without it.
- **Plugin paths, not core edits.** New backends / judges / rubrics / skills / tools / domains / prompts go in their respective dirs. Core orchestrator and tool registry are closed for modification.
- **Design.json contract for multi-part objects.** When parts are written by independent agent tasks, they program against the same `design.json` (frozen). `build.py` validates each part's world bbox against the contract (5mm tolerance) — independent agents stay aligned without inter-talk.
- **Prompt layering.** `topos/prompts/system/` (framework prompts) < `topos/prompts/<domain>/` (domain-generic Jinja2 templates) < `examples/<slug>/prompts/` (per-example specifics). All loaded into agents at task time.

## Architecture in one screen

```
L7 CLI                   topos make (the entry) | run | doctor | config | cost | analyze | skill | bpy-docs
L6 Domain workflows      rigid / articulated  (each is a plan.json template + rubric + examples)
L5 Orchestrator          DAG runner (AgentTask | ToolTask | SubgraphTask), iter_policy fix-loop, runtime expansion via topos/orchestrator/expand.py (ADR-0008)
L4 Critic                Critic protocol; ClaudeVisionCritic default; rubric YAML decoupled from code
L3 Knowledge layers      topos/skills/topos_*/SKILL.md (agent-invoked capability bundles) · topos/bpy_docs/ (local Blender API RAG; index at ~/.config/topos/bpy_docs.json)
L2 Tools                 @tool-registered capabilities (see `topos/tools/registry.py` — every `@tool(...)` decorator under `topos/tools/` is the authoritative set)
L1 Agent backends        AgentBackend protocol · ClaudeCLIBackend (default; model pinned in config) · CodexCLIBackend · GeminiCLIBackend
L0 Substrate             Workspace · Blender runtime (stateless + hot-pool stub) · process · logging · config (defaults/user/repo/env)
```
*SubgraphTask is reserved for scene/city domains; not yet implemented.

## Current stage

**Stage 2 articulated working end-to-end as of 2026-05-11.**

- `examples/articulated_drawer_cabinet/` reliably passes the judge (score ~0.65-0.75, threshold 0.65) in a single iter — 6 agent tasks (design / 3 parts / build / joints) + 4 tool tasks (render_multiview EEVEE / export_glb / export_urdf / judge), ~$1.5-2.2 per run, ~4-7 min wall time.
- Multi-file output (`src/parts/*.py` + `src/build.py` + `src/design.json` + `src/joints.yaml`) with bbox-contract validation.
- Per-part GLB + whole-scene GLB + valid URDF — all parseable by trimesh / urdfpy / Blender.
- Prompt-folder reorg (Phase A+B 2026-05-11): all agent prompts live as plain files under `topos/prompts/` and `examples/<slug>/prompts/`; plan.json references via `goal_template: topos:...` + `goal_params: {extras_file: ./prompts/...}`.
- Skills v2 (2026-05-11): `topos/skills/topos_<name>/SKILL.md` capability bundles, plan.json `skills: [...]` field. Runner materializes each SKILL.md into `workspace/.topos_skills/` and emits a soft hint listing them; the agent autonomously Reads only the SKILL bodies whose `when_to_use` matches the task. `topos skill install --target {claude|codex|opencode}` also copies them into the agent runtime's global skills dir for native discovery.
- Fix-loop honors `iter_policy.max_global_iters`; auto-builds a FIX99 agent task from judge feedback when score < threshold.
- Cost tracking per task + per iter + total ($/usage/cache_hit) in `run_report.json` and `topos cost <slug>`.

**Validation ladder.** Stage 0 smoke ✓ · Stage 1 rigid ✓ · **Stage 2 articulated ✓** · Stage 3 scene (not started) · Stage 4 city (far future).

**Next chunks (in `memory/` as deferred tasks):**
- Texture infra refinement: image-conditioned per-part textures (Gemini Nano Banana already wired for the procedural-bake path)
- More furniture-grade recipes (hinges, drawer rails) beyond `topos_furniture_hardware`
- Scene domain + SubgraphTask + fanout
- `topos make` support for `rigid` domain (currently articulated only)

## Common commands

```bash
topos doctor                                       # check python / claude CLI / blender / config
topos config init / get / set / show / edit        # config layered (defaults < user < repo < env)
topos make "<NL prompt>" [-i ref.png ...] [--slug <s>] [--no-run]
                                                   # THE entry: prompt (+ optional reference images) → workspace → auto-run
                                                   #   writes prompts/intent.md + fixed articulated plan; design agent derives parts at runtime (no spec step)
topos run <slug> [--base <outputs_dir>]            # re-execute an existing workspace's plan.json (e.g. after --no-run)
topos cost <slug> [--by-model]                     # last-run cost & token breakdown
topos skill list                                   # list shipped topos_* skills
topos skill install --target claude                # copy topos_* skills to ~/.claude/skills/ for native discovery
topos skill uninstall --target claude              # remove them
# topos freeze <slug>                              # (planned) emit standalone project; not yet implemented
pytest tests/                                       # unit suite (fast, no Blender/LLM; `-m 'not integration'` is the default)
```

## Memory layout (where things live across sessions)

- `CLAUDE.md` (this file) — standing context. Edited by hand or by an agent that explicitly intends to update it. Not auto-overwritten.
- `docs/decisions/NNNN-*.md` — ADRs. One file per locked architectural decision. Append new files; mark superseded with `Status: Superseded by NNNN`.
- `docs/lessons.md` — append-only running log of gotchas, version-specific quirks, debugging insights. Dated + linked to commit.
- `docs/architecture.md`, `docs/extending.md`, `docs/config.md` — current-state reference docs.
- `~/.claude/projects/-lab-yipeng-topos/memory/` — cross-session memory (per-user, never in repo): user prefs, deferred chunks, project-level state.
- `outputs/<slug>/trajectories/<task_id>_iter<N>/` — full transcript + cost + score per task per iter. Per-run scope, gitignored.
- `outputs/` — **single canonical drop zone** that every `topos run` / `topos make` writes into directly. Flat layout, one subdir per run: `outputs/<slug>/` with `src/` + `artifacts/` + `trajectories/` + `run_report.json`. Gitignored. **Do not invent new top-level folders** (`outputs/diag/`, dated subdirs, etc.) — flat `outputs/<slug>/` only. Failed / exploratory runs are pruned manually with `rm -rf outputs/<slug>/`; for spec-only dry runs (`topos make --no-run`) pass `--base /tmp/topos-spec-...` to keep them outside the repo entirely.
- `~/.config/topos/config.yaml` — machine/account specifics (Blender binary path, API keys). Not in repo.

## Pointers

- ADRs: 0001 code-as-truth · 0002 stateless-blender · 0003 claude-cli backend · 0004 recipe-injection · 0005 modeling-vs-rendering separation · 0006 design-json bbox-contract · 0007 three-layer-prompts · 0008 subgraph-runtime-expansion
- Recursive DAG sketch: `docs/architecture-recursive-dag.md`
- Architecture: `docs/architecture.md`
- Extending the framework: `docs/extending.md`
- Config schema: `docs/config.md`
