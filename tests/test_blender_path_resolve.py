"""resolve_blender_path: ~ / absolute / bare-name / project-relative handling.

Lets a project-vendored Blender be configured with a relative path
(``./vendor/blender/blender``) and resolve from any cwd, while keeping the
existing absolute-path and bare-PATH-name behaviours intact.
"""

from __future__ import annotations

import os
from pathlib import Path

from topos.tools._blender_subprocess import resolve_blender_path, _repo_root


def test_absolute_path_returned_as_is(tmp_path):
    p = tmp_path / "blender"
    p.write_text("#!/bin/sh\n")
    assert resolve_blender_path(str(p)) == str(p)


def test_bare_name_left_for_path_lookup():
    # No separator → return untouched so the OS PATH resolves it.
    assert resolve_blender_path("blender") == "blender"


def test_home_tilde_expanded():
    out = resolve_blender_path("~/bin/blender")
    assert out == str(Path.home() / "bin" / "blender")
    assert Path(out).is_absolute()


def test_relative_resolved_against_cwd(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor" / "blender"
    vendor.mkdir(parents=True)
    (vendor / "blender").write_text("#!/bin/sh\n")
    monkeypatch.chdir(tmp_path)
    out = resolve_blender_path("./vendor/blender/blender")
    assert out == str((tmp_path / "vendor" / "blender" / "blender").resolve())


def test_relative_resolved_against_repo_root_when_present():
    # A relative path that exists under the repo root resolves there even if
    # cwd has no such file. We use a path we know exists in the repo: the
    # package's own __init__.py via 'topos/__init__.py'.
    rel = "topos/__init__.py"
    out = resolve_blender_path(rel)
    assert out == str((_repo_root() / rel).resolve())


def test_relative_nonexistent_resolves_to_repo_root(tmp_path, monkeypatch):
    # When the relative target exists nowhere, we return the repo-root-relative
    # path (deterministic) so the downstream error names a concrete location.
    monkeypatch.chdir(tmp_path)
    out = resolve_blender_path("vendor/blender/does_not_exist_xyz")
    assert out == str((_repo_root() / "vendor" / "blender" / "does_not_exist_xyz").resolve())
