# Configuration

Topos config is layered. Each layer overrides the prior. Read with `topos config show` (with source annotation per key) and `topos config get <dotted.key>`.

## Layers (in increasing precedence)

1. **Built-in defaults** — `topos/config_defaults.yaml` shipped with the package
2. **User-global** — `~/.config/topos/config.yaml` (machine/account-specific things like Blender path and API keys)
3. **Repo-local** — `./topos.config.yaml` (gitignored; for per-project overrides)
4. **Env vars** — `TOPOS__<SECTION>__<KEY>=value` (double underscore separates nesting). One-off CI overrides

Use `topos config set <key> <value> [--scope user|repo]` to write. Use `topos config edit [--scope user|repo]` to open in `$EDITOR`.

## Key reference

### `backends`

```yaml
backends:
  default: claude          # which backend to use when an AgentTask doesn't specify
  claude:
    auth: subscription     # "subscription" (uses local `claude` login) or "api_key" (needs ANTHROPIC_API_KEY env var)
    cli: claude            # binary name or absolute path
    model: claude-opus-4-7[1m]  # passed as --model to the CLI. Set to null to use the CLI's own default.
    extra_args: []          # appended to every invocation (rarely needed — the backend already
                            # passes --output-format stream-json itself; adding another
                            # --output-format here would override that and break the watchdog)
    timeout_s: 600          # per-task wall-time cap
    permission_mode: bypassPermissions  # Claude Code permission mode
```

### `blender`

```yaml
blender:
  binary: /lab/yipeng/bin/blender   # absolute path; `topos doctor` auto-detects on first run
  hot_pool:
    enabled: false                     # if true, start a persistent Blender process pool
    max_procs: 2
    idle_kill_s: 600                   # kill an idle pool process after this many seconds
```

### `visual_critic`

```yaml
visual_critic:
  default: null              # null = honor each rubric's own judge_backend: field.
                             # Set to a backend name (claude_vision | gemini_vision |
                             # openai_vision | codex_cli | gemini_cli) to override
                             # every rubric — useful for swapping the whole pipeline
                             # to a cheaper model without editing per-rubric YAMLs.
  openai_vision:
    api_key: null            # falls back to OPENAI_API_KEY env var
    model: gpt-5
    timeout_s: 180
    max_retries: 3           # retries on 429 / 5xx
    retry_base_wait_s: 30.0
  gemini_vision:
    api_key: null            # falls back to GEMINI_API_KEY env var, then image_gen.gemini.api_key
    model: gemini-3-flash-preview
    timeout_s: 180
    max_retries: 3
    retry_base_wait_s: 30.0
    use_google_search: false # optional Google Search grounding (~$0.035 per grounded call)
```

The rubric YAML's own `judge_backend:` field selects which critic evaluates that rubric unless `visual_critic.default` overrides it. The field is named `judge_backend:` for back-compat with shipped rubrics; the implementation is in `topos/agents/visual_critic/`.

### `bpy_docs`

```yaml
bpy_docs:
  index_path: ~/.config/topos/bpy_docs.json   # built by `topos bpy-docs index`
```

The index is a flat JSON of the installed Blender's Python API (every `bpy.ops.*`, `bmesh.ops.*`, plus `mathutils`), built once per Blender version. Agents query it via `topos bpy-docs search "<query>"` from Bash. See `topos/bpy_docs/version_notes/` for curated cross-version diffs and Blender 6.0 deprecation notes.

### `orchestrator`

```yaml
orchestrator:
  max_parallel_tasks: 4        # ceiling on concurrent task execution
```

The fix-loop's iteration cap (`max_global_iters`) lives on each plan's `iter_policy` block in `plan.json`, not in shared config — different domains/examples want different ceilings.

## Env var examples

```bash
# Pin to a specific Blender on this machine without editing yaml:
TOPOS__BLENDER__BINARY=/opt/blender-5.0.1/blender topos doctor

# Switch backend mode for one run:
TOPOS__BACKENDS__CLAUDE__AUTH=api_key topos run mug

# Make API key available to the judge:
ANTHROPIC_API_KEY=sk-... topos run smoke
```
