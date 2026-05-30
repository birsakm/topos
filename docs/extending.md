# Extending Topos

Every layer of the framework is designed to be extended by adding files in the right directory — no core edits required. This doc lists each extension point with the file you create and what plumbing it hooks into.

## Add a new coding-agent backend (Codex, Gemini, custom)

Implement the `AgentBackend` Protocol in `topos/backends/<name>.py`:

```python
from .base import AgentBackend, AgentRunResult, McpServerConfig

class MyBackend:
    name = "my_backend"
    auth_mode = "subscription"  # or "api_key"

    def run(self, *, prompt, workspace, allowed_tools, mcp_servers,
            timeout_s=None, env=None, system_prompt_append=None,
            trajectory_dir=None) -> AgentRunResult:
        # spawn your CLI / call your SDK / etc.
        # return AgentRunResult(success=..., files_modified=..., cost_usd=..., usage={...}, ...)
```

Reference in `plan.json` via `"backend": "my_backend"` per agent task. Add to the backends dict in `topos/cli.py:run` or use `ClaudeCLIBackend.from_config()` analog. The `CodexCLIBackend` / `GeminiCLIBackend` placeholders show the expected shape.

## Add a new critic backend

Implement the `Critic` Protocol in `topos/agents/visual_critic/<name>.py`:

```python
from .base import Critic, CriticInputs, CriticResult, Rubric

class GeometryCritic:
    def evaluate(self, inputs: CriticInputs, rubric: Rubric) -> CriticResult:
        # inspect inputs.images / inputs.metadata
        # return CriticResult(passed=..., overall_score=..., per_criterion=..., suggested_fixes=...)
```

Wire it up in `topos/agents/visual_critic/base.py:make_critic` factory (one elif branch). Reference from a rubric's `judge_backend: geometry` field (the YAML field name is kept for plan.json / rubric stability).

For composing multiple critics (e.g. VLM + geometry checks), no built-in pattern is shipped yet — combine in your own factory branch.

## Add a new rubric

Drop a YAML file at `topos/rubrics/<id>.yaml`:

```yaml
id: my_rubric_v1
judge_backend: claude_vision      # which Judge implementation evaluates this
pass_threshold: 0.7
criteria:
  - id: <criterion_id>
    weight: 0.3
    prompt: >
      What should the judge look for? Plain natural language.
  ...
```

Reference from a `judge` ToolTask in plan.json: `args: { rubric: "my_rubric_v1" }`. The framework resolves it from `topos/rubrics/`. Sum of weights doesn't need to be 1.0 — the judge normalizes.

## Add a new skill (capability bundle)

Create `topos/skills/topos_<name>/SKILL.md` with YAML frontmatter:

```markdown
---
name: topos_<name>
description: One-line action + when to use. The agent reads this to DECIDE to invoke.
when_to_use: Any AgentTask that <does what>
provides:
  - capability_1
  - capability_2
related_tools: [tool_name]
related_skills: [other_skill]
---

# Topos: <Capability Name>

(full content the agent reads after invoking the skill)
```

Naming: prefix `topos_` so future user-installed skills from other sources don't collide. Reference from `plan.json` agent task: `"skills": ["topos_<name>"]`.

**Current behavior (v1, pending refactor to v2):** the runner reads each declared skill's SKILL.md and concatenates it into the prompt at task time. This forces the skill on the agent.

**Coming in v2:** `topos skill install [--target claude|codex|opencode]` copies skill folders to the agent runtime's discovery directory (`~/.claude/skills/<name>/`). Agent runtime auto-discovers; agent autonomously invokes via the Skill tool. The `skills: [...]` field in plan.json becomes a soft hint section. See `memory/feedback_skills_should_be_agent_invoked.md` for the design rationale.

## Add a new domain template

Reusable prompt templates for the "kind of object" go in `topos/prompts/<domain>/`. Files ending in `.md.j2` are Jinja2 templates rendered at plan-load time with the task's `goal_params`. Files ending in plain `.md` are static and referenced via `goal_file`.

For an articulated-object pipeline, the templates are:

- `<domain>/designer.md.j2` — how to write `design.json` (params: `intent` text)
- `<domain>/part_geom.md.j2` — how to write a single part (params: `part_name`, `lower_name`, `extras`)
- `<domain>/builder.md` — how to write `build.py` (generic, no params)
- `<domain>/joints_writer.md` — how to write `joints.yaml` (generic, no params)

A new domain — say, `mechanism` for screws-and-gears — would replicate this layout, adapting the templates to the domain's vocabulary.

## Add a new example

Drop a folder at `examples/<slug>/`:

```
examples/<slug>/
├── spec.yaml                  # human-readable description of intent
├── plan.json                  # the DAG that produces the project
└── prompts/                   # example-specific extras
    ├── intent.md              # passed to the designer template as {{ intent }}
    ├── extras_<part>.md       # passed to the part_geom template as {{ extras }}
    └── ...
```

`plan.json` references the domain templates and your example's prompts:

```jsonc
{
  "tasks": [
    {
      "id": "01_agent_design", "kind": "agent",
      "goal_template": "topos:articulated/designer.md.j2",
      "goal_params": { "intent_file": "./prompts/intent.md" }
    },
    // ... more tasks
  ]
}
```

The `topos init <slug> --from-example <name>` command copies the whole folder into a fresh workspace at `outputs/<slug>/`.

## Add a new tool

Decorator-register in `topos/tools/<name>.py`:

```python
from .registry import tool

@tool(
    "my_tool",
    description="One-line action description",
    input_schema={
      "type": "object",
      "properties": {
        "workspace": {"type": "string"},
        # ... other args
      },
      "required": ["workspace"],
    },
    output_schema={"type": "object", "properties": {"success": {"type": "boolean"}}},
)
def my_tool(*, workspace: str, ...) -> dict:
    # do the thing
    return {"success": True, ...}
```

Add an import line in `topos/tools/registry.py:_ensure_default_tools_imported()` so the decorator fires at load time. Reference from a ToolTask in plan.json: `"tool": "my_tool"`, `"args": {...}`. The framework auto-injects `workspace` if your tool declares it.

## Add a new Blender-side wrapper

`render_wrapper.py` and `export_wrapper.py` are the patterns for any task that needs to run inside Blender. Rules:

- The file lives at `topos/tools/<group>/wrapper.py`. It must NOT `import topos.*` — it runs in Blender's bundled Python which doesn't see the host venv.
- Parse args from `sys.argv` after the `--` separator.
- Insert `Path(script).parent.resolve()` into `sys.path` before any `runpy.run_path(script)` so multi-file projects work.
- Bake all object transforms (`bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)`) before exporting to any flat format like OBJ/GLB. The wrapper for per-part export uses duplicate-and-apply to avoid mutating the original.

## Add a new system prompt

The framework-level prompts (system, fix-loop, judge) live in `topos/prompts/system/`. They're loaded by Python code via `topos.prompts.load_text(rel_path)` or `topos.prompts.render(rel_path, **params)`. To change the system prompt that every agent task sees, edit `topos/prompts/system/coding_agent_base.md` directly — no code change needed.

## Add a new ADR

`docs/decisions/NNNN-<slug>.md`. Number monotonically. Each ADR is its own file: title, status (Proposed/Accepted/Superseded), context, decision, alternatives considered, consequences. If you supersede a prior ADR, mark the old one with `Status: Superseded by NNNN`.

## Add a new lesson

`docs/lessons.md` is append-only. Date the entry, write what was tried and what was learned. Link to the relevant commit or file path. Lessons are the working memory across implementation sessions — when something surprises you, write it down.
