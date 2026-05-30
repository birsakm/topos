"""Generate a plan.json from a ``ProjectSpec``.

Used by ``topos make`` to bootstrap a fresh workspace's plan from a spec
agent's structured output. Keeps the plan generation logic separate from
the runner / schema so it can evolve independently.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..agents.spec import ProjectSpec


_SKILL_BY_TASK = {
    # Design agent: pure design.json author. Doesn't write Python so no bpy_docs;
    # gets texture skill so it knows the optional `texture` field exists.
    "design": ["topos_design_articulated", "topos_texture_creator"],
    # Build agent: stitches parts together, also a Python author. Gets
    # geometry_contracts (fill-ratio / inter-part collision / cavity-fit) so
    # it can emit the corresponding validation blocks. mesh_islands is a
    # related-but-experimental skill (see its STATUS section) that is not
    # wired here yet — its heuristic for picking a "main mass" baseline
    # produces false positives on parts whose main body is itself composed
    # of many small joined sub-meshes (the dominant case in practice).
    # Re-enable once a robust main-mass detector lands.
    "build": ["topos_bpy_docs", "topos_geometry_contracts"],
    # Joints agent: writes a YAML / Python joint description — small surface
    # area, can still benefit from bpy_docs (mathutils etc.).
    "joints": ["topos_joints_creator", "topos_bpy_docs"],
}


def generate_plan_articulated(spec: ProjectSpec) -> dict:
    """Build a plan.json dict for an articulated-domain project.

    Post-ADR-0008: the plan emits a single ``SubgraphTask`` (kind=``subgraph``)
    in place of the per-part fan-out. At runtime, after ``01_agent_design``
    writes ``src/design.json``, the runner reads it and dynamically spawns
    one part-agent + texture + judge_part triplet per design.json part —
    plus a single verify_parts + render_parts batch — via the
    ``articulated_parts`` strategy in ``topos/orchestrator/expand.py``.

    The build / joints / asm-tool tasks reference the subgraph id in their
    deps; the subgraph completes when all its dynamic children resolve.
    """
    if spec.domain != "articulated":
        raise NotImplementedError(f"plan generator: domain {spec.domain!r} not supported yet")

    tasks: list[dict] = []

    # 01_agent_design — reads ./prompts/intent.md, writes src/design.json
    # WebSearch + WebFetch let the designer consult reference imagery /
    # canonical descriptions for the asset class (e.g., G1 Optimus Prime
    # color codes, Victorian wardrobe proportions) before fixing the parts
    # list. The agent decides whether to invoke; furniture flows typically
    # don't, but humanoid / mecha flows benefit substantially.
    tasks.append({
        "id": "01_agent_design",
        "kind": "agent",
        "backend": "claude",
        "goal_template": "topos:articulated/designer.md.j2",
        "goal_params": {"intent_file": "./prompts/intent.md"},
        "skills": _SKILL_BY_TASK["design"],
        "allowed_tools": [
            "Read", "Edit", "Write", "Glob",
            "WebSearch", "WebFetch",
        ],
        "timeout_s": 300,
    })

    # 02_subgraph_parts — at runtime expands into per-part agent / texture /
    # judge_part triplets + 1 verify_parts + 1 render_parts batch, driven by
    # design.json's parts list. See topos/orchestrator/expand.py.
    SUBGRAPH_ID = "02_subgraph_parts"
    tasks.append({
        "id": SUBGRAPH_ID,
        "kind": "subgraph",
        "expand_from": "src/design.json",
        "expansion_kind": "articulated_parts",
        "deps": ["01_agent_design"],
    })

    # 03_agent_build — composes all parts written by the subgraph's children.
    # Depends on the subgraph itself; the runner blocks build until every
    # child resolves and reports success.
    tasks.append({
        "id": "03_agent_build",
        "kind": "agent",
        "backend": "claude",
        "deps": [SUBGRAPH_ID],
        "goal_file": "topos:articulated/builder.md",
        "skills": _SKILL_BY_TASK["build"],
        "allowed_tools": ["Read", "Edit", "Write", "Glob", "Bash"],
        "timeout_s": 300,
    })

    # 04_agent_joints — joints.yaml is derived from design.json; only needs
    # the design agent's output, runs in parallel with the parts subgraph.
    tasks.append({
        "id": "04_agent_joints",
        "kind": "agent",
        "backend": "claude",
        "deps": ["01_agent_design"],
        "goal_file": "topos:articulated/joints_writer.md",
        "skills": _SKILL_BY_TASK["joints"],
        "allowed_tools": ["Read", "Edit", "Write", "Glob", "Bash"],
        "timeout_s": 180,
    })

    # Assembly tool tasks
    tasks.extend([
        {
            "id": "05_tool_render_multiview",
            "kind": "tool",
            "tool": "render_multiview",
            "args": {
                "script_relpath": "src/build.py",
                "output_subdir": "artifacts/object_render",
                "n_views": 8,
                "resolution": 512,
                "engine": "eevee",
                "coloring": "as_authored",
                "timeout_s": 360,
            },
            "deps": ["03_agent_build"],
        },
        {
            "id": "06_tool_export_glb",
            "kind": "tool",
            "tool": "export_glb",
            "args": {
                "script_relpath": "src/build.py",
                "output_relpath": "artifacts/object.glb",
                "timeout_s": 300,
            },
            "deps": ["03_agent_build"],
        },
        {
            "id": "07_tool_export_urdf",
            "kind": "tool",
            "tool": "export_urdf",
            "args": {
                "script_relpath": "src/build.py",
                "joints_relpath": "src/joints.yaml",
                "output_urdf_relpath": "artifacts/object.urdf",
                "parts_subdir": "artifacts/parts",
                "timeout_s": 300,
            },
            "deps": ["03_agent_build", "04_agent_joints"],
        },
        {
            "id": "08_tool_judge",
            "kind": "tool",
            "tool": "judge",
            "args": {
                "rubric": "articulated_object_v1",
                "image_pattern": "artifacts/object_render/view_*.png",
            },
            "deps": ["05_tool_render_multiview"],
        },
    ])

    return {
        "project": spec.slug,
        # 3 global iters: enough budget for per-part fix + assembly fix
        "iter_policy": {"max_global_iters": 3, "stop_on": "judge_pass"},
        "tasks": tasks,
    }


def materialize_project_files(spec: ProjectSpec, workspace_root: Path) -> None:
    """Write the example-style files into a workspace from a ``ProjectSpec``:

    - ``spec.yaml`` (machine record of what was generated)
    - ``prompts/intent.md`` (fed to designer template)
    - ``prompts/extras_<lower_name>.md`` per part (fed to part_geom template)
    - ``plan.json`` (the DAG)
    """
    workspace_root = Path(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)

    prompts_dir = workspace_root / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # spec.yaml — captures what the spec agent produced
    spec_yaml_lines = [
        f"id: {spec.slug}",
        f"domain: {spec.domain}",
        f"robot_name: {spec.robot_name}",
        f"description: |",
        f"  Auto-generated by `topos make`. See prompts/intent.md for the design intent.",
    ]
    (workspace_root / "spec.yaml").write_text("\n".join(spec_yaml_lines) + "\n", encoding="utf-8")

    # intent.md
    (prompts_dir / "intent.md").write_text(spec.intent_md.rstrip() + "\n", encoding="utf-8")

    # extras_<part>.md per part
    for part in spec.parts:
        (prompts_dir / f"extras_{part.lower_name}.md").write_text(part.extras_md.rstrip() + "\n", encoding="utf-8")

    # plan.json
    plan = generate_plan_articulated(spec)
    (workspace_root / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
