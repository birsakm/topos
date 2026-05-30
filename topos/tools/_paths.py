"""Path-validation helper shared across ``@tool`` implementations.

Tools receive relative path strings from plan.json / MCP-style agent calls.
Before joining them onto the workspace and passing to Blender / urllib /
shell, those paths must be checked: a hallucinated
``output_subdir="../../tmp/escape"`` would otherwise let an agent write
outside the workspace, and the failure would only surface at the trailing
``relative_to(ws)`` after the (expensive) subprocess already finished.

Every tool used to inline the same three lines (``ws / rel`` resolve →
``is_relative_to`` check → ``raise``). Some tools forgot one or more
parameters, and at least one had drift between two output params in the
same function. Centralising the check here makes "forgot the check"
impossible by construction: there's exactly one way to take a relpath."""

from __future__ import annotations

from pathlib import Path


def resolve_under_workspace(ws: Path, rel: str, *, label: str) -> Path:
    """Resolve ``ws/rel`` and assert it stays under ``ws``.

    ``label`` is the parameter name as the caller sees it
    (``"output_subdir"``, ``"script_relpath"``, ...) and is echoed in the
    error so a misbehaving agent's traceback names the exact arg.

    Existence is NOT checked here — inputs need ``.is_file()`` /
    ``.is_dir()``, outputs typically need ``.parent.mkdir(parents=True)``
    afterwards; both stay at the call site so the read/write intent
    remains obvious.
    """
    if not isinstance(rel, str):
        raise TypeError(f"{label}: expected str, got {type(rel).__name__}")
    out = (ws / rel).resolve()
    if not out.is_relative_to(ws):
        raise ValueError(f"{label} escapes workspace: {rel!r}")
    return out
