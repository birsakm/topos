"""Direct invocation of the registered ``blender_run`` tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.tools._blender_subprocess import resolve_blender_binary
from topos.tools import blender_run as _br_mod  # noqa: F401  (registers)
from topos.tools.registry import get


def _blender_available() -> bool:
    try:
        binary = resolve_blender_binary()
    except RuntimeError:
        return False
    return Path(binary).is_file()


pytestmark = pytest.mark.skipif(
    not _blender_available(), reason="blender.binary not configured"
)


def test_blender_run_tool_registered():
    spec = get("blender_run")
    assert spec.name == "blender_run"
    assert "blender" in spec.description.lower()
    assert "workspace" in spec.input_schema["properties"]


def test_blender_run_tool_executes(tmp_path: Path):
    script = tmp_path / "make_blend.py"
    out = tmp_path / "result.blend"
    script.write_text(
        "import bpy\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "bpy.ops.mesh.primitive_cube_add()\n"
        f"bpy.ops.wm.save_as_mainfile(filepath={str(out)!r})\n"
    )
    spec = get("blender_run")
    result = spec.func(
        workspace=str(tmp_path),
        script_relpath="make_blend.py",
        timeout_s=60,
    )
    assert result["success"], result
    assert "result.blend" in result["artifacts"]
    assert result["timed_out"] is False
