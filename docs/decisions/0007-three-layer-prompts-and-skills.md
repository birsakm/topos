# ADR 0007 — Three-layer prompts + skills/ for capability bundles

- **Date:** 2026-05-11
- **Status:** Accepted (skills v1; v2 refactor pending — see `memory/feedback_skills_should_be_agent_invoked.md`)

## Context

Agent prompts started as inline strings in `plan.json` (16+ KB per example), then as flat per-task files in `topos/prompts/articulated_drawer_cabinet/`. Two problems:

1. **Mixed abstractions in one place.** The cabinet's prompt mixed framework-level rules ("don't import from topos.*"), domain-level patterns ("articulated objects have a design.json"), example-level details ("cabinet outer is 30cm cube"). When extending to a chair or door, every prompt needed to be duplicated and edited.
2. **No place for accumulated knowledge.** Lessons like "transform_apply before join with thin-axis active" lived only in `docs/lessons.md` (not visible to agents) or were re-explained in every part prompt (token cost).

## Decision

Prompts live in three layers:

- **`topos/prompts/system/`** — framework-level. Loaded by Python code (runner, judge, fix-loop) via `topos.prompts.load_text` / `render`. Examples: `coding_agent_base.md`, `fix_loop.md.j2`, `vision_judge_base.md.j2`. Editing these changes behavior for every task in every example.
- **`topos/prompts/<domain>/`** — domain-generic. Jinja2 templates parameterized for any task in that domain (rigid, articulated, ...). Examples: `articulated/part_geom.md.j2`, `articulated/designer.md.j2`. Referenced from `plan.json` via `goal_template: topos:<domain>/<file>.md.j2` + `goal_params`.
- **`examples/<slug>/prompts/`** — example-specific. The slot-fillers for the domain templates. Examples: `intent.md` (fed as `{{ intent }}` to the designer template), `extras_<part>.md` (fed as `{{ extras }}` to the part_geom template per part).

Plan.json `goal_params` keys ending `_file` are auto-resolved as file references — the runner reads the file and binds its contents to the stripped key:

```jsonc
{
  "goal_template": "topos:articulated/part_geom.md.j2",
  "goal_params": {
    "part_name": "Frame",                  // → {{ part_name }}
    "extras_file": "./prompts/extras_frame.md"  // → {{ extras }} (file content)
  }
}
```

Skills live alongside as a fourth layer:

- **`topos/skills/topos_<name>/SKILL.md`** — capability bundles. Each is a self-contained "how to do X correctly" doc with YAML frontmatter (`name`, `description`, `when_to_use`, `provides`, `related_tools`). P0 set: `topos_part_geometry`, `topos_joints_creator`, `topos_design_articulated`.

Plan.json agent tasks declare `skills: [...]` to request specific skills be available. **In v1 (current), the runner concatenates each SKILL.md into the prompt at task time** (forced injection). This is architecturally wrong — agents should choose autonomously. **The v2 refactor** is to ship a `topos skill install [--target claude|codex|opencode]` CLI that copies skill folders into the runtime's discovery directory (`~/.claude/skills/`); the agent runtime auto-discovers and the agent invokes skills via its native Skill tool. `skills:` in plan.json becomes a soft hint section in the prompt. The refactor is queued; v1 functions correctly (handle bbox err dropped from 18mm to 0mm once `topos_part_geometry` was injected) just at higher token cost than necessary.

## Alternatives considered

1. **Keep prompts inline in plan.json.** Rejected: 16 KB JSON is unreadable; can't be reviewed or edited as plain markdown; doesn't enable cross-example reuse.
2. **Flat per-example folder under `topos/prompts/<slug>/`.** Rejected: collapses domain and example abstractions into one place; chair vs cabinet duplicate 80% of their part_geom prompt.
3. **Recipes only, no skills.** Rejected: recipes are narrow snippets injected as RAG context; skills are coherent capability packages with frontmatter + body + (optional) examples. Both are needed for different granularities.
4. **Skills as Python modules instead of markdown.** Rejected: skills are agent-facing knowledge, not framework-facing code. Markdown + frontmatter is the universal format (matches Claude Code, ForgeCAD, etc.).

## Consequences

- Adding a new articulated object (door, lid, mechanism) reuses every `articulated/` template. The example folder only needs `intent.md` and per-part `extras_*.md`.
- The system prompt is editable as a plain file — no rebuild, no code change.
- Framework prompts (fix-loop, judge) are inspectable and tunable by anyone, including non-developers reviewing the agent's behavior.
- `topos/skills/` is the natural home for accumulated lessons: the transform_apply trap, the cube_add half-extent gotcha, etc. Each agent task that touches geometry gets these lessons in scope.
- Skills v2 refactor (pending) will reduce token cost per task by ~5-10 KB (skills become on-demand rather than always-loaded) and restore agent autonomy.
- Plan.json shrunk from 16.6 KB → 4 KB.

## Empirical results

- Cabinet pipeline still passes (0.65-0.75) with the new three-layer prompts. No regression.
- Handle agent's `cube_add(size=1) + scale=full` bug fixed on first attempt after `topos_part_geometry` skill was injected (had been a recurring WARN before).
- 38 unit tests; new ones cover `goal_file` / `goal_template` resolution, `topos:` URI scheme, missing/conflicting goal sources, and skill discovery + injection.
