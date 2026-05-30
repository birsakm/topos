# In-context examples (design reference library)

This directory is a **plugin path** for real-world reference vocabulary used by the spec agent when writing `extras_md` per part. Each `.md` file here describes named industrial-design references for one category of object (furniture, engines, vehicles, appliances, ...). The spec agent reads ALL of them and uses the references as templates when writing `extras_md` for each part of the user's project.

The folder is called `in_context_examples/` because each file becomes part of the spec agent's in-context examples at prompt time. The Python loader symbol is still `_load_design_references` — the "design references" name describes the *concept* (worked references the spec agent imitates), not the on-disk location.

**Add a new category:** drop a new file. No code changes.

## File contract

- Filename: `<category>.md` (snake_case, no leading underscore — those are reserved for meta files like this README).
- Auto-discovered by `topos/agents/spec.py:_load_design_references()`.
- Auto-injected into `topos/prompts/system/spec_agent.md.j2` via the `design_references` Jinja loop.
- Loaded ALL at once today (Phase 1). Future Phase 2 will route per-archetype based on YAML frontmatter — leave room by giving each file a clear category name in its H1.

## File body convention

Lead with one line on the category. Then a **bad → good** anchor table or paired list. Each row teaches the spec agent how a vague extras_md ("a handle: rectangular bar") becomes a useful one ("an IKEA brushed-steel D-handle pull, 96-128mm wide, visible mounting screws at both stems, slight chamfer where cylindrical grip meets stem"). Keep each anchor under ~30 words — long enough to be specific, short enough to scan.

5-8 anchors per file is plenty. The point is to *teach the technique*, not be a complete catalog. The spec agent's pre-trained knowledge fills in the rest.

## Future: archetype routing (Phase 2)

When this library grows past ~5 files, we'll add YAML frontmatter to each:

```yaml
---
name: furniture
applies_to: [cabinet, drawer, chair, table, dresser, shelf, bookcase]
---
```

`_load_design_references()` will then filter based on user_prompt keywords before injecting, instead of dumping everything. Don't write the frontmatter today — the loader doesn't parse it yet, and the interface change is non-breaking when it lands.
