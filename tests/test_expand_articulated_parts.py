"""Unit tests for the articulated_parts expansion strategy (ADR-0008)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from topos.orchestrator.expand import (
    _camel_to_snake,
    _find_extras_file,
    build_children,
    expansion_kinds,
    get_expander,
)
from topos.orchestrator.tasks import AgentTask, SubgraphTask, ToolTask


def _make_design(parts: list[dict]) -> dict:
    return {"robot_name": "t", "description": "t", "parts": parts}


def _ws(tmp_path: Path, *, with_prompts: dict[str, str] | None = None) -> Path:
    """Create a minimal workspace-like dir. ``with_prompts`` maps filename → body."""
    if with_prompts:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        for name, body in with_prompts.items():
            (prompts_dir / name).write_text(body, encoding="utf-8")
    return tmp_path


def _subgraph(deps: list[str] | None = None) -> SubgraphTask:
    return SubgraphTask(
        id="02_subgraph_parts",
        expand_from="src/design.json",
        expansion_kind="articulated_parts",
        deps=deps or ["01_design"],
    )


# --- helpers --------------------------------------------------------------

def test_camel_to_snake():
    assert _camel_to_snake("Frame") == "frame"
    assert _camel_to_snake("DrawerTop") == "drawer_top"
    assert _camel_to_snake("HandleMiddle") == "handle_middle"
    assert _camel_to_snake("OptimusPrimeForearm") == "optimus_prime_forearm"


def test_find_extras_file_exact(tmp_path: Path):
    ws = _ws(tmp_path, with_prompts={"extras_frame.md": "frame extras"})
    assert _find_extras_file(ws, "Frame") == "./prompts/extras_frame.md"


def test_find_extras_file_category_prefix(tmp_path: Path):
    ws = _ws(tmp_path, with_prompts={"extras_drawer.md": "drawer extras"})
    # DrawerTop should fall through to extras_drawer.md
    assert _find_extras_file(ws, "DrawerTop") == "./prompts/extras_drawer.md"
    assert _find_extras_file(ws, "DrawerMiddle") == "./prompts/extras_drawer.md"


def test_find_extras_file_none(tmp_path: Path):
    ws = _ws(tmp_path)
    assert _find_extras_file(ws, "Mystery") is None


def test_find_extras_file_strips_leading_segment(tmp_path: Path):
    """LeftArm / RightArm should fall through to extras_arm.md when
    the spec only listed the bare category."""
    ws = _ws(tmp_path, with_prompts={"extras_arm.md": "arm extras"})
    assert _find_extras_file(ws, "LeftArm") == "./prompts/extras_arm.md"
    assert _find_extras_file(ws, "RightArm") == "./prompts/extras_arm.md"


def test_find_extras_file_prefix_beats_suffix(tmp_path: Path):
    """When both prefix and suffix variants exist, prefer the prefix match
    (drawer_top → drawer is a more meaningful category for the type system
    than the trailing word)."""
    ws = _ws(tmp_path, with_prompts={
        "extras_drawer.md": "drawer extras",
        "extras_top.md": "top extras",
    })
    assert _find_extras_file(ws, "DrawerTop") == "./prompts/extras_drawer.md"


# --- registry -------------------------------------------------------------

def test_articulated_parts_registered():
    assert "articulated_parts" in expansion_kinds()


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown expansion_kind"):
        get_expander("bogus")


# --- expansion ------------------------------------------------------------

def test_expand_three_parts_emits_correct_count(tmp_path: Path):
    ws = _ws(tmp_path, with_prompts={
        "extras_frame.md": "(frame extras)",
        "extras_drawer.md": "(drawer extras)",
        "extras_handle.md": "(handle extras)",
    })
    design = _make_design([
        {"name": "Frame", "world_xyz": [0, 0, 0.15], "world_extents": [0.3, 0.3, 0.3]},
        {"name": "Drawer", "world_xyz": [0, -0.1, 0.15], "world_extents": [0.27, 0.27, 0.08]},
        {"name": "Handle", "world_xyz": [0, -0.26, 0.15], "world_extents": [0.08, 0.02, 0.08]},
    ])
    tasks = build_children(_subgraph(), workspace_root=ws, design_doc=design)

    agent_tasks = [t for t in tasks if isinstance(t, AgentTask)]
    tool_tasks = [t for t in tasks if isinstance(t, ToolTask)]

    # 3 agents + 3 textures + 1 verify + 1 render + 3 judges = 11 tool tasks + 3 agents
    assert len(agent_tasks) == 3
    assert len(tool_tasks) == 3 + 1 + 1 + 3


def test_expand_seven_parts_dynamic_drawers(tmp_path: Path):
    """User-visible behavior: spec agent listed 3 categories but design agent
    expanded to 7 instances. Expansion should produce 7 part agents."""
    ws = _ws(tmp_path, with_prompts={
        "extras_frame.md": "(frame)",
        "extras_drawer.md": "(drawer)",
        "extras_handle.md": "(handle)",
    })
    design = _make_design([
        {"name": "Frame", "world_xyz": [0, 0, 0.15], "world_extents": [0.3, 0.3, 0.3]},
        {"name": "DrawerTop", "world_xyz": [0, -0.1, 0.24], "world_extents": [0.27, 0.27, 0.08]},
        {"name": "DrawerMiddle", "world_xyz": [0, -0.1, 0.15], "world_extents": [0.27, 0.27, 0.08]},
        {"name": "DrawerBottom", "world_xyz": [0, -0.1, 0.05], "world_extents": [0.27, 0.27, 0.08]},
        {"name": "HandleTop", "world_xyz": [0, -0.26, 0.24], "world_extents": [0.08, 0.02, 0.08]},
        {"name": "HandleMiddle", "world_xyz": [0, -0.26, 0.15], "world_extents": [0.08, 0.02, 0.08]},
        {"name": "HandleBottom", "world_xyz": [0, -0.26, 0.05], "world_extents": [0.08, 0.02, 0.08]},
    ])
    tasks = build_children(_subgraph(), workspace_root=ws, design_doc=design)
    agent_tasks = [t for t in tasks if isinstance(t, AgentTask)]
    assert len(agent_tasks) == 7
    # All ids namespaced under the subgraph
    assert all(t.id.startswith("02_subgraph_parts__") for t in tasks)
    # Snake-cased local part names
    agent_ids = {t.id for t in agent_tasks}
    assert "02_subgraph_parts__01_agent_part_frame" in agent_ids
    assert "02_subgraph_parts__02_agent_part_drawer_top" in agent_ids
    assert "02_subgraph_parts__05_agent_part_handle_top" in agent_ids


def test_expand_hardware_skill_for_handle(tmp_path: Path):
    """Hardware parts (handle / pull / knob) get topos_furniture_hardware skill."""
    ws = _ws(tmp_path, with_prompts={
        "extras_frame.md": "(frame)",
        "extras_handle.md": "(handle)",
    })
    design = _make_design([
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
        {"name": "Handle", "world_xyz": [0, -0.5, 0.5], "world_extents": [0.1, 0.02, 0.1]},
    ])
    tasks = build_children(_subgraph(), workspace_root=ws, design_doc=design)
    by_id = {t.id: t for t in tasks if isinstance(t, AgentTask)}
    frame_t = by_id["02_subgraph_parts__01_agent_part_frame"]
    handle_t = by_id["02_subgraph_parts__02_agent_part_handle"]
    assert "topos_furniture_hardware" not in frame_t.skills
    assert "topos_furniture_hardware" in handle_t.skills


def test_expand_mechanical_skill_for_drivetrain(tmp_path: Path):
    """Drivetrain / running-gear / insertion parts get topos_mechanical_details;
    purely structural parts don't."""
    ws = _ws(tmp_path, with_prompts={
        "extras_frame.md": "(frame)",
        "extras_crankset.md": "(crankset)",
        "extras_seat_post.md": "(seat post)",
    })
    design = _make_design([
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
        {"name": "Crankset", "world_xyz": [0, 0, -0.2], "world_extents": [0.16, 0.2, 0.2]},
        {"name": "SeatPost", "world_xyz": [0, 0.1, 0.4], "world_extents": [0.03, 0.03, 0.2]},
    ])
    tasks = build_children(_subgraph(), workspace_root=ws, design_doc=design)
    by_id = {t.id: t for t in tasks if isinstance(t, AgentTask)}
    frame_t = by_id["02_subgraph_parts__01_agent_part_frame"]
    crank_t = by_id["02_subgraph_parts__02_agent_part_crankset"]
    post_t = by_id["02_subgraph_parts__03_agent_part_seat_post"]
    assert "topos_mechanical_details" not in frame_t.skills
    assert "topos_mechanical_details" in crank_t.skills
    assert "topos_mechanical_details" in post_t.skills  # seat_post → insertion-style


def test_expand_deps_wire_correctly(tmp_path: Path):
    """
    agent → its texture (chain)
    all agents → verify
    verify → render
    render → each judge_part
    """
    ws = _ws(tmp_path, with_prompts={"extras_frame.md": "(frame)"})
    design = _make_design([
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
        {"name": "Frame2", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ])
    sg = _subgraph(deps=["01_design"])
    tasks = build_children(sg, workspace_root=ws, design_doc=design)
    by_id = {t.id: t for t in tasks}
    sg_pfx = "02_subgraph_parts__"

    agent_frame = by_id[f"{sg_pfx}01_agent_part_frame"]
    tex_frame   = by_id[f"{sg_pfx}01_tool_texture_frame"]
    verify      = by_id[f"{sg_pfx}zz_tool_verify_parts"]
    render      = by_id[f"{sg_pfx}zz_tool_render_parts"]
    judge_frame = by_id[f"{sg_pfx}01_tool_judge_part_frame"]

    assert agent_frame.deps == ["01_design"]                # inherits subgraph deps
    assert tex_frame.deps == [agent_frame.id]
    assert sorted(verify.deps) == sorted([
        f"{sg_pfx}01_agent_part_frame",
        f"{sg_pfx}02_agent_part_frame2",
    ])
    assert render.deps == [verify.id]
    assert judge_frame.deps == [render.id]


def test_expand_uses_children_alias(tmp_path: Path):
    """Forward-compat: ``children`` should be accepted as a synonym for ``parts``."""
    ws = _ws(tmp_path)
    design = {"children": [
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ]}
    tasks = build_children(_subgraph(), workspace_root=ws, design_doc=design)
    assert any(isinstance(t, AgentTask) for t in tasks)


def test_expand_empty_parts_raises(tmp_path: Path):
    ws = _ws(tmp_path)
    with pytest.raises(ValueError, match="no 'parts' or 'children'"):
        build_children(_subgraph(), workspace_root=ws, design_doc={})


def test_build_children_reads_file_when_design_doc_omitted(tmp_path: Path):
    ws = _ws(tmp_path, with_prompts={"extras_frame.md": "(frame)"})
    (ws / "src").mkdir()
    design_path = ws / "src" / "design.json"
    design_path.write_text(json.dumps(_make_design([
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ])), encoding="utf-8")

    tasks = build_children(_subgraph(), workspace_root=ws)  # no design_doc kwarg
    assert any(isinstance(t, AgentTask) for t in tasks)


def test_expand_agent_goal_resolved_inline(tmp_path: Path):
    """AgentTask.goal must be the rendered prompt string, not a template ref —
    the runner sees the dataclass after expansion, never re-resolves templates."""
    ws = _ws(tmp_path, with_prompts={"extras_frame.md": "FRAME_EXTRAS_MARKER"})
    design = _make_design([
        {"name": "Frame", "world_xyz": [0, 0, 0], "world_extents": [1, 1, 1]},
    ])
    tasks = build_children(_subgraph(), workspace_root=ws, design_doc=design)
    agent = next(t for t in tasks if isinstance(t, AgentTask))
    assert "FRAME_EXTRAS_MARKER" in agent.goal
    assert "{{" not in agent.goal  # no unresolved jinja
