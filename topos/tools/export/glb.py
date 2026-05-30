"""``export_glb`` tool: run agent's geometry script in Blender and emit a
single ``.glb`` for the resulting scene. Cameras/lights are stripped before
export so the file contains only the modelled geometry.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .._blender_subprocess import resolve_blender_binary
from .._paths import resolve_under_workspace
from .._success import judge_subprocess_success
from .._warnings import extract_contract_warnings
from ...process import run_process
from ..registry import tool


_WRAPPER = Path(__file__).resolve().parent / "wrapper.py"


@tool(
    "export_glb",
    description=(
        "Export the agent's geometry script as a single GLB file (binary glTF) "
        "via Blender. The agent's script must be pure geometry — cameras and "
        "lights are stripped before export."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "script_relpath": {"type": "string"},
            "output_relpath": {"type": "string", "default": "artifacts/object.glb"},
            "timeout_s": {"type": "integer", "default": 300, "minimum": 5, "maximum": 1800},
            "bake_procedural": {
                "type": "string", "enum": ["on", "off"], "default": "on",
                "description": (
                    "Bake procedural shader Base Color (Wave/Noise/etc) to "
                    "an embedded image before export, so the GLB carries real "
                    "textures. 'off' skips bake (materials become empty PBR)."
                ),
            },
            "bake_resolution": {
                "type": "integer", "default": 1024, "minimum": 128, "maximum": 4096,
                "description": "Pixels per side for baked textures (only when bake_procedural=on).",
            },
            "texture_save_relpath": {
                "type": "string", "default": "artifacts/textures",
                "description": (
                    "Dir (relative to workspace) for inspectable PNG copies of "
                    "textures produced by procedural baking. Textures always "
                    "embed in the GLB regardless; this is for visibility."
                ),
            },
        },
        "required": ["workspace", "script_relpath"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "glb_path": {"type": "string"},
            "byte_size": {"type": "integer"},
            "duration_s": {"type": "number"},
            "exit_code": {"type": "integer"},
            "stdout": {"type": "string", "description": "Full subprocess stdout (no truncation)."},
            "stderr": {"type": "string", "description": "Full subprocess stderr (no truncation)."},
            "warnings": {
                "type": "array", "items": {"type": "string"},
                "description": "Build-time geometry contract warnings ([*_WARN] lines) lifted from stdout regardless of tail truncation.",
            },
        },
    },
    deterministic=True,
)
def export_glb(
    *,
    workspace: str,
    script_relpath: str,
    output_relpath: str = "artifacts/object.glb",
    timeout_s: int = 300,
    bake_procedural: str = "on",
    bake_resolution: int = 1024,
    texture_save_relpath: str | None = "artifacts/textures",
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    script = resolve_under_workspace(ws, script_relpath, label="script_relpath")
    out = resolve_under_workspace(ws, output_relpath, label="output_relpath")
    out.parent.mkdir(parents=True, exist_ok=True)
    texture_save_dir: Path | None = None
    if texture_save_relpath:
        texture_save_dir = resolve_under_workspace(
            ws, texture_save_relpath, label="texture_save_relpath",
        )

    binary = resolve_blender_binary()
    cmd = [
        binary, "--background", "--python", str(_WRAPPER), "--",
        "--mode", "glb",
        "--script", str(script),
        "--output", str(out),
        "--bake-procedural", bake_procedural,
        "--bake-resolution", str(bake_resolution),
    ]
    if texture_save_dir is not None:
        cmd.extend(["--texture-save-dir", str(texture_save_dir)])
    start = time.monotonic()
    proc = run_process(cmd, cwd=ws, timeout_s=timeout_s)
    duration_s = time.monotonic() - start
    # success = exit_clean AND no python traceback in stderr AND .glb actually
    # exists on disk. The original check missed the traceback case — blender's
    # `--background --python` exits 0 even if the script raised mid-export,
    # leaving zero output. Without the traceback check, downstream judges
    # trust the lie and crash with a FileNotFoundError seconds later.
    success = (
        judge_subprocess_success(
            returncode=proc.returncode,
            timed_out=proc.timed_out,
            stderr=proc.stderr,
            artifacts=None,            # GLB-specific artifact check below
            expects_artifacts=False,
        )
        and out.is_file()
    )
    return {
        "success": success,
        "glb_path": str(out.relative_to(ws)) if out.is_file() else "",
        "byte_size": out.stat().st_size if out.is_file() else 0,
        "duration_s": duration_s,
        "exit_code": proc.returncode,
        "warnings": extract_contract_warnings(proc.stdout),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
