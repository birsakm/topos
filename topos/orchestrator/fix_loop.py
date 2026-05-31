"""Fix-loop strategy: read the judges' verdicts, build targeted fix tasks.

When ``Runner.run()`` completes an iteration with one or more failing
judges, it asks this module for a list of fix-shaped ``AgentTask`` to
queue for the next iteration. Each free function here takes the current
``results`` dict and returns the next decision — no ``Runner`` self
state, so the strategy can grow (multi-judge synthesis, model-upgrade
on failure, incremental retry, etc.) without entangling the orchestrator
core in ``runner.py``.

Today's strategy is simple: one fix task per failing judge, with the
scope inferred from the judge's task id (per-part judges trigger
per-part fixes; the assembly judge triggers an assembly-wide fix).
"""

from __future__ import annotations

import re

from .results import TaskResult
from .tasks import AgentTask, Task


# Map judge task id → corresponding fix scope.
#   XX_tool_judge_part_<name>  → fix src/parts/<name>.py only
#   XX_tool_judge   (assembly) → fix the whole assembly (build.py + anything)
# Optional leading ``<subgraph_id>__`` is accepted so namespaced dynamic
# children from runtime SubgraphTask expansion (ADR-0008) also match.
# Pre-refactor flat ids (e.g. ``06_tool_judge_part_frame``) still match;
# namespaced ids like ``02_subgraph_parts__06_tool_judge_part_frame`` do too.
_PART_JUDGE_RE = re.compile(r"^(?:.+__)?\d+_tool_judge_part_(?P<name>\w+)$")
_PART_FIX_RE   = re.compile(r"^(?:.+__)?99_agent_fix_part_(?P<name>\w+)$")

# Conventional part-task id shape emitted by plan_generator:
#   ``<NN>_agent_part_<lower>``  (NN is two digits; lower is the part's lower_name)
_PART_AGENT_RE = re.compile(r"^(?:.+__)?\d+_agent_part_(?P<name>\w+)$")

# Fallback skill set when the originating part task can't be found (e.g.
# hand-authored plan.json that doesn't follow plan_generator's id scheme).
# Kept narrow on purpose — we want the agent's prompt to load only what's
# likely relevant. Hardware skill is excluded since most parts aren't
# hardware; per-part inheritance is the right answer.
_FALLBACK_PART_FIX_SKILLS = ("topos_part_geometry", "topos_bpy_docs")


def _find_part_agent_task(
    original_tasks: list[Task] | None, part_lower_name: str,
) -> AgentTask | None:
    """Locate the originating part AgentTask by its ``<NN>_agent_part_<lower>``
    id. Returns None if no match (caller falls back to a default skill set)."""
    if not original_tasks:
        return None
    for t in original_tasks:
        if not isinstance(t, AgentTask):
            continue
        m = _PART_AGENT_RE.match(t.id)
        if m and m.group("name") == part_lower_name:
            return t
    return None


def _default_fix_backend(original_tasks: list[Task] | None) -> str:
    """Pick a sensible backend for a fix task when the originating agent
    can't be located. Plans are typically homogeneous in backend, so any
    AgentTask in the plan is a reasonable representative. Falls back to
    ``"claude"`` only when the plan has zero agent tasks (degenerate case).

    Why this exists: hardcoding ``backend="claude"`` for fix tasks silently
    broke gemini/codex-backed plans — the fix task would queue with a
    backend the runner hadn't registered, surfacing only as a buried
    "no backend registered" TaskResult mid-DAG.
    """
    if original_tasks:
        for t in original_tasks:
            if isinstance(t, AgentTask):
                return t.backend
    return "claude"


def _find_build_agent_task(original_tasks: list[Task] | None) -> AgentTask | None:
    """Locate the assembly builder by its ``<NN>_agent_build`` id. Used to
    reuse its ID when the assembly judge fails — the assembly fix REPLACES
    the build node so downstream render/export tasks automatically wait
    for the fix to land."""
    if not original_tasks:
        return None
    for t in original_tasks:
        if isinstance(t, AgentTask) and t.id.endswith("_agent_build"):
            return t
    return None


def judge_scores_snapshot(results: dict[str, TaskResult]) -> dict[str, float]:
    """Map judge_id → overall_score for every judge in ``results`` right now.
    Used by saturation detection to compare scores across iterations.
    """
    return {
        tid: float(r.output.get("overall_score") or 0.0)
        for tid, r in results.items()
        if r.kind == "tool" and isinstance(r.output, dict) and "overall_score" in r.output
    }


def iter_improved(prev: dict[str, float], cur: dict[str, float], min_delta: float) -> bool:
    """True if the iteration made real PROGRESS, i.e. it's worth continuing.

    Progress is either:
      - a judge sticky-passed (was present last iter, now absent → that part is
        locked in as good), or
      - some still-evaluated judge improved UPWARD by >= ``min_delta``.

    Pure wiggle, judge sampling noise, or regressions do NOT count as progress —
    counting "any movement" (the old ``abs`` rule) meant a 9-part object almost
    never early-stopped, because with ~10 judges one always jitters, so the fix
    loop ground to ``max_global_iters`` even when nothing was actually getting
    better (observed: bicycle build agent looping 3 iters on the same gap).
    """
    if set(prev) - set(cur):
        # A previously-evaluated judge disappeared = sticky-pass froze a part
        # that reached a passing score → genuine progress.
        return True
    shared = set(prev) & set(cur)
    if not shared:
        # No basis for comparison — assume improvement is still possible.
        return True
    max_gain = max(cur[k] - prev[k] for k in shared)   # positive improvement only
    return max_gain >= min_delta


def frozen_parts(results: dict[str, TaskResult]) -> set[str]:
    """Per-part names whose judge has reported passed=True in some prior
    iter — these parts are "locked in" by sticky-pass policy.

    Why: judge sampling noise can flip a 0.7+ score → 0.36 on identical
    input (observed in cab_a4_perpart Handle iter2 — judge hallucinated
    "no handle visible"). With "all judges must pass" gating, a single
    noisy false-fail would kill the whole run. Sticky-pass treats a
    passed part as locked: skip re-judge, skip refix.

    Only per-part judges freeze. The assembly judge always re-runs since
    its input (full scene) changes whenever any fix happens.
    """
    out: set[str] = set()
    for tid, r in results.items():
        m = _PART_JUDGE_RE.match(tid)
        if not m:
            continue
        if r.kind == "tool" and isinstance(r.output, dict) and r.output.get("passed") is True:
            out.add(m.group("name"))
    return out


def all_judge_results(results: dict[str, TaskResult]) -> list[TaskResult]:
    """Every tool task in ``results`` whose output has the ``passed`` key
    set to a BOOLEAN — i.e. every judge. Returned in insertion order so
    "first judge to fail" is deterministic.

    The bool type check is load-bearing: ``verify_parts`` outputs
    ``passed_parts: list[str]`` (the names that verified OK), and a
    historical typo had it under ``passed: list[str]``. A non-strict
    ``"passed" in output`` check would misclassify the verify tool as
    a judge, and ``latest_judge_passed`` would short-circuit the
    fix-loop because a non-empty list is truthy. Require bool to keep
    judges and tools distinguishable by their output shape alone."""
    return [
        r for r in results.values()
        if r.kind == "tool"
        and isinstance(r.output, dict)
        and isinstance(r.output.get("passed"), bool)
    ]


def latest_judge_passed(results: dict[str, TaskResult]) -> bool | None:
    """All judges passed?

    Returns:
        - ``None`` if no judges in results
        - ``False`` if any judge reports passed != True
        - ``True`` only when every judge reports passed=True
    """
    all_judges = all_judge_results(results)
    if not all_judges:
        return None
    if all(bool(j.output.get("passed")) for j in all_judges):
        return True
    return False


def collect_runtime_failures(results: dict[str, TaskResult]) -> list[dict]:
    """Scan all tool task results for runtime ``failed_parts`` records.

    A tool can surface per-part runtime failures by setting an output dict
    with a ``failed_parts: [{name, lower_name, stage, error_class,
    error_msg, traceback}, ...]`` field. The buildability gate
    (``verify_parts``) is the primary producer today; future tools can
    follow the same convention.

    Returns a flat list of all failure records across all tools. Empty
    when nothing has surfaced runtime errors.
    """
    out: list[dict] = []
    for r in results.values():
        if r.kind != "tool" or not isinstance(r.output, dict):
            continue
        failed = r.output.get("failed_parts")
        if not failed:
            continue
        for fp in failed:
            if isinstance(fp, dict) and fp.get("name"):
                out.append(fp)
    return out


def stop_condition_met(results: dict[str, TaskResult], stop_on: str) -> bool:
    """Has the configured iteration stop condition been satisfied?"""
    if stop_on == "never":
        return False
    if stop_on == "first_failure":
        return any(not r.success for r in results.values())
    if stop_on == "judge_pass":
        return latest_judge_passed(results) is True
    return False


def build_fix_tasks(
    results: dict[str, TaskResult],
    next_iter: int,
    *,
    original_tasks: list[Task] | None = None,
) -> list[AgentTask]:
    """For every judge that failed in this iter, build one targeted fix task.

    Returns an empty list when all judges pass — that signals the
    iteration loop to terminate without queuing more work.

    Today's mapping is one fix task per failing judge:
      - per-part judge → ``99_agent_fix_part_<name>`` (re-writes that part only)
      - assembly judge → ``99_agent_fix``           (re-writes build.py + anything)

    ``original_tasks`` (optional but recommended): the full plan's task list.
    When provided, per-part fix tasks inherit the originating part task's
    ``skills`` — keeps hardware parts loading the hardware skill while
    structural parts skip it (and the reverse). Without it, a narrow
    ``_FALLBACK_PART_FIX_SKILLS`` is used.
    """
    # Local import to avoid a hard prompt-package dep at module-load time
    # (and to keep this module testable without standing up the prompt env).
    from ..prompts import render as render_prompt

    fix_tasks: list[AgentTask] = []
    for judge in all_judge_results(results):
        if judge.output.get("passed") is True:
            continue
        out = judge.output
        m = _PART_JUDGE_RE.match(judge.id)
        if m:
            # Per-part fix — re-write src/parts/<name>.py only.
            part_name = m.group("name")
            origin = _find_part_agent_task(original_tasks, part_name)
            inherited_skills = (
                list(origin.skills)
                if origin is not None and origin.skills
                else list(_FALLBACK_PART_FIX_SKILLS)
            )
            goal = render_prompt(
                "system/fix_part.md.j2",
                iteration=next_iter,
                part_name=part_name,
                overall_score=float(out.get("overall_score") or 0.0),
                per_criterion=out.get("per_criterion") or {},
                suggested_fixes=out.get("suggested_fixes") or [],
            )
            # Re-use the ORIGINAL part agent's ID + deps. This is what makes
            # the downstream DAG (verify_parts → render → build → export →
            # judge) automatically wait for the fixed version: they already
            # dep on `02_agent_part_torso`; the fix REPLACES that node's
            # latest result. `is_fix_rerun=True` tells the runner to skip
            # the carry-forward shortcut (otherwise iter1 would see iter0's
            # success and never execute the fix).
            origin_id = origin.id if origin is not None else f"99_agent_fix_part_{part_name}"
            origin_deps = list(origin.deps) if origin is not None else []
            origin_tools = (
                list(origin.allowed_tools)
                if origin is not None and origin.allowed_tools
                else ["Read", "Edit", "Write", "Glob", "Bash"]
            )
            fix_tasks.append(AgentTask(
                id=origin_id,
                goal=goal,
                backend=origin.backend if origin is not None else _default_fix_backend(original_tasks),
                skills=inherited_skills,
                allowed_tools=origin_tools,
                deps=origin_deps,
                # 360s was tight on complex parts (articulated hands, 4-PBR-
                # material shins, smokestacks with bracket geometry) — observed
                # ~30% timeout rate on the optimus_prime_v2 run, May 2026.
                # 600s matches the bumped spec-agent timeout and gives the
                # fix agent breathing room for an Edit-only multi-pass refine.
                timeout_s=600,
                is_fix_rerun=True,
            ))
        else:
            # Assembly fix — reuse the build agent's ID so downstream
            # render/export tools that already dep on `46_agent_build`
            # automatically wait for the assembly fix to land. (If no
            # build task exists, fall back to the legacy "99_agent_fix"
            # ID so the fix still runs — it just won't be on the critical
            # path until a real refactor.)
            build_origin = _find_build_agent_task(original_tasks)
            goal = render_prompt(
                "system/fix_loop.md.j2",
                iteration=next_iter,
                overall_score=float(out.get("overall_score") or 0.0),
                per_criterion=out.get("per_criterion") or {},
                suggested_fixes=out.get("suggested_fixes") or [],
            )
            if build_origin is not None:
                fix_tasks.append(AgentTask(
                    id=build_origin.id,
                    goal=goal,
                    backend=build_origin.backend,
                    skills=list(build_origin.skills),
                    allowed_tools=list(build_origin.allowed_tools)
                                  or ["Read", "Edit", "Write", "Glob", "Bash"],
                    deps=list(build_origin.deps),
                    timeout_s=300,
                    is_fix_rerun=True,
                ))
            else:
                fix_tasks.append(AgentTask(
                    id="99_agent_fix",
                    goal=goal,
                    backend=_default_fix_backend(original_tasks),
                    allowed_tools=["Read", "Edit", "Glob"],
                    deps=[],
                    timeout_s=300,
                    is_fix_rerun=True,
                ))
    return fix_tasks


def build_runtime_fix_tasks(
    results: dict[str, TaskResult],
    next_iter: int,
    *,
    original_tasks: list[Task] | None = None,
) -> list[AgentTask]:
    """For every part that surfaced a runtime build/import failure (e.g. via
    ``verify_parts``), build one targeted runtime fix task. The fix agent
    sees the traceback and edits *just* the broken part file.

    Returns an empty list when no runtime failures exist (the framework
    treats that as "nothing for THIS strategy to do" — the judge-driven
    ``build_fix_tasks`` still gets its turn).

    Why this is separate from ``build_fix_tasks``: judge feedback is a
    visual quality signal ("looks like a featureless cube"); a runtime
    traceback is a hard correctness signal ("AttributeError on line 145").
    They want different prompts and different urgency — a runtime failure
    means render couldn't even produce an image to judge.
    """
    from ..prompts import render as render_prompt

    fix_tasks: list[AgentTask] = []
    # De-dup by part name in case multiple tools reported the same failure.
    seen: set[str] = set()
    for fp in collect_runtime_failures(results):
        name = fp.get("name") or ""
        lower = fp.get("lower_name") or ""
        if not name or not lower or name in seen:
            continue
        seen.add(name)

        # Inherit skills + tool whitelist from the original part agent so the
        # runtime fixer has the SAME context the agent had when first writing
        # the file (e.g. topos_bpy_docs is the right skill here — fixer
        # should grep the local Blender API index for the offending symbol).
        orig = _find_part_agent_task(original_tasks, lower)
        if orig is not None:
            skills = list(orig.skills)
            allowed_tools = list(orig.allowed_tools)
            # Match the build-fix-task floor (see comment in build_fix_tasks).
            timeout_s = max(orig.timeout_s, 600)
        else:
            skills = list(_FALLBACK_PART_FIX_SKILLS)
            allowed_tools = ["Read", "Edit", "Write", "Glob", "Bash"]
            timeout_s = 600

        goal = render_prompt(
            "system/fix_part_runtime.md.j2",
            iteration=next_iter,
            part_name=name,
            lower_name=lower,
            stage=fp.get("stage", "unknown"),
            error_class=fp.get("error_class", "Exception"),
            error_msg=fp.get("error_msg", ""),
            # Cap traceback so the prompt doesn't blow out — head + tail
            # are the useful parts; middle of a deep stack is noise.
            traceback=(fp.get("traceback") or "")[:4000],
        )

        # Reuse the origin part agent's ID so downstream tasks that already
        # dep on it automatically wait for the runtime fix to land. Same
        # rationale as judge-driven fixes (see build_fix_tasks). Without
        # this the runtime fix runs in parallel with verify/render/build,
        # and those run against stale (still-broken) code.
        if orig is not None:
            fix_id = orig.id
            fix_deps = list(orig.deps)
        else:
            fix_id = f"99_agent_fix_part_{lower}_runtime"
            fix_deps = []

        fix_tasks.append(AgentTask(
            id=fix_id,
            goal=goal,
            backend=orig.backend if orig is not None else _default_fix_backend(original_tasks),
            skills=skills,
            allowed_tools=allowed_tools,
            deps=fix_deps,
            timeout_s=timeout_s,
            is_fix_rerun=True,
        ))
    return fix_tasks
