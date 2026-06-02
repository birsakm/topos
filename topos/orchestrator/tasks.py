"""Task dataclasses for the DAG runner.

Three task kinds:

* ``AgentTask`` — a coding-agent call (Claude / Codex / Gemini CLI). Writes
  source files into the workspace.
* ``ToolTask`` — a deterministic tool call (render, judge, export_glb, ...).
  Reads the workspace, writes artifacts.
* ``SubgraphTask`` — a runtime-expanded fan-out node. When its ``deps`` finish,
  the runner reads ``expand_from`` (a design doc written by a parent agent)
  and splices per-child tasks into the live DAG; see ADR-0008 and
  ``topos/orchestrator/expand.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union


@dataclass
class AgentTask:
    id: str
    goal: str
    backend: str = "claude"
    deps: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)     # capability bundles injected into the prompt
    timeout_s: int = 600
    images: list[str] = field(default_factory=list)  # workspace-relative image paths for reference
    system_prompt_append: str | None = None
    # Workspace-relative files this task MUST produce to count as successful.
    # A CLI can report success while doing nothing — gemini-cli intermittently
    # ends a turn with no tool calls (a "no-op turn"); without this guard a
    # no-op design agent reports success and the run later CRASHES at subgraph
    # expansion when src/design.json is absent. When declared, the runner
    # validates presence post-run (retry-once, then fail loud). Empty = no check.
    expected_outputs: list[str] = field(default_factory=list)
    kind: Literal["agent"] = "agent"
    # When True, signals to the runner: this AgentTask is a fix-loop re-run
    # that REUSES the original task's ID (so downstream DAG deps auto-resolve
    # to the fixed version). Without it, the carry-forward path would see a
    # prior successful result under the same ID and skip — defeating the
    # fix. Defaults False so plan.json-defined tasks behave as before.
    is_fix_rerun: bool = False


@dataclass
class ToolTask:
    id: str
    tool: str
    args: dict = field(default_factory=dict)
    deps: list[str] = field(default_factory=list)
    kind: Literal["tool"] = "tool"


@dataclass
class SubgraphTask:
    """Runtime fan-out node. Parent agent writes a design doc at ``expand_from``;
    the runner reads it after deps complete, calls the strategy keyed by
    ``expansion_kind`` in ``topos/orchestrator/expand.py``, and splices the
    returned child tasks into the live DAG with namespaced ids
    (``<this.id>__<local_child_id>``). Downstream tasks depend on the
    SubgraphTask's id; it's considered successful when all children are.
    """
    id: str
    expand_from: str          # workspace-relative path, e.g. "src/design.json"
    expansion_kind: str       # registry key in expand._EXPANDERS
    deps: list[str] = field(default_factory=list)
    timeout_s: int = 60       # expansion is deterministic Python; safety only
    kind: Literal["subgraph"] = "subgraph"


Task = Union[AgentTask, ToolTask, SubgraphTask]
