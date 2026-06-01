"""Static render tools that run the agent's geometry script under Blender
via ``topos/tools/blender_render/wrapper.py``. Two flavors, both wired into the
articulated pipeline:

- ``render_multiview`` — 8 octant views, the standard eval set for the judge
                         (scheduled by ``plan_generator`` for the assembly)
- ``render_part``      — per-part isolated views for the component vision critic
                         (scheduled by the ``articulated_parts`` expander)

Output paths are relative to ``workspace``. The agent's ``src/build.py`` is
expected to be pure geometry (no camera, no lights, no render config) —
see ADR 0005.
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


def _common_input_schema_extras() -> dict:
    return {
        "resolution": {"type": "integer", "default": 512, "minimum": 64, "maximum": 4096},
        "engine": {"type": "string", "enum": ["workbench", "eevee", "cycles"], "default": "workbench"},
        "coloring": {"type": "string", "enum": ["as_authored", "palette"], "default": "as_authored"},
        "view_prefix": {"type": "string", "default": "view_"},
        "timeout_s": {"type": "integer", "default": 180, "minimum": 5, "maximum": 1800},
    }


def _spawn_wrapper(
    *,
    workspace: Path,
    wrapper_args: list[str],
    timeout_s: int,
) -> tuple[str, str, int, float, list[Path], bool]:
    """Spawn ``blender --background --python render_wrapper.py -- <args>`` and
    surface artifacts produced (PNG files under the output dir).

    Returns ``(stdout, stderr, returncode, duration_s, new_files, timed_out)``.
    """
    binary = resolve_blender_binary()
    cmd = [binary, "--background", "--python", str(_WRAPPER), "--", *wrapper_args]

    # Snapshot artifact-dir mtimes (output-dir is passed via wrapper_args)
    out_idx = wrapper_args.index("--output-dir") + 1
    output_dir = Path(wrapper_args[out_idx])
    before = (
        {p: p.stat().st_mtime for p in output_dir.rglob("*") if p.is_file()}
        if output_dir.exists() else {}
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    start = time.monotonic()
    result = run_process(cmd, cwd=workspace, timeout_s=timeout_s)
    duration_s = time.monotonic() - start

    new_files: list[Path] = []
    for p in output_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() != ".png":
            continue
        prev = before.get(p)
        if prev is None or p.stat().st_mtime > prev + 1e-6:
            new_files.append(p)
    new_files.sort()
    return (
        result.stdout,
        result.stderr,
        result.returncode,
        duration_s,
        new_files,
        result.timed_out,
    )


def _spawn_and_assemble(
    *,
    ws: Path,
    wrapper_args: list[str],
    timeout_s: int,
) -> tuple[dict[str, Any], list[Path]]:
    """Spawn the render wrapper and assemble the standard render-tool return
    dict (success / exit_code / duration_s / artifacts / warnings / full
    stdout + stderr). Tool-specific extras (``by_part`` / ``frame_count`` /
    ...) are added by the caller, which is why this returns the raw
    ``artifacts`` Path list alongside the dict — the caller needs Paths to
    compute things like the per-part grouping in ``render_part``.

    Note: stdout and stderr are emitted in full (no truncation). Downstream
    persistence — ``output.json`` in the trajectory dir, ``run_report.json`` —
    keeps the entire subprocess output so postmortem has everything.
    """
    stdout, stderr, exit_code, duration_s, artifacts, timed_out = _spawn_wrapper(
        workspace=ws, wrapper_args=wrapper_args, timeout_s=timeout_s,
    )
    ok = judge_subprocess_success(
        returncode=exit_code, timed_out=timed_out,
        stderr=stderr, artifacts=artifacts, expects_artifacts=True,
    )
    result: dict[str, Any] = {
        "success": ok,
        "exit_code": exit_code,
        "duration_s": duration_s,
        "artifacts": [str(p.relative_to(ws)) for p in artifacts],
        "warnings": extract_contract_warnings(stdout),
        "stdout": stdout,
        "stderr": stderr,
    }
    return result, artifacts


# ---------- render_multiview (8 octant) ----------

@tool(
    "render_multiview",
    description=(
        "Render the project from 8 standard octant viewpoints (4 azimuths × 2 elevations). "
        "Produces eight PNGs the judge can evaluate from multiple angles. The agent's "
        "script must be pure geometry."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "script_relpath": {"type": "string"},
            "output_subdir": {"type": "string", "default": "artifacts"},
            "n_views": {"type": "integer", "default": 8, "minimum": 1, "maximum": 8},
            **_common_input_schema_extras(),
        },
        "required": ["workspace", "script_relpath"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "artifacts": {"type": "array", "items": {"type": "string"}},
            "exit_code": {"type": "integer"},
            "duration_s": {"type": "number"},
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
def render_multiview(
    *,
    workspace: str,
    script_relpath: str,
    output_subdir: str = "artifacts",
    n_views: int = 8,
    resolution: int = 512,
    engine: str = "workbench",
    coloring: str = "as_authored",
    view_prefix: str = "view_",
    timeout_s: int = 300,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    script = resolve_under_workspace(ws, script_relpath, label="script_relpath")
    output_dir = resolve_under_workspace(ws, output_subdir, label="output_subdir")

    args = [
        "--mode", "multiview",
        "--script", str(script),
        "--output-dir", str(output_dir),
        "--n-views", str(n_views),
        "--resolution", str(resolution),
        "--engine", engine,
        "--coloring", coloring,
        "--view-prefix", view_prefix,
    ]
    result, _ = _spawn_and_assemble(ws=ws, wrapper_args=args, timeout_s=timeout_s)
    return result


# ---------- render_part (per-part isolated views for component-level vision critic) ----------

@tool(
    "render_part",
    description=(
        "Render one or more parts in isolation (other parts hidden), each at "
        "tight framing. Output is `<output_subdir>/<part_name>/view_*.png`. "
        "Used by the per-part vision critic stage so each part's shape can be "
        "judged independently of how it fits in the assembly."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "parts_dir_relpath": {"type": "string", "default": "src/parts",
                                    "description": "Where parts/<lower>.py files live (relative to workspace). "
                                                   "Per-part renders import these directly — no dependency on build.py."},
            "output_subdir": {"type": "string", "default": "artifacts/parts_render"},
            "parts": {
                "type": "array",
                "description": "List of MESH names to render (Frame/Drawer/Handle etc); each must have a parts/<lower>.py with build_<lower>().",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "n_views": {"type": "integer", "default": 4, "minimum": 1, "maximum": 8,
                         "description": "Views per part. 4 = main octants; 8 = full octant set."},
            **_common_input_schema_extras(),
        },
        "required": ["workspace", "parts"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "exit_code": {"type": "integer"},
            "duration_s": {"type": "number"},
            "artifacts": {"type": "array", "items": {"type": "string"}},
            "by_part": {"type": "object",
                         "description": "Map of part name → list of image relpaths."},
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
def render_part(
    *,
    workspace: str,
    parts: list[str],
    parts_dir_relpath: str = "src/parts",
    output_subdir: str = "artifacts/parts_render",
    n_views: int = 4,
    resolution: int = 384,
    engine: str = "eevee",
    coloring: str = "as_authored",
    view_prefix: str = "view_",
    timeout_s: int = 360,
    # backward-compat: accept script_relpath but ignore (some plan.jsons still pass it)
    script_relpath: str | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    parts_dir = resolve_under_workspace(ws, parts_dir_relpath, label="parts_dir_relpath")
    if not parts_dir.is_dir():
        raise ValueError(f"parts_dir does not exist: {parts_dir}")
    output_dir = resolve_under_workspace(ws, output_subdir, label="output_subdir")

    args = [
        "--mode", "part",
        "--parts-dir", str(parts_dir),
        "--output-dir", str(output_dir),
        "--parts", ",".join(parts),
        "--part-n-views", str(n_views),
        "--resolution", str(resolution),
        "--engine", engine,
        "--coloring", coloring,
        "--view-prefix", view_prefix,
    ]
    result, artifacts = _spawn_and_assemble(ws=ws, wrapper_args=args, timeout_s=timeout_s)

    # Group images by which part subdir they live in:
    # ``<output_subdir>/<part_name>/view_*.png``.
    by_part: dict[str, list[str]] = {}
    for p in artifacts:
        rel = p.relative_to(ws)
        for known in parts:
            if known in rel.parts:
                by_part.setdefault(known, []).append(str(rel))
                break
    result["by_part"] = by_part
    return result
