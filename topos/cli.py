"""`topos` CLI entry point. Subcommands grow as the corresponding layers land."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import typer
import yaml

from . import config as cfg
from . import doctor as doctor_mod
from .workspace import Workspace

app = typer.Typer(
    name="topos",
    help="Multi-agent orchestrated, code-driven 3D content generation framework.",
    no_args_is_help=True,
    add_completion=False,
)

config_app = typer.Typer(name="config", help="Manage layered configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


# ---------- top-level commands ----------

_STATUS_TAG = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}


@app.command()
def doctor(
    suggest_writes: bool = typer.Option(
        True,
        "--suggest/--no-suggest",
        help="Print suggested `topos config set` commands for fixable warnings.",
    ),
):
    """Probe Python, claude CLI, Blender, API key, MCP, config files."""
    results = doctor_mod.run_all()
    worst = "ok"
    rank = {"ok": 0, "warn": 1, "fail": 2}
    for r in results:
        tag = _STATUS_TAG[r.status]
        typer.echo(f"{tag}  {r.name}: {r.summary}")
        if r.hint and suggest_writes:
            typer.echo(f"        ↳ {r.hint}")
        if rank[r.status] > rank[worst]:
            worst = r.status
    raise typer.Exit(code={"ok": 0, "warn": 0, "fail": 1}[worst])


# ---------- config subcommands ----------

@config_app.command("init")
def config_init(
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite if user config exists."),
):
    """Interactively create ~/.config/topos/config.yaml.

    Probes Blender, asks for auth mode, writes the file.
    """
    path = cfg.user_config_path()
    if path.is_file() and not overwrite:
        typer.echo(f"User config already exists at {path}. Re-run with --overwrite to replace.")
        raise typer.Exit(code=1)

    data: dict = {}

    # Blender
    found = doctor_mod.discover_blender()
    default_blender = str(found) if found else ""
    prompt_msg = f"Blender binary path [{default_blender}]" if default_blender else "Blender binary path"
    blender_path = typer.prompt(prompt_msg, default=default_blender, show_default=bool(default_blender))
    if blender_path:
        if not Path(blender_path).is_file():
            typer.echo(f"warning: {blender_path} does not exist; saving anyway.")
        data.setdefault("blender", {})["binary"] = blender_path

    # Auth mode
    auth = typer.prompt("Claude auth mode (subscription|api_key)", default="subscription")
    if auth not in ("subscription", "api_key"):
        typer.echo("auth must be 'subscription' or 'api_key'.")
        raise typer.Exit(code=2)
    data.setdefault("backends", {}).setdefault("claude", {})["auth"] = auth
    if auth == "api_key" and not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("note: ANTHROPIC_API_KEY is not set yet; set it before running pipelines.")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    typer.echo(f"Wrote {path}")


@config_app.command("get")
def config_get(dotted_key: str = typer.Argument(..., help="e.g., blender.binary")):
    """Print one config value with its source layer."""
    try:
        value, source = cfg.get_config_value(dotted_key)
    except KeyError:
        typer.echo(f"no such key: {dotted_key}")
        raise typer.Exit(code=1)
    typer.echo(f"{dotted_key} = {value!r}  (source: {source})")


@config_app.command("set")
def config_set(
    dotted_key: str = typer.Argument(...),
    value: str = typer.Argument(..., help="YAML-parsed; '1.5', 'true', strings all work"),
    scope: str = typer.Option("user", "--scope", help="'user' or 'repo'"),
):
    """Write a single value to the user-global or repo-local config file."""
    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError:
        parsed = value
    path = cfg.set_config_value(dotted_key, parsed, scope=scope)
    typer.echo(f"wrote {dotted_key} = {parsed!r} to {path}")


@config_app.command("show")
def config_show(
    with_sources: bool = typer.Option(
        True, "--sources/--no-sources", help="Annotate each leaf with its source layer."
    ),
):
    """Print the effective merged config."""
    effective, sources = cfg.effective_config_with_sources()
    if not with_sources:
        typer.echo(yaml.safe_dump(effective, sort_keys=False))
        return
    typer.echo(_render_with_sources(effective, sources))


def _render_with_sources(value, sources, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            sub_src = sources.get(k) if isinstance(sources, dict) else None
            if isinstance(v, dict):
                lines.append(f"{pad}{k}:")
                lines.append(_render_with_sources(v, sub_src or {}, indent + 1))
            else:
                tag = f"  # ({sub_src})" if isinstance(sub_src, str) else ""
                lines.append(f"{pad}{k}: {yaml.safe_dump(v).strip()}{tag}")
        return "\n".join(lines)
    return f"{pad}{yaml.safe_dump(value).strip()}"


@config_app.command("edit")
def config_edit(
    scope: str = typer.Option("user", "--scope", help="'user' or 'repo'"),
):
    """Open the config file in $EDITOR (creates an empty one if missing)."""
    if scope == "user":
        path = cfg.user_config_path()
    elif scope == "repo":
        path = Path.cwd() / "topos.config.yaml"
    else:
        typer.echo("scope must be 'user' or 'repo'")
        raise typer.Exit(code=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# Topos config override\n", encoding="utf-8")
    editor = os.environ.get("EDITOR") or shutil.which("nano") or shutil.which("vi")
    if not editor:
        typer.echo(f"no $EDITOR set and no nano/vi found; edit manually: {path}")
        raise typer.Exit(code=1)
    rc = subprocess.call([editor, str(path)])
    raise typer.Exit(code=rc)


# ---------- project lifecycle ----------

_SLUG_STOPWORDS = {
    "a", "an", "the", "of", "with", "and", "or", "for", "that", "this",
    "these", "those", "is", "are", "was", "were", "be", "to", "in", "on",
    "at", "by", "from", "as",
}


def _derive_slug(prompt: str) -> str:
    """Derive a workspace slug from the first few content words of the prompt.

    "a palace-style three-drawer cabinet" → "palace_style_three_drawer".
    """
    import re
    words = [w for w in re.findall(r"[a-z0-9]+", prompt.lower())
             if w not in _SLUG_STOPWORDS][:4]
    return "_".join(words) or "object"


def _copy_reference_images(images: "list[Path] | None", prompts_dir: Path) -> int:
    """Copy reference images into ``prompts/references/`` with an ``all_`` prefix
    so the part-agent reference-image auto-discovery picks them up for every
    part. Returns how many were copied."""
    if not images:
        return 0
    refs = prompts_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    ok_ext = {".png", ".jpg", ".jpeg", ".webp"}
    n = 0
    for img in images:
        if not img.is_file():
            typer.echo(f"WARN: reference image not found: {img}", err=True)
            continue
        if img.suffix.lower() not in ok_ext:
            typer.echo(f"WARN: unsupported image type {img.suffix!r} (use png/jpg/webp): {img}", err=True)
            continue
        shutil.copy(img, refs / f"all_{img.name}")
        n += 1
    return n


@app.command()
def make(
    prompt: str = typer.Argument(..., help="What to build, in natural language."),
    image: list[Path] = typer.Option(
        None, "--image", "-i",
        help="Reference image(s) guiding geometry / proportions / style. Copied "
             "into the workspace and shown to every part agent. Repeatable.",
    ),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: derived from the prompt)."),
    base: Path = typer.Option(None, "--base", help="Base dir for workspaces (default: ./outputs)."),
    no_run: bool = typer.Option(False, "--no-run", help="Create the workspace but don't auto-run."),
):
    """Build 3D content from a single prompt (+ optional reference images).

    The one entry point. Writes the prompt to prompts/intent.md, copies any
    reference images into prompts/references/, lays down the fixed articulated
    plan, and runs the orchestrator. The design agent derives the parts from the
    prompt (and any reference images) at runtime — there is no separate spec
    step. A static object is just an articulated one the design agent gives no
    joints.
    """
    from .backends.claude_cli import ClaudeCLIBackend
    from .orchestrator.plan_generator import generate_plan_articulated

    slug = slug or _derive_slug(prompt)
    ws = Workspace.create(slug, "articulated", base=base, exist_ok=False)
    try:
        prompts_dir = ws.root / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / "intent.md").write_text(prompt.strip() + "\n", encoding="utf-8")
        n_imgs = _copy_reference_images(image, prompts_dir)
        ws.plan_path.write_text(json.dumps(generate_plan_articulated(slug), indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(ws.root, ignore_errors=True)  # don't leave a half-written workspace
        raise

    typer.echo(f"=== workspace ready: {ws.root} ===")
    typer.echo(f"  → slug={slug}  domain=articulated")
    typer.echo("  → wrote prompts/intent.md + plan.json"
               + (f" + {n_imgs} reference image(s)" if n_imgs else ""))

    if no_run:
        typer.echo(f"\nSkipping run (--no-run). To execute: topos run {slug}" + (f" --base {base}" if base else ""))
        return

    typer.echo(f"\n=== running {slug} ===")
    from .orchestrator.plan_schema import load_plan
    from .orchestrator.runner import Runner
    plan_obj = load_plan(ws.plan_path)
    backends = {"claude": ClaudeCLIBackend.from_config()}
    runner = Runner(workspace=ws, plan=plan_obj, backends=backends,
                    event_sink=_maybe_event_sink())
    report = runner.run()
    _print_run_report(slug, report)
    raise typer.Exit(code=0 if report.success else 1)


def _maybe_event_sink():
    """Best-effort runner event sink. Returns None unless the supabase
    plugin successfully initializes (which itself requires SUPABASE_URL +
    SUPABASE_SERVICE_ROLE_KEY + TOPOS_RUN_ID env vars). A missing supabase
    package or any other import error is swallowed so plain `topos run`
    keeps working on hosts without the optional dependency."""
    try:
        from .plugins.supabase_event_sink import make_sink
    except Exception:  # noqa: BLE001 — never break the runner over a plugin
        return None
    try:
        return make_sink()
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"[topos] event sink init failed: {exc!r}", err=True)
        return None


def _print_run_report(slug: str, report) -> None:
    """Print the standard run report — same format as `topos run`."""
    typer.echo(f"\n=== run report ({slug}) ===")
    iters = report.iteration_count
    cost_str_top = (
        f"cost=${report.total_cost_usd_all_iters:.4f}"
        if iters > 1
        else f"cost=${report.total_cost_usd:.4f}"
    )
    iter_str = f"  iters={iters}" if iters > 1 else ""
    judge_str = ""
    if report.final_judge_passed is not None:
        judge_str = f"  judge={'PASS' if report.final_judge_passed else 'FAIL'}"
    typer.echo(
        f"overall: {'PASS' if report.success else 'FAIL'}  "
        f"duration={report.duration_s:.1f}s  {cost_str_top}{iter_str}{judge_str}"
    )
    if iters > 1:
        typer.echo("iteration history:")
        for h in report.history:
            jp = "PASS" if h.judge_passed else ("FAIL" if h.judge_passed is False else "—")
            sc = f"{h.judge_score:.2f}" if h.judge_score is not None else "—"
            typer.echo(
                f"  iter {h.iteration}: judge={jp} score={sc} "
                f"duration={h.duration_s:.1f}s cost=${h.cost_usd:.4f}"
            )
        typer.echo("final iter task breakdown:")
    for tid, r in report.results.items():
        flag = "ok" if r.success else "FAIL"
        note = f"  ({r.note})" if r.note else ""
        cost_str = f"  ${r.cost_usd:.4f}" if r.cost_usd > 0 else "  $0.0000"
        iter_tag = f" i{r.iteration}" if iters > 1 else ""
        typer.echo(
            f"  [{flag}] {tid:8s} {r.kind:6s} {r.duration_s:6.1f}s{cost_str}{iter_tag}{note}"
        )


@app.command()
def run(
    slug: str = typer.Argument(...),
    base: Path = typer.Option(None, "--base"),
    resume: bool = typer.Option(
        False, "--resume",
        help=(
            "Reuse already-successful task results from the workspace's previous "
            "run_report.json (skips re-execution of those tasks). Failed or "
            "missing tasks still run normally. Useful for picking up after a "
            "timeout or transient failure without re-paying for completed work."
        ),
    ),
):
    """Execute plan.json under the orchestrator."""
    from .orchestrator.plan_schema import load_plan
    from .orchestrator.runner import Runner

    ws = Workspace.locate(slug, base=base)
    if not ws.plan_path.is_file():
        typer.echo(
            f"no plan.json at {ws.plan_path}; seed one via "
            f"`topos init {slug} --from-example <name>` or `topos make \"<prompt>\" --slug {slug}`"
        )
        raise typer.Exit(code=1)

    plan_obj = load_plan(ws.plan_path)
    backends = _make_backends_for_plan(plan_obj)
    runner = Runner(workspace=ws, plan=plan_obj, backends=backends, resume=resume,
                    event_sink=_maybe_event_sink())
    report = runner.run()
    _print_run_report(slug, report)
    raise typer.Exit(code=0 if report.success else 1)


def _make_backends_for_plan(plan_obj):
    """Construct an AgentBackend for every distinct ``task.backend`` the plan
    actually uses. Lazy on purpose: a plan that only uses ``claude`` won't
    try to read ``GEMINI_API_KEY``, and a plan that asks for an unknown
    backend fails here with a clear error instead of mid-DAG with a
    cryptic ``no backend registered``.

    Dispatch parallels ``visual_critic.base.make_critic`` — the agent
    runner and the critic factory should fail in the same shape.
    """
    from .backends.claude_cli import ClaudeCLIBackend
    from .backends.codex_cli import CodexCLIBackend
    from .backends.gemini_cli import GeminiCLIBackend
    factories = {
        "claude": ClaudeCLIBackend.from_config,
        "codex":  CodexCLIBackend.from_config,
        "gemini": GeminiCLIBackend.from_config,
    }
    used = {
        getattr(t, "backend", None)
        for t in plan_obj.tasks
        if getattr(t, "backend", None) is not None
    }
    unknown = used - factories.keys()
    if unknown:
        raise typer.BadParameter(
            f"plan.json references unknown backend(s) {sorted(unknown)!r}; "
            f"known: {sorted(factories)!r}"
        )
    return {name: factories[name]() for name in used}


# ---------- cost inspection ----------

@app.command()
def cost(
    slug: str = typer.Argument(...),
    base: Path = typer.Option(None, "--base"),
    by_model: bool = typer.Option(False, "--by-model", help="Show per-model breakdown for tasks that report it."),
):
    """Show cost + token usage for the last run of a project."""
    ws = Workspace.locate(slug, base=base)
    report_path = ws.root / "run_report.json"
    if not report_path.is_file():
        typer.echo(f"no run_report.json at {report_path}; run `topos run {slug}` first")
        raise typer.Exit(code=1)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    cost = data.get("cost") or {}
    total_last = cost.get("total_usd_last_iter", 0.0)
    total_all = cost.get("total_usd_all_iters", total_last)
    by_kind = cost.get("by_kind_last_iter", {})
    iters = data.get("iteration_count", 1)
    judge = data.get("final_judge_passed")
    judge_str = f"  judge={'PASS' if judge else 'FAIL'}" if judge is not None else ""
    typer.echo(
        f"=== cost ({slug}) ===  "
        f"total=${total_all:.4f} (all iters)  "
        f"last_iter=${total_last:.4f}  "
        f"iters={iters}{judge_str}  "
        f"duration={data.get('duration_s', 0):.1f}s"
    )
    if by_kind:
        kind_str = "  ".join(f"{k}=${v:.4f}" for k, v in sorted(by_kind.items()))
        typer.echo(f"by kind (last iter): {kind_str}")
    history = data.get("history") or []
    if len(history) > 1:
        typer.echo("iteration history:")
        for h in history:
            jp = "PASS" if h.get("judge_passed") else ("FAIL" if h.get("judge_passed") is False else "—")
            sc = f"{h.get('judge_score'):.2f}" if h.get("judge_score") is not None else "—"
            typer.echo(
                f"  iter {h.get('iteration')}: judge={jp} score={sc} "
                f"duration={h.get('duration_s',0):.1f}s cost=${h.get('cost_usd',0):.4f}"
            )
    typer.echo("")
    typer.echo(f"{'task':<8} {'kind':<6} {'duration':>9} {'cost':>10}  tokens (in/out cache_read/cache_creation)")
    for tid, r in data.get("results", {}).items():
        u = r.get("usage") or {}
        i_tok = u.get("input_tokens", 0)
        o_tok = u.get("output_tokens", 0)
        cr_tok = u.get("cache_read_input_tokens", 0)
        cc_tok = u.get("cache_creation_input_tokens", 0)
        typer.echo(
            f"{tid:<8} {r.get('kind',''):<6} {r.get('duration_s',0):>7.1f}s "
            f"${r.get('cost_usd',0):>8.4f}  {i_tok}/{o_tok}  cache:{cr_tok} new-cache:{cc_tok}"
        )
        if by_model:
            mu = r.get("model_usage") or {}
            for model_id, mdata in mu.items():
                typer.echo(f"   ↳ {model_id}: {mdata}")


# ---------- skill management (ForgeCAD-style global install) ----------

skill_app = typer.Typer(name="skill", help="Manage Topos skills (capability bundles).", no_args_is_help=True)
app.add_typer(skill_app, name="skill")


_SKILL_TARGETS = {
    "claude":   Path.home() / ".claude" / "skills",
    "codex":    Path.home() / ".codex" / "skills",
    "opencode": Path.home() / ".config" / "opencode" / "skills",
}


def _skills_source_dir() -> Path:
    """The dir on disk where topos's skills are kept (in the editable install,
    this is the package's skills/ folder; in a wheel install, it's still
    package data on the filesystem)."""
    from importlib import resources
    ref = resources.files("topos").joinpath("skills")
    return Path(str(ref))


def _topos_skill_names() -> list[str]:
    src = _skills_source_dir()
    return sorted(
        p.name
        for p in src.iterdir()
        if p.is_dir() and (p / "SKILL.md").is_file()
    )


@skill_app.command("list")
def skill_list():
    """List the topos_* skills shipped with this Topos install."""
    src = _skills_source_dir()
    names = _topos_skill_names()
    typer.echo(f"=== skills shipped with topos (source: {src}) ===")
    for name in names:
        skill_md = (src / name / "SKILL.md").read_text(encoding="utf-8")
        # extract description from frontmatter
        import re
        m = re.search(r"^description:\s*(.+)$", skill_md, re.MULTILINE)
        desc = m.group(1).strip() if m else "(no description)"
        typer.echo(f"  {name}")
        typer.echo(f"    {desc}")


@skill_app.command("install")
def skill_install(
    target: str = typer.Option("claude", "--target",
                                help="Target runtime: claude | codex | opencode"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                  help="Show what would be installed, don't copy"),
    force: bool = typer.Option(False, "--force",
                                help="Overwrite existing skills at the target"),
):
    """Install topos_* skills into the target agent runtime's discovery dir
    so the agent auto-discovers them and can invoke via its native Skill tool.

    Targets:
      claude   → ~/.claude/skills/topos_<name>/
      codex    → ~/.codex/skills/topos_<name>/
      opencode → ~/.config/opencode/skills/topos_<name>/

    Complements the workspace-local mechanism (which always works without install).
    """
    if target not in _SKILL_TARGETS:
        typer.echo(f"unknown target {target!r}. Choices: {list(_SKILL_TARGETS)}")
        raise typer.Exit(code=2)
    target_dir = _SKILL_TARGETS[target]
    src = _skills_source_dir()
    names = _topos_skill_names()
    typer.echo(f"=== installing {len(names)} skill(s) into {target_dir} (target={target}) ===")
    for name in names:
        src_dir = src / name
        dst = target_dir / name
        action = "WOULD INSTALL" if dry_run else ("REPLACE" if dst.exists() else "INSTALL")
        if dst.exists() and not force and not dry_run:
            typer.echo(f"  [SKIP] {name} (already exists; --force to overwrite)")
            continue
        typer.echo(f"  [{action}] {name} → {dst}")
        if dry_run:
            continue
        if dst.exists() and force:
            shutil.rmtree(dst)
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dst,
                        ignore=shutil.ignore_patterns("__pycache__", "__init__.py"))
    if dry_run:
        typer.echo("(dry run; nothing copied)")
    else:
        typer.echo(f"done. Agents running with the {target} runtime now auto-discover these skills.")


@skill_app.command("uninstall")
def skill_uninstall(
    target: str = typer.Option("claude", "--target"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Remove all topos_* skills from the target runtime's skills dir."""
    if target not in _SKILL_TARGETS:
        typer.echo(f"unknown target {target!r}.")
        raise typer.Exit(code=2)
    target_dir = _SKILL_TARGETS[target]
    if not target_dir.is_dir():
        typer.echo(f"{target_dir} doesn't exist; nothing to uninstall.")
        return
    found = sorted(p for p in target_dir.iterdir() if p.is_dir() and p.name.startswith("topos_"))
    typer.echo(f"=== uninstalling {len(found)} topos_* skill(s) from {target_dir} ===")
    for p in found:
        typer.echo(f"  [{'WOULD REMOVE' if dry_run else 'REMOVE'}] {p.name}")
        if not dry_run:
            shutil.rmtree(p)
    if dry_run:
        typer.echo("(dry run; nothing removed)")


# ---------- bpy docs RAG ----------

bpy_docs_app = typer.Typer(name="bpy-docs", help="Manage the local Blender API docs index for the bpy_docs_search tool.", no_args_is_help=True)
app.add_typer(bpy_docs_app, name="bpy-docs")


@bpy_docs_app.command("index")
def bpy_docs_index(
    blender: str = typer.Option(None, "--blender",
                                 help="Path to blender binary (defaults to config.blender.binary)"),
    output: Path = typer.Option(None, "--output",
                                 help="Where to write the JSON index (defaults to config.bpy_docs.index_path)"),
    include_bpy_types: bool = typer.Option(False, "--include-bpy-types",
                                            help="Also walk bpy.types (huge; off by default)"),
    timeout_s: int = typer.Option(180, "--timeout-s"),
):
    """Run Blender once to introspect its Python API and write a search index.

    The index is pinned to the Blender version that built it. Re-run after
    upgrading Blender."""
    from .tools._blender_subprocess import resolve_blender_binary
    from .process import run_process
    from .bpy_docs import index_path as default_index_path

    binary = blender or resolve_blender_binary()
    out_path = output or default_index_path()
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    introspect_script = Path(__file__).parent / "bpy_docs" / "introspect.py"
    cmd = [
        binary, "--background", "--python", str(introspect_script), "--",
        "--output", str(out_path),
    ]
    if include_bpy_types:
        cmd.append("--include-bpy-types")

    typer.echo(f"=== indexing bpy docs ===  binary={binary}  out={out_path}")
    result = run_process(cmd, timeout_s=timeout_s)
    if result.returncode != 0:
        typer.echo(f"FAILED (exit {result.returncode})")
        typer.echo(result.stderr[-2000:])
        raise typer.Exit(code=1)

    # Surface the last few summary lines from the introspect script
    for line in result.stdout.strip().splitlines()[-6:]:
        typer.echo(f"  {line}")
    typer.echo("done.")


@bpy_docs_app.command("search")
def bpy_docs_search_cmd(
    query: str = typer.Argument(...),
    top_k: int = typer.Option(5, "--top-k"),
    kind: str = typer.Option(None, "--kind",
                              help="Restrict to one kind: op | bmesh_op | class | method | function"),
):
    """Query the index and print matches. Useful for sanity-checking the index."""
    from .bpy_docs import search
    kinds = [kind] if kind else None
    try:
        matches = search(query, top_k=top_k, kinds=kinds)
    except FileNotFoundError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1)
    if not matches:
        typer.echo(f"(no matches for {query!r})")
        return
    for m in matches:
        typer.echo(f"\n[{m['kind']:9s} score={m['score']:5.1f}]  {m['symbol']}")
        if m.get("signature"):
            typer.echo(f"  sig: {m['signature']}")
        if m.get("short_doc"):
            typer.echo(f"  doc: {m['short_doc'][:200]}")


@app.command("generate-texture")
def generate_texture_cmd(
    prompt: str = typer.Argument(..., help="What texture to generate, e.g. 'seamless tileable walnut wood plank, photoreal, 4k'"),
    output: Path = typer.Option(..., "--output", "-o",
                                 help="Output path for the PNG (absolute or relative to cwd)."),
    condition: Path | None = typer.Option(None, "--condition", "-c",
                                           help="Optional sketch/silhouette image to condition the generation on."),
    size: int = typer.Option(1024, "--size", "-s", min=128, max=2048,
                              help="Target square resolution in pixels."),
    backend: str | None = typer.Option(None, "--backend",
                                        help="Override image-gen backend (default: config.image_gen.default = gemini)."),
    timeout_s: int = typer.Option(180, "--timeout-s"),
):
    """Generate a texture image via an ImageGenBackend (one-off, debug-style).

    This CLI does NOT go through the ``generate_texture_image`` tool — that
    tool is now design.json-aware and meant for orchestrator dispatch. The
    CLI is for human / scripted one-offs where you already have a prompt in
    mind and just want a PNG out.
    """
    from .agents.image_gen.base import make_backend
    impl = make_backend(backend)
    condition_bytes: bytes | None = None
    if condition is not None:
        if not condition.is_file():
            typer.echo(f"FAIL  condition image not found: {condition}", err=True)
            raise typer.Exit(code=1)
        condition_bytes = condition.read_bytes()
    result = impl.generate(prompt, condition_image=condition_bytes, size=size,
                           timeout_s=timeout_s)
    if not result.success:
        typer.echo(f"FAIL  {result.error or 'unknown error'}", err=True)
        raise typer.Exit(code=1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result.png_bytes)
    typer.echo(
        f"OK  wrote {output} ({len(result.png_bytes)} bytes, "
        f"{result.duration_s:.1f}s, model={result.model}, "
        f"cost=${result.cost_usd:.4f})"
    )


# ---------- trajectory analysis ----------

@app.command()
def analyze(
    slug: str = typer.Argument(..., help="Project slug to analyze"),
    base: Path = typer.Option(None, "--base", help="Base dir for workspaces (default: ./outputs)"),
    save: bool = typer.Option(False, "--save", help="Also save report to outputs/<slug>/analysis.md"),
    backend: str = typer.Option(
        None, "--backend",
        help="Analysis LLM provider: gemini | openai | anthropic (default: analysis.backend config, else gemini).",
    ),
    model: str = typer.Option(
        None, "--model",
        help="Model override for synthesis (default: the chosen backend's default model).",
    ),
    extract_only: bool = typer.Option(
        False, "--extract-only",
        help="Only print extracted metrics (no LLM call).",
    ),
):
    """Analyze agent trajectories from a completed run.

    Extracts structured metrics from trajectory files and (unless --extract-only)
    calls a configurable text-LLM provider (gemini / openai / anthropic) to
    synthesize an actionable analysis report.
    """
    from .analysis.extract import extract_run_metrics
    from .workspace import Workspace

    ws = Workspace.locate(slug, base=base)
    report_path = ws.root / "run_report.json"
    if not report_path.is_file():
        typer.echo(f"no run_report.json at {report_path}; run `topos run {slug}` first")
        raise typer.Exit(code=1)

    if extract_only:
        import dataclasses
        analysis = extract_run_metrics(ws.root)
        # Print as JSON
        def _ser(obj):
            if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                return dataclasses.asdict(obj)
            return str(obj)
        typer.echo(json.dumps(dataclasses.asdict(analysis), indent=2, default=_ser))
        return

    from .analysis.synthesize import analyze_run

    typer.echo(f"=== analyzing {slug} ===")
    try:
        report_md = analyze_run(ws.root, backend=backend, model=model)
    except (RuntimeError, ValueError) as e:
        typer.echo(f"FAIL  {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(report_md)

    if save:
        out_path = ws.root / "analysis.md"
        out_path.write_text(report_md, encoding="utf-8")
        typer.echo(f"\nsaved to {out_path}")


if __name__ == "__main__":  # pragma: no cover
    app()
