"""End-to-end test of ``run_blender``: launches a real Blender subprocess.

Skipped automatically if the configured Blender binary is missing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from topos import config as cfg
from topos.tools._blender_subprocess import run_blender, resolve_blender_binary, BlenderResult


def _blender_available() -> bool:
    try:
        binary = resolve_blender_binary()
    except RuntimeError:
        return False
    return Path(binary).is_file()


pytestmark = pytest.mark.skipif(
    not _blender_available(),
    reason="blender.binary not configured or not present; skip integration test",
)


def test_run_blender_saves_blend_file(tmp_path: Path):
    script = tmp_path / "add_cube.py"
    out_blend = tmp_path / "out.blend"
    script.write_text(
        "import bpy\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "bpy.ops.mesh.primitive_cube_add()\n"
        f"bpy.ops.wm.save_as_mainfile(filepath={str(out_blend)!r})\n"
    )

    result: BlenderResult = run_blender(script, cwd=tmp_path, timeout_s=60)

    assert result.success, f"blender failed: exit={result.exit_code}\nSTDERR:\n{result.stderr}"
    assert out_blend.is_file(), "expected out.blend to be saved"
    # at least the .blend should appear in artifacts
    assert any(p.name == "out.blend" for p in result.artifacts)


def test_run_blender_missing_script_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_blender(tmp_path / "no_such.py", cwd=tmp_path)


def test_run_blender_propagates_nonzero(tmp_path: Path):
    script = tmp_path / "boom.py"
    script.write_text("raise SystemExit(7)\n")
    result = run_blender(script, cwd=tmp_path, timeout_s=30)
    # Blender exits non-zero on a SystemExit from the script
    assert not result.success
    assert result.exit_code != 0


def test_hot_pool_not_implemented(tmp_path: Path):
    script = tmp_path / "noop.py"
    script.write_text("pass\n")
    with pytest.raises(NotImplementedError):
        run_blender(script, cwd=tmp_path, hot_pool=True)
