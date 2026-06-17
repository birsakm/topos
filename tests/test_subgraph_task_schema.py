"""Unit tests for SubgraphTask schema (ADR-0008)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from topos.orchestrator.plan_schema import load_plan
from topos.orchestrator.tasks import SubgraphTask


def _write_plan(tmp_path: Path, tasks: list[dict]) -> Path:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"project": "t", "tasks": tasks}))
    return p


def test_subgraph_task_loads(tmp_path: Path):
    p = _write_plan(tmp_path, [
        {"id": "01_design", "kind": "agent", "goal": "design", "backend": "claude"},
        {
            "id": "02_subgraph_parts",
            "kind": "subgraph",
            "expand_from": "src/design.json",
            "expansion_kind": "articulated_parts",
            "backend": "gemini",
            "deps": ["01_design"],
        },
    ])
    plan = load_plan(p)
    materialised = plan.materialised()
    assert len(materialised) == 2
    sg = materialised[1]
    assert isinstance(sg, SubgraphTask)
    assert sg.id == "02_subgraph_parts"
    assert sg.expand_from == "src/design.json"
    assert sg.expansion_kind == "articulated_parts"
    assert sg.backend == "gemini"
    assert sg.deps == ["01_design"]
    assert sg.kind == "subgraph"


def test_subgraph_task_requires_expand_from(tmp_path: Path):
    p = _write_plan(tmp_path, [{
        "id": "02_subgraph_parts",
        "kind": "subgraph",
        "expansion_kind": "articulated_parts",
    }])
    with pytest.raises(Exception):  # pydantic ValidationError
        load_plan(p)


def test_subgraph_task_requires_expansion_kind(tmp_path: Path):
    p = _write_plan(tmp_path, [{
        "id": "02_subgraph_parts",
        "kind": "subgraph",
        "expand_from": "src/design.json",
    }])
    with pytest.raises(Exception):
        load_plan(p)


def test_subgraph_task_rejects_extra_fields(tmp_path: Path):
    p = _write_plan(tmp_path, [{
        "id": "02_subgraph_parts",
        "kind": "subgraph",
        "expand_from": "src/design.json",
        "expansion_kind": "articulated_parts",
        "bogus_field": "nope",
    }])
    with pytest.raises(Exception):
        load_plan(p)


def test_subgraph_task_dup_id_with_agent_rejected(tmp_path: Path):
    p = _write_plan(tmp_path, [
        {"id": "x", "kind": "agent", "goal": "foo"},
        {"id": "x", "kind": "subgraph", "expand_from": "f", "expansion_kind": "k"},
    ])
    with pytest.raises(Exception, match="duplicate"):
        load_plan(p)


def test_subgraph_task_default_timeout(tmp_path: Path):
    p = _write_plan(tmp_path, [{
        "id": "sg", "kind": "subgraph",
        "expand_from": "src/design.json",
        "expansion_kind": "articulated_parts",
    }])
    plan = load_plan(p)
    sg = plan.materialised()[0]
    assert isinstance(sg, SubgraphTask)
    assert sg.timeout_s == 60
