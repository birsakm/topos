"""Plan JSON schema: pydantic validation + topo-sort entry point.

Plans validate as pydantic models on load; the runner consumes a list of
``Task`` dataclasses (``AgentTask`` | ``ToolTask``) produced by ``Plan.tasks``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .tasks import AgentTask, SubgraphTask, Task, ToolTask


class IterPolicy(BaseModel):
    max_global_iters: int = 1
    stop_on: Literal["judge_pass", "first_failure", "never"] = "judge_pass"
    # Cost-saturation early-stop: if NO judge moves by >= this delta between
    # consecutive iters, abort the fix loop even before max_global_iters.
    # max_global_iters remains the hard ceiling regardless.
    # Set to 0 to disable. Default 0.05 = "judge score bumped at least 5pp
    # somewhere this iter, otherwise we're spinning on sampling noise".
    min_improvement: float = 0.05


class _AgentTaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["agent"]
    # ONE of {goal, goal_file, goal_template} must be set. Mutually exclusive.
    goal: str | None = None
    goal_file: str | None = None
    goal_template: str | None = None
    goal_params: dict = {}              # jinja2 params for goal_template. Keys ending '_file' are auto-resolved as nested file refs.
    backend: str = "claude"
    deps: list[str] = []
    allowed_tools: list[str] = []
    skills: list[str] = []               # SKILL.md bundles to inject into the agent's prompt
    images: list[str] = []               # workspace-relative paths to reference images
    timeout_s: int = 600
    system_prompt_append: str | None = None


class _ToolTaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["tool"]
    tool: str
    args: dict = {}
    deps: list[str] = []


class _SubgraphTaskModel(BaseModel):
    """Runtime fan-out: when ``deps`` complete, the runner reads the parent's
    output at ``expand_from`` and synthesizes per-child tasks via the strategy
    registered under ``expansion_kind`` in ``topos/orchestrator/expand.py``.
    Downstream tasks depend on this id; success ⇔ all children succeed.
    See ADR-0008.
    """
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["subgraph"]
    expand_from: str
    expansion_kind: str
    deps: list[str] = []
    timeout_s: int = 60


_TaskUnion = Union[_AgentTaskModel, _ToolTaskModel, _SubgraphTaskModel]


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str
    iter_policy: IterPolicy = Field(default_factory=IterPolicy)
    tasks: list[_TaskUnion]

    @field_validator("tasks")
    @classmethod
    def _validate_unique_ids(cls, v):
        seen: set[str] = set()
        for t in v:
            if t.id in seen:
                raise ValueError(f"duplicate task id: {t.id!r}")
            seen.add(t.id)
        return v

    def materialised(self) -> list[Task]:
        out: list[Task] = []
        for t in self.tasks:
            if isinstance(t, _AgentTaskModel):
                out.append(AgentTask(
                    id=t.id, goal=t.goal, backend=t.backend, deps=list(t.deps),
                    allowed_tools=list(t.allowed_tools),
                    skills=list(t.skills),
                    images=list(t.images),
                    timeout_s=t.timeout_s,
                    system_prompt_append=t.system_prompt_append,
                ))
            elif isinstance(t, _SubgraphTaskModel):
                out.append(SubgraphTask(
                    id=t.id,
                    expand_from=t.expand_from,
                    expansion_kind=t.expansion_kind,
                    deps=list(t.deps),
                    timeout_s=t.timeout_s,
                ))
            else:
                out.append(ToolTask(
                    id=t.id, tool=t.tool, args=dict(t.args), deps=list(t.deps),
                ))
        return out


def _resolve_goal_file(goal_file: str, plan_dir: Path, *, task_id: str | None = None) -> str:
    """Resolve a ``goal_file`` reference to its text content.

    Two reference styles are supported:

    1. ``topos:<relpath>`` — looked up inside the installed ``topos.prompts``
       package via ``importlib.resources``. This is the **recommended** way
       because it survives ``topos init`` copying a plan into a workspace
       (the resolution doesn't depend on relative paths from the workspace).

    2. Plain relative path — resolved relative to the plan.json's directory.
       Useful for project-specific one-off prompts colocated with the plan.
    """
    where = f" (task {task_id!r})" if task_id else ""
    if goal_file.startswith("topos:"):
        rel = goal_file[len("topos:"):]
        from importlib import resources
        try:
            ref = resources.files("topos").joinpath("prompts").joinpath(rel)
        except (ModuleNotFoundError, FileNotFoundError) as e:
            raise FileNotFoundError(
                f"goal_file{where}: cannot resolve topos:{rel} ({e})"
            )
        if not ref.is_file():
            raise FileNotFoundError(
                f"goal_file{where}: topos:{rel} → {ref} does not exist"
            )
        return ref.read_text(encoding="utf-8")
    goal_path = (plan_dir / goal_file).resolve()
    if not goal_path.is_file():
        raise FileNotFoundError(
            f"goal_file{where}: {goal_path} does not exist"
        )
    return goal_path.read_text(encoding="utf-8")


def _resolve_goal_template(
    template_ref: str,
    raw_params: dict,
    plan_dir: Path,
    *,
    task_id: str | None = None,
) -> str:
    """Render a Jinja2 template (referenced by topos: or relative path) with
    the given params. Param keys ending with ``_file`` are auto-resolved as
    nested file references (using the same scheme as goal_file) and the file
    contents are passed under the stripped key. So::

        "goal_params": {"part_name": "Frame", "extras_file": "./prompts/extras_frame.md"}

    becomes a template render with::

        part_name="Frame"
        extras="<contents of prompts/extras_frame.md>"
    """
    template_text = _resolve_goal_file(template_ref, plan_dir, task_id=task_id)
    where = f" (task {task_id!r})" if task_id else ""
    resolved_params: dict[str, object] = {}
    for k, v in raw_params.items():
        if k.endswith("_file") and isinstance(v, str):
            inner_key = k[:-5]  # strip the trailing "_file"
            if not inner_key:
                raise ValueError(
                    f"goal_params{where}: key {k!r} has no name before '_file'"
                )
            resolved_params[inner_key] = _resolve_goal_file(v, plan_dir, task_id=task_id)
        else:
            resolved_params[k] = v

    from jinja2 import Environment, StrictUndefined
    env = Environment(
        undefined=StrictUndefined,   # fail loudly on missing param
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    try:
        template = env.from_string(template_text)
        return template.render(**resolved_params)
    except Exception as e:
        raise ValueError(
            f"goal_template{where}: jinja2 render failed for {template_ref!r}: {e}"
        ) from e


def load_plan(path: Path) -> Plan:
    """Load and validate a plan from JSON or YAML.

    Agent tasks may use ``goal`` (inline string) or ``goal_file`` (path
    relative to the plan file's directory, pointing at a .md/.txt file). At
    load time ``goal_file`` is resolved and its content is used as the goal.
    Exactly one of the two must be set per agent task.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    plan_dir = path.parent
    for t in data.get("tasks") or []:
        if not isinstance(t, dict) or t.get("kind") != "agent":
            continue
        has_inline = bool(t.get("goal"))
        has_file = bool(t.get("goal_file"))
        has_template = bool(t.get("goal_template"))
        present = sum(int(b) for b in (has_inline, has_file, has_template))
        if present == 0:
            raise ValueError(
                f"agent task {t.get('id')!r}: must set 'goal', 'goal_file', or 'goal_template'"
            )
        if present > 1:
            raise ValueError(
                f"agent task {t.get('id')!r}: 'goal', 'goal_file', and 'goal_template' are mutually exclusive"
            )
        if has_file:
            t["goal"] = _resolve_goal_file(t["goal_file"], plan_dir, task_id=t.get('id'))
        elif has_template:
            t["goal"] = _resolve_goal_template(
                t["goal_template"],
                t.get("goal_params") or {},
                plan_dir,
                task_id=t.get('id'),
            )

    return Plan.model_validate(data)


def topo_sort(tasks: list[Task]) -> list[Task]:
    """Stable Kahn sort; raises ``ValueError`` on cycle."""
    by_id = {t.id: t for t in tasks}
    indeg: dict[str, int] = {t.id: 0 for t in tasks}
    for t in tasks:
        for d in t.deps:
            if d not in by_id:
                raise ValueError(f"task {t.id!r} depends on unknown task {d!r}")
            indeg[t.id] += 1

    ready = [t for t in tasks if indeg[t.id] == 0]
    ordered: list[Task] = []
    while ready:
        head = ready.pop(0)
        ordered.append(head)
        for t in tasks:
            if head.id in t.deps:
                indeg[t.id] -= 1
                if indeg[t.id] == 0:
                    ready.append(t)

    if len(ordered) != len(tasks):
        remaining = [t.id for t in tasks if t not in ordered]
        raise ValueError(f"cycle detected; unresolved tasks: {remaining}")
    return ordered
