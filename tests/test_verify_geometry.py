"""Unit tests for the ``verify_geometry`` tool."""

from __future__ import annotations

import json
from pathlib import Path

from topos.tools.registry import _ensure_default_tools_imported, get


def _design(parts: list[dict]) -> dict:
    return {"robot_name": "t", "parts": parts, "joints": []}


def _write_workspace(tmp_path: Path, design: dict) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "design.json").write_text(json.dumps(design))
    return tmp_path


def _run(workspace: Path) -> dict:
    _ensure_default_tools_imported()
    return get("verify_geometry").func(workspace=str(workspace))


def test_clean_handle_proud_5mm_passes(tmp_path: Path):
    """Handle correctly placed 5mm in front of drawer: should pass."""
    design = _design([
        {"name": "Drawer1", "world_xyz": [0, -0.05, 0.5], "world_extents": [0.26, 0.28, 0.09]},
        {"name": "Handle1", "role": "Brass pull",
         "world_xyz": [0, -0.2, 0.5], "world_extents": [0.07, 0.015, 0.06]},
    ])
    ws = _write_workspace(tmp_path, design)
    out = _run(ws)
    assert out["success"] is True
    assert any("Handle1_proud_over_Drawer1" in a for a in out["passed_assertions"])


def test_sunken_handle_caught_with_exact_mm(tmp_path: Path):
    """The cab_gemini_*_palace5 bug class: handle sunk 4mm into drawer face."""
    design = _design([
        # Drawer front face = -0.05 - 0.28/2 = -0.19
        {"name": "Drawer1", "world_xyz": [0, -0.05, 0.5], "world_extents": [0.26, 0.28, 0.09]},
        # Handle back = -0.19 + 0.015/2 = -0.1825, which is BEHIND drawer front (-0.19)
        # by 7.5mm — handle is sunken
        {"name": "Handle1", "role": "Brass pull",
         "world_xyz": [0, -0.19, 0.5], "world_extents": [0.07, 0.015, 0.06]},
    ])
    ws = _write_workspace(tmp_path, design)
    out = _run(ws)
    assert out["success"] is False
    handle_failures = [f for f in out["failed_parts"] if f["name"] == "Handle1"]
    assert len(handle_failures) == 1
    assert handle_failures[0]["stage"] == "design_handle_protrusion"
    assert "-7.5" in handle_failures[0]["error_msg"] or "7.50" in handle_failures[0]["error_msg"]


def test_drawer_z_overlap_caught(tmp_path: Path):
    """Two drawers overlap in Z."""
    design = _design([
        # Drawer1 Z range [0.45, 0.55]
        {"name": "Drawer1", "world_xyz": [0, -0.05, 0.5], "world_extents": [0.26, 0.28, 0.10]},
        # Drawer2 Z range [0.40, 0.50] — overlaps with Drawer1 by 5cm
        {"name": "Drawer2", "world_xyz": [0, -0.05, 0.45], "world_extents": [0.26, 0.28, 0.10]},
    ])
    ws = _write_workspace(tmp_path, design)
    out = _run(ws)
    assert out["success"] is False
    z_failures = [f for f in out["failed_parts"] if f["stage"] == "design_drawer_z_overlap"]
    assert len(z_failures) == 1


def test_missing_cavity_on_hollow_frame_caught(tmp_path: Path):
    """Frame says 'hollow' but no cavity field — exact cab_gemini bug."""
    design = _design([
        {"name": "Frame", "geometry_strategy": "hollow-case-with-dividers",
         "world_xyz": [0, 0, 0.25], "world_extents": [0.3, 0.3, 0.5],
         "wall_thickness": 0.012},
    ])
    ws = _write_workspace(tmp_path, design)
    out = _run(ws)
    assert out["success"] is False
    assert any(f["stage"] == "design_missing_cavity" for f in out["failed_parts"])


def test_solid_frame_no_cavity_required(tmp_path: Path):
    """Solid (non-hollow) frame doesn't need a cavity field."""
    design = _design([
        {"name": "Frame", "geometry_strategy": "single-cube",
         "world_xyz": [0, 0, 0.25], "world_extents": [0.3, 0.3, 0.5]},
    ])
    ws = _write_workspace(tmp_path, design)
    out = _run(ws)
    # No hardware, no drawers, no hollow frame → all green
    assert out["success"] is True


def test_handle_without_matching_drawer_is_skipped(tmp_path: Path):
    """If Handle3 exists but Drawer3 doesn't, the protrusion check is silently
    skipped (we can't verify what we can't pair). Should NOT fail the run."""
    design = _design([
        {"name": "Drawer1", "world_xyz": [0, -0.05, 0.5], "world_extents": [0.26, 0.28, 0.09]},
        {"name": "Handle3", "role": "Brass pull",  # No Drawer3 to pair with
         "world_xyz": [0, -0.2, 0.5], "world_extents": [0.07, 0.015, 0.06]},
    ])
    ws = _write_workspace(tmp_path, design)
    out = _run(ws)
    # Only check that we don't crash; Handle3 silently ignored
    assert isinstance(out["failed_parts"], list)


def test_missing_design_json_is_a_clean_failure(tmp_path: Path):
    """No design.json should produce a clean failed_parts record, not raise."""
    out = _run(tmp_path)
    assert out["success"] is False
    assert any(f["error_class"] == "FileNotFoundError" for f in out["failed_parts"])
