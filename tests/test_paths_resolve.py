"""Unit tests for ``topos.tools._paths.resolve_under_workspace``."""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.tools._paths import resolve_under_workspace


def test_resolves_simple_relpath(tmp_path: Path):
    out = resolve_under_workspace(tmp_path, "artifacts/object.glb", label="output_relpath")
    assert out == (tmp_path / "artifacts" / "object.glb").resolve()


def test_resolves_nested_relpath(tmp_path: Path):
    out = resolve_under_workspace(tmp_path, "src/parts/handle.py", label="script_relpath")
    assert out.is_relative_to(tmp_path)
    assert out.name == "handle.py"


def test_rejects_parent_escape(tmp_path: Path):
    # The canonical attack: agent hallucinates a ".." path that lands outside ws.
    with pytest.raises(ValueError, match="output_subdir escapes workspace"):
        resolve_under_workspace(tmp_path, "../escape", label="output_subdir")


def test_rejects_absolute_escape(tmp_path: Path):
    # Absolute paths under (ws / abs) get re-rooted to abs, escaping ws.
    with pytest.raises(ValueError, match="output_relpath escapes workspace"):
        resolve_under_workspace(tmp_path, "/etc/passwd", label="output_relpath")


def test_rejects_symlink_traversal(tmp_path: Path):
    # A symlink under ws that points outside should be caught by .resolve().
    outside = tmp_path.parent / "outside_dir"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="output_subdir escapes workspace"):
        resolve_under_workspace(tmp_path, "link/file.txt", label="output_subdir")


def test_error_message_names_the_parameter(tmp_path: Path):
    # The label must appear so a misbehaving plan.json can be traced to the
    # exact arg, not just "some path escaped".
    with pytest.raises(ValueError, match="parts_subdir escapes workspace"):
        resolve_under_workspace(tmp_path, "../x", label="parts_subdir")


def test_rejects_non_string(tmp_path: Path):
    with pytest.raises(TypeError, match="output_relpath"):
        resolve_under_workspace(tmp_path, 42, label="output_relpath")  # type: ignore[arg-type]


def test_existence_not_checked(tmp_path: Path):
    # Caller's job: helper returns the path even if it doesn't exist yet
    # (outputs typically don't exist on entry).
    out = resolve_under_workspace(tmp_path, "does/not/exist/yet.png", label="output_relpath")
    assert not out.exists()
    assert out.is_relative_to(tmp_path)
