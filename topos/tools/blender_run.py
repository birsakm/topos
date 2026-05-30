"""``blender_run`` tool: execute a Blender Python script located inside a
project workspace.

Returned ``artifacts`` are paths *relative to the workspace*, so they are
agent-friendly and round-trip cleanly through JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._blender_subprocess import run_blender as _run_blender
from .registry import tool


INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "workspace": {"type": "string", "description": "Absolute path to the project workspace."},
        "script_relpath": {"type": "string", "description": "Path to the Blender Python script, relative to workspace."},
        "timeout_s": {"type": "integer", "default": 120, "minimum": 5, "maximum": 3600},
        "script_args": {"type": "array", "items": {"type": "string"}, "default": []},
        "hot_pool": {"type": "boolean", "default": False},
    },
    "required": ["workspace", "script_relpath"],
}

OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "exit_code": {"type": "integer"},
        "duration_s": {"type": "number"},
        "stdout": {"type": "string", "description": "Full subprocess stdout (no truncation)."},
        "stderr": {"type": "string", "description": "Full subprocess stderr (no truncation)."},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "timed_out": {"type": "boolean"},
    },
    "required": ["success", "exit_code", "artifacts"],
}


@tool(
    "blender_run",
    description=(
        "Run a Blender Python script in the project workspace using a "
        "stateless `blender --background --python` subprocess. Returns "
        "full stdout/stderr and the list of files created or modified."
    ),
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    side_effects=True,
)
def blender_run(
    *,
    workspace: str,
    script_relpath: str,
    timeout_s: int = 120,
    script_args: list[str] | None = None,
    hot_pool: bool = False,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    script = (ws / script_relpath).resolve()
    if not script.is_relative_to(ws):
        raise ValueError(f"script_relpath escapes workspace: {script_relpath!r}")
    result = _run_blender(
        script,
        cwd=ws,
        hot_pool=hot_pool,
        timeout_s=timeout_s,
        script_args=script_args,
    )
    return {
        "success": result.success,
        "exit_code": result.exit_code,
        "duration_s": result.duration_s,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "artifacts": [str(p.relative_to(ws)) for p in result.artifacts],
        "timed_out": result.timed_out,
    }
