"""resolve_blender_path: exactly two supported forms.

- an absolute path is used as-is;
- anything else is project-relative, resolved against the repo root
  (e.g. ``./vendor/blender/blender``) so a vendored Blender works from any cwd.

No PATH-name lookup, no ~ expansion — those forms are intentionally unsupported.
"""

from __future__ import annotations

from pathlib import Path

from topos.tools._blender_subprocess import resolve_blender_path, _repo_root


def test_absolute_path_used_as_is(tmp_path):
    p = tmp_path / "blender"
    p.write_text("#!/bin/sh\n")
    assert resolve_blender_path(str(p)) == str(p)


def test_relative_resolved_against_repo_root():
    out = resolve_blender_path("./vendor/blender/blender")
    assert out == str((_repo_root() / "vendor" / "blender" / "blender").resolve())


def test_relative_without_dot_prefix_also_repo_root():
    out = resolve_blender_path("vendor/blender/blender")
    assert out == str((_repo_root() / "vendor" / "blender" / "blender").resolve())


def test_relative_is_repo_root_not_cwd(tmp_path, monkeypatch):
    # Resolution is anchored to the repo root, independent of the current dir.
    monkeypatch.chdir(tmp_path)
    out = resolve_blender_path("./vendor/blender/blender")
    assert out == str((_repo_root() / "vendor" / "blender" / "blender").resolve())
    assert str(tmp_path) not in out
