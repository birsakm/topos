"""``export_urdf`` tool: produce a URDF + per-part OBJ files from the agent's
geometry script and the agent's joints spec.

Pipeline:
1. Spawn Blender + ``export_wrapper.py --mode parts`` to dump every named MESH
   object to ``artifacts/parts/<name>.obj`` (in object-local coordinates) plus
   a ``parts/manifest.json`` recording each object's world transform.
2. Load the agent-authored joints spec (YAML at ``src/joints.yaml`` by default;
   structure documented in ``topos.urdf.from_dict``).
3. Merge the manifest (where each MESH was positioned in world) into the spec
   (link → mesh_path + world_xyz), validate, and call the URDF writer.

The resulting ``.urdf`` references ``parts/<name>.obj`` relatively, so the
``artifacts/`` directory is a self-contained URDF package.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import yaml

from .._blender_subprocess import resolve_blender_binary
from .._paths import resolve_under_workspace
from .._success import judge_subprocess_success
from ...process import run_process
from ..registry import tool
from topos.urdf import Joint, Link, write_urdf


_WRAPPER = Path(__file__).resolve().parent / "wrapper.py"


@tool(
    "export_urdf",
    description=(
        "Export the project as a URDF package: per-part OBJ meshes (in local "
        "coordinates) + a URDF file describing links and joints. The agent's "
        "geometry script provides mesh objects (named to match URDF links); a "
        "separate joints spec (YAML) provides the articulation."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "script_relpath": {"type": "string"},
            "joints_relpath": {"type": "string", "default": "src/joints.yaml"},
            "output_urdf_relpath": {"type": "string", "default": "artifacts/object.urdf"},
            "parts_subdir": {"type": "string", "default": "artifacts/parts"},
            "timeout_s": {"type": "integer", "default": 300, "minimum": 5, "maximum": 1800},
            "bake_procedural": {
                "type": "string", "enum": ["on", "off"], "default": "on",
                "description": (
                    "Bake procedural shaders to embedded images before export. "
                    "Per-part GLB files will carry real textures; URDF mesh refs "
                    "point at these per-part GLBs, so robotics viewers "
                    "(RViz / Webots / Gazebo) get the materials."
                ),
            },
            "bake_resolution": {
                "type": "integer", "default": 1024, "minimum": 128, "maximum": 4096,
            },
        },
        "required": ["workspace", "script_relpath"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "urdf_path": {"type": "string"},
            "parts_dir": {"type": "string"},
            "link_count": {"type": "integer"},
            "joint_count": {"type": "integer"},
            "duration_s": {"type": "number"},
            "exit_code": {"type": "integer"},
            "stdout": {"type": "string", "description": "Full subprocess stdout (no truncation)."},
            "stderr": {"type": "string", "description": "Full subprocess stderr (no truncation)."},
            "error": {"type": "string"},
        },
    },
    deterministic=True,
)
def export_urdf(
    *,
    workspace: str,
    script_relpath: str,
    joints_relpath: str = "src/joints.yaml",
    output_urdf_relpath: str = "artifacts/object.urdf",
    parts_subdir: str = "artifacts/parts",
    timeout_s: int = 300,
    bake_procedural: str = "on",
    bake_resolution: int = 1024,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    script = resolve_under_workspace(ws, script_relpath, label="script_relpath")
    joints_path = resolve_under_workspace(ws, joints_relpath, label="joints_relpath")
    parts_dir = resolve_under_workspace(ws, parts_subdir, label="parts_subdir")
    urdf_path = resolve_under_workspace(ws, output_urdf_relpath, label="output_urdf_relpath")

    for p in (script, joints_path):
        if not p.is_file():
            return _err(f"missing required input: {p}")

    parts_dir.mkdir(parents=True, exist_ok=True)
    urdf_path.parent.mkdir(parents=True, exist_ok=True)

    # ---- Step 1: spawn Blender to dump per-part OBJ + manifest.json ----
    binary = resolve_blender_binary()
    cmd = [
        binary, "--background", "--python", str(_WRAPPER), "--",
        "--mode", "parts",
        "--script", str(script),
        "--output-dir", str(parts_dir),
        "--bake-procedural", bake_procedural,
        "--bake-resolution", str(bake_resolution),
    ]
    start = time.monotonic()
    proc = run_process(cmd, cwd=ws, timeout_s=timeout_s)
    duration_s = time.monotonic() - start

    # Detect the silent-failure case: blender exited 0 but the agent's
    # script raised inside `--background --python`. Without this check, we'd
    # progress to "load manifest" and hit a confusing "manifest not found"
    # error instead of surfacing the python traceback.
    subprocess_ok = judge_subprocess_success(
        returncode=proc.returncode,
        timed_out=proc.timed_out,
        stderr=proc.stderr,
        artifacts=None,
        expects_artifacts=False,
    )
    if not subprocess_ok:
        return {
            "success": False,
            "urdf_path": "",
            "parts_dir": str(parts_dir.relative_to(ws)),
            "link_count": 0,
            "joint_count": 0,
            "duration_s": duration_s,
            "exit_code": proc.returncode,
            "error": "blender per-part export failed",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    manifest_path = parts_dir / "manifest.json"
    if not manifest_path.is_file():
        return _err(f"export_wrapper did not produce manifest at {manifest_path}",
                    duration_s=duration_s, exit_code=proc.returncode,
                    stdout=proc.stdout, stderr=proc.stderr)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return _err(f"manifest at {manifest_path} is not valid JSON: {e}",
                    duration_s=duration_s, exit_code=proc.returncode,
                    stdout=proc.stdout, stderr=proc.stderr)
    if not isinstance(manifest, dict):
        return _err(f"manifest at {manifest_path} is not a JSON object",
                    duration_s=duration_s, exit_code=proc.returncode,
                    stdout=proc.stdout, stderr=proc.stderr)
    try:
        obj_by_name = {o["name"]: o for o in manifest.get("objects", []) if isinstance(o, dict)}
    except KeyError:
        return _err("manifest contains object entries missing the 'name' field",
                    duration_s=duration_s, exit_code=proc.returncode,
                    stdout=proc.stdout, stderr=proc.stderr)

    # ---- Step 2: load joints spec ----
    try:
        spec = yaml.safe_load(joints_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return _err(f"joints spec at {joints_path} has YAML syntax error: {e}")
    if not isinstance(spec, dict):
        return _err(f"joints spec at {joints_path} is not a YAML mapping")

    robot_name = spec.get("robot") or ws.name
    raw_links = spec.get("links") or []
    raw_joints = spec.get("joints") or []

    # ---- Step 3: merge manifest into link specs ----
    links: list[Link] = []
    parts_rel = parts_dir.relative_to(urdf_path.parent)
    for i, lspec in enumerate(raw_links):
        if not isinstance(lspec, dict):
            return _err(f"links[{i}] is not a mapping")
        name = lspec.get("name")
        if not name:
            return _err(f"links[{i}] missing required 'name' field")
        bpy_obj_name = lspec.get("object") or name
        if bpy_obj_name not in obj_by_name:
            return _err(
                f"link {name!r} references bpy object {bpy_obj_name!r} that the "
                f"geometry script did not produce. produced: {sorted(obj_by_name)}"
            )
        info = obj_by_name[bpy_obj_name]
        mesh_filename = info.get("mesh_path")
        if not mesh_filename:
            return _err(f"manifest entry for {bpy_obj_name!r} missing mesh_path")
        xyz = info.get("world_xyz")
        if not isinstance(xyz, (list, tuple)) or len(xyz) < 3:
            return _err(f"manifest entry for {bpy_obj_name!r}: world_xyz must be a list of 3 numbers")
        rpy = info.get("world_rpy") or (0.0, 0.0, 0.0)
        color = lspec.get("color_rgba")
        links.append(Link(
            name=name,
            mesh_path=str(parts_rel / mesh_filename),
            world_xyz=tuple(float(v) for v in xyz[:3]),  # type: ignore[arg-type]
            world_rpy=tuple(float(v) for v in rpy[:3]),  # type: ignore[arg-type]
            color_rgba=tuple(float(v) for v in color[:4]) if color else None,  # type: ignore[arg-type]
        ))

    joints: list[Joint] = []
    for i, jspec in enumerate(raw_joints):
        if not isinstance(jspec, dict):
            return _err(f"joints[{i}] is not a mapping")
        missing = [k for k in ("name", "type", "parent", "child") if k not in jspec]
        if missing:
            return _err(f"joints[{i}] missing required field(s): {missing}")
        limit = jspec.get("limit") or [0.0, 0.0]
        try:
            joints.append(Joint(
                name=jspec["name"],
                type=jspec["type"],
                parent=jspec["parent"],
                child=jspec["child"],
                origin_xyz=tuple(jspec.get("origin") or jspec.get("origin_xyz") or (0, 0, 0)),  # type: ignore[arg-type]
                origin_rpy=tuple(jspec.get("rpy") or jspec.get("origin_rpy") or (0, 0, 0)),  # type: ignore[arg-type]
                axis=tuple(jspec.get("axis") or (0, 0, 1)),  # type: ignore[arg-type]
                limit_lower=float(limit[0]),
                limit_upper=float(limit[1]),
                limit_effort=float(jspec.get("effort", 10.0)),
                limit_velocity=float(jspec.get("velocity", 1.0)),
            ))
        except (ValueError, TypeError, IndexError) as e:
            return _err(f"joints[{i}] ({jspec.get('name', '?')}): invalid values: {e}")

    write_urdf(robot_name, links, joints, urdf_path)

    return {
        "success": True,
        "urdf_path": str(urdf_path.relative_to(ws)),
        "parts_dir": str(parts_dir.relative_to(ws)),
        "link_count": len(links),
        "joint_count": len(joints),
        "duration_s": duration_s,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _err(msg: str, *, duration_s: float = 0.0, exit_code: int = -1,
         stdout: str = "", stderr: str = "") -> dict[str, Any]:
    return {
        "success": False,
        "urdf_path": "",
        "parts_dir": "",
        "link_count": 0,
        "joint_count": 0,
        "duration_s": duration_s,
        "exit_code": exit_code,
        "error": msg,
        "stdout": stdout,
        "stderr": stderr,
    }
