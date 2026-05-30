"""``verify_parts`` tool — lightweight buildability check for part .py files.

For each part name supplied, runs Blender in background once, imports the
agent-authored ``parts/<snake>.py``, calls ``build_<snake>()``, and asserts
a non-None ``bpy.types.Object`` of type ``MESH`` is returned. **No
rendering.** Pure code-can-run check.

Use cases:

- **Framework gate (DAG)** — runs between part agents and render_parts.
  If any part fails verify, the orchestrator triggers a runtime fix-loop
  on the failing agent(s) instead of letting the failure cascade into
  the render step.
- **Coding agent self-check** — agents can invoke this via Bash
  (``topos verify-parts <Name1> <Name2>``) after Writing their .py file
  to catch Blender-version API drift or syntax errors before declaring
  the task done.

Output mirrors the structured failure records the framework's runtime
fix-loop expects, so the same path through fix_loop.py handles both
framework-initiated and agent-initiated verification.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .._blender_subprocess import resolve_blender_binary
from .._paths import resolve_under_workspace
from ...process import run_process
from ..registry import tool


_WRAPPER = Path(__file__).resolve().parent / "wrapper.py"


INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "workspace": {"type": "string", "description": "Absolute path to the project workspace."},
        "parts_dir_relpath": {"type": "string", "default": "src/parts",
                              "description": "Path to the parts/ dir relative to workspace."},
        "parts": {"type": "array", "items": {"type": "string"},
                  "description": "PascalCase part names to verify."},
        "output_relpath": {"type": "string", "default": "scratch/verify_parts_result.json",
                           "description": "Where the per-part JSON result is written, relative to workspace."},
        "timeout_s": {"type": "integer", "default": 120, "minimum": 5, "maximum": 1800},
    },
    "required": ["workspace", "parts"],
}


OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean",
                    "description": "True iff every requested part verified successfully."},
        "exit_code": {"type": "integer"},
        "duration_s": {"type": "number"},
        "total": {"type": "integer"},
        "passed_parts": {"type": "array", "items": {"type": "string"},
                   "description": "Names of parts that verified OK."},
        "failed_parts": {"type": "array",
                         "items": {"type": "object"},
                         "description": "Per-failed-part records with name, stage, error_class, error_msg, traceback."},
        "results": {"type": "array", "items": {"type": "object"},
                    "description": "Full per-part records (passed and failed)."},
        "stdout": {"type": "string", "description": "Full subprocess stdout (no truncation)."},
        "stderr": {"type": "string", "description": "Full subprocess stderr (no truncation)."},
    },
    "required": ["success", "exit_code", "total", "passed_parts", "failed_parts"],
}


@tool(
    "verify_parts",
    description=(
        "Lightweight buildability check: for each named part, run Blender "
        "in background, import parts/<lower>.py, call build_<lower>(), and "
        "assert a non-None MESH object was produced. No rendering — pure "
        "code-can-run verification. Returns per-part pass/fail with error "
        "class + traceback for any failures. Used as a framework gate before "
        "render_parts AND as a self-check the coding agent can invoke."
    ),
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    side_effects=False,
    deterministic=True,
)
def verify_parts(
    *,
    workspace: str,
    parts: list[str],
    parts_dir_relpath: str = "src/parts",
    output_relpath: str = "scratch/verify_parts_result.json",
    timeout_s: int = 120,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    parts_dir = resolve_under_workspace(ws, parts_dir_relpath, label="parts_dir_relpath")
    if not parts_dir.is_dir():
        raise ValueError(f"parts_dir does not exist: {parts_dir}")
    if not parts:
        raise ValueError("verify_parts: 'parts' list is empty")

    output_path = resolve_under_workspace(ws, output_relpath, label="output_relpath")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    binary = resolve_blender_binary()
    cmd = [
        binary, "--background", "--python", str(_WRAPPER), "--",
        "--parts-dir", str(parts_dir),
        "--parts", ",".join(parts),
        "--output-json", str(output_path),
    ]

    start = time.monotonic()
    result = run_process(cmd, cwd=ws, timeout_s=timeout_s)
    duration_s = time.monotonic() - start

    # Read the JSON the wrapper wrote (it always writes on a non-catastrophic
    # exit; if the file is missing, surface that as the failure).
    streams = {"stdout": result.stdout, "stderr": result.stderr}
    if not output_path.is_file():
        return {
            "success": False,
            "exit_code": result.returncode,
            "duration_s": duration_s,
            "total": len(parts),
            "passed_parts": [],
            "failed_parts": [{
                "name": "<wrapper>",
                "stage": "wrapper_missing_output",
                "error_class": "RuntimeError",
                "error_msg": f"verify_wrapper did not write {output_path}",
                "traceback": "",
            }],
            "results": [],
            **streams,
        }

    try:
        summary = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "exit_code": result.returncode,
            "duration_s": duration_s,
            "total": len(parts),
            "passed_parts": [],
            "failed_parts": [{
                "name": "<wrapper>",
                "stage": "wrapper_bad_output",
                "error_class": "JSONDecodeError",
                "error_msg": str(e),
                "traceback": "",
            }],
            "results": [],
            **streams,
        }

    failed_parts = summary.get("failed_parts") or []
    return {
        "success": (result.returncode == 0 and not result.timed_out and not failed_parts),
        "exit_code": result.returncode,
        "duration_s": duration_s,
        "total": summary.get("total", len(parts)),
        "passed_parts": summary.get("passed_parts") or [],
        "failed_parts": failed_parts,
        "results": summary.get("results") or [],
        **streams,
    }
