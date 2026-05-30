"""Unit tests for the skills/ package and AgentTask skill injection."""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.skills import list_skills, load_skill_md


def test_list_skills_includes_p0_skills():
    skills = list_skills()
    for expected in (
        "topos_part_geometry",
        "topos_joints_creator",
        "topos_design_articulated",
        "topos_geometry_contracts",
        "topos_mesh_islands",
    ):
        assert expected in skills, f"P0 skill {expected!r} missing from {skills!r}"


def test_mesh_islands_skill_complements_geometry_contracts():
    """``topos_mesh_islands`` is the within-part counterpart to
    ``topos_geometry_contracts`` (which is AABB-level inter-part). It must
    advertise a distinct FLOATING_WARN tag (so build stderr is greppable
    by part name) and ship drop-in build.py code that bmesh-walks every
    Object to group verts by edge-connectivity."""
    s = load_skill_md("topos_mesh_islands")
    # Distinct from geometry_contracts' tags — grep target for fix agents
    assert "FLOATING_WARN" in s, "mesh-islands skill must define its own warning tag"
    # Cross-references to the sibling skill so agents understand the split
    assert "topos_geometry_contracts" in s, \
        "mesh-islands SKILL.md should reference the complementary contracts skill"
    # Drop-in code shape: must walk edges to discover islands
    assert "link_edges" in s, \
        "mesh-islands check needs edge-connectivity walk to define islands"
    # Must compute world-space centroid (an island sitting at world origin
    # would otherwise look near the part if local coords are used)
    assert "matrix_world" in s
    # The tunable defaults must be documented so agents can adjust per-project.
    # The current algorithm (largest-island-bbox + margin + cluster) exposes:
    #   MARGIN_FACTOR     — bbox expansion before flagging
    #   CLUSTER_RADIUS_M  — group nearby outliers into one warning
    #   MIN_CLUSTER_VERTS — suppress noise clusters below this vert floor
    assert "MARGIN_FACTOR" in s
    assert "MIN_CLUSTER_VERTS" in s


def test_build_agent_gets_geometry_contracts():
    """plan_generator.py wires the build agent with the production-ready
    geometry-contracts skill. ``topos_mesh_islands`` is currently flagged
    experimental and NOT wired (its heuristic produces false positives on
    composite parts whose main body is itself many small joined sub-meshes
    — see SKILL.md STATUS section). When a robust main-mass detector is
    written, flip mesh_islands into the build skill list and update this
    test."""
    from topos.orchestrator.plan_generator import _SKILL_BY_TASK
    build_skills = set(_SKILL_BY_TASK["build"])
    assert "topos_geometry_contracts" in build_skills
    assert "topos_mesh_islands" not in build_skills, (
        "mesh_islands is experimental; wire it in once tuned (see SKILL.md)"
    )


def test_mesh_islands_skill_self_flags_as_experimental():
    """The skill must mark itself experimental at the top of the body so an
    agent that does Read it (e.g. via 'when_to_use' partial match) doesn't
    naively paste broken contract code into build.py."""
    s = load_skill_md("topos_mesh_islands")
    # Frontmatter must carry the warning so the soft-hint listing alone
    # (without reading the full body) already discourages copy-paste
    assert "EXPERIMENTAL" in s, \
        "frontmatter when_to_use should announce EXPERIMENTAL state"
    # Body must have a STATUS section explaining the known false-positive
    # cases — so anyone who DOES Read it sees the gotchas before the code
    assert "STATUS" in s
    assert ("false positive" in s.lower()) or ("false-positive" in s.lower())


def test_geometry_contracts_skill_has_drop_in_code():
    """The geometry-contracts skill is value-add only if its worked code is
    drop-in for build.py — agent should be able to copy each block after the
    bbox-contract loop. Verify the three named checks are present and that
    they reference the contract names the prompt template advertises."""
    s = load_skill_md("topos_geometry_contracts")
    # All three checks named
    assert "fill-ratio" in s.lower()
    assert "inter-part collision" in s.lower()
    assert "cavity-fit" in s.lower()
    # The HOLLOW_WARN / COLLISION_WARN / FIT_WARN tags are the contract surface
    # — the fix-loop and human reviewers grep for these.
    for tag in ("HOLLOW_WARN", "COLLISION_WARN", "FIT_WARN"):
        assert tag in s, f"contract tag {tag!r} missing from geometry-contracts skill"
    # The code must use bmesh (volume calc) and matrix_world (world-space AABB)
    assert "import bmesh" in s
    assert "matrix_world" in s


def test_load_skill_md_returns_frontmatter_and_content():
    s = load_skill_md("topos_part_geometry")
    # frontmatter
    assert s.startswith("---")
    assert "name: topos_part_geometry" in s
    assert "description:" in s
    # body — should mention bbox contract
    assert "bbox contract" in s.lower()
    # actionable content — should mention the transform_apply trick (a key lesson)
    assert "transform_apply" in s


def test_load_unknown_skill_raises():
    with pytest.raises(FileNotFoundError, match="topos_does_not_exist"):
        load_skill_md("topos_does_not_exist")


def test_runner_emits_soft_skill_hints_and_materializes_files():
    """v2 architecture: the runner does NOT inject SKILL.md content into the
    prompt. It emits a SHORT hint listing per skill (name + description +
    when_to_use + path to read), and copies the SKILL.md into the workspace
    so the agent can Read it on demand if relevant."""
    from topos.orchestrator.tasks import AgentTask
    from topos.workspace import Workspace
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        ws = Workspace.create("t", "rigid", base=Path(td))

        from topos.orchestrator.plan_schema import Plan
        plan = Plan(project="t", tasks=[])
        from topos.orchestrator.runner import Runner
        runner = Runner.__new__(Runner)
        runner.ws = ws
        runner.plan = plan
        runner.backends = {}

        task = AgentTask(
            id="x",
            goal="do the thing",
            skills=["topos_part_geometry"],
        )
        prompt = runner._build_agent_prompt(task)

        # Goal text present
        assert "do the thing" in prompt
        # Soft-hint section header (NOT the old hard-inject phrasing)
        assert "Skills available for this task" in prompt
        # The hint should strongly nudge the agent to Read the SKILL.md
        # when when_to_use matches, rather than blindly forcing the body
        assert "when to use" in prompt.lower() or "when_to_use" in prompt
        # Skill listed with its short metadata + path to read
        assert "topos_part_geometry" in prompt
        assert "description:" in prompt
        assert ".topos_skills/topos_part_geometry.md" in prompt
        # The FULL SKILL.md content (e.g. "bbox contract" section header) is
        # NOT in the prompt — that's the whole point of v2.
        # (The description in frontmatter happens to contain "bbox-contract pattern" — we
        #  only check that the heavyweight body content is absent.)
        assert "5-panel-join strategy" not in prompt, "v2: SKILL.md body should not be in prompt"

        # SKILL.md should be materialized at the workspace path the prompt advertised
        materialized = ws.root / ".topos_skills" / "topos_part_geometry.md"
        assert materialized.is_file(), "skill SKILL.md not copied into workspace"
        body = materialized.read_text()
        # Verify actual body content (markdown section headers + worked code),
        # NOT just frontmatter — "5-panel-join" appears as both a frontmatter
        # `provides:` bullet AND a real `### Strategy:` section, so we anchor
        # on the H3 header that can only exist in the body.
        assert "### Strategy: 5-panel-join" in body, (
            "SKILL.md body missing the 5-panel-join Strategy section header"
        )
        assert "transform_apply" in body, (
            "SKILL.md body missing the transform_apply lesson — body looks truncated"
        )
        assert len(body) > 2000, (
            f"SKILL.md body is only {len(body)} chars; should contain multiple "
            "worked strategies with code examples"
        )
