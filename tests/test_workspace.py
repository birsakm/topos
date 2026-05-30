"""Workspace lifecycle tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from topos.workspace import Workspace


def test_create_makes_canonical_layout(tmp_path: Path):
    ws = Workspace.create("hello", "rigid", base=tmp_path)
    assert ws.root == tmp_path / "hello"
    for sub in ("src", "artifacts", "trajectories"):
        assert (ws.root / sub).is_dir()
    # scratch/ deliberately NOT pre-created — see commit notes; no caller used it
    assert not (ws.root / "scratch").exists()
    manifest = json.loads(ws.manifest_path.read_text())
    assert manifest["slug"] == "hello"
    assert manifest["domain"] == "rigid"
    assert manifest["frozen"] is False


def test_locate_finds_existing(tmp_path: Path):
    Workspace.create("widget", "rigid", base=tmp_path)
    found = Workspace.locate("widget", base=tmp_path)
    assert found.root == tmp_path / "widget"


def test_locate_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        Workspace.locate("nope", base=tmp_path)


def test_create_existing_raises_without_exist_ok(tmp_path: Path):
    Workspace.create("dup", "rigid", base=tmp_path)
    with pytest.raises(FileExistsError):
        Workspace.create("dup", "rigid", base=tmp_path)


def test_create_existing_with_exist_ok(tmp_path: Path):
    ws1 = Workspace.create("dup", "rigid", base=tmp_path)
    ws2 = Workspace.create("dup", "rigid", base=tmp_path, exist_ok=True)
    assert ws1.root == ws2.root


def test_trajectory_dir_created_on_demand(tmp_path: Path):
    ws = Workspace.create("x", "rigid", base=tmp_path)
    t = ws.trajectory_dir("T1")
    assert t.is_dir()
    assert t.parent == ws.trajectories_root


@pytest.mark.parametrize("slug", ["", "Has Spaces", "UPPER", "with/slash", "x" * 65])
def test_invalid_slug_rejected(tmp_path: Path, slug: str):
    with pytest.raises(ValueError):
        Workspace.create(slug, "rigid", base=tmp_path)
