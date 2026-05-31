"""`topos make` is the single entry: prompt (+ optional reference images) ->
workspace -> fixed articulated plan, no spec agent.

These use --no-run so no coding agent / Blender is invoked — they assert the
materialization contract the auto-run then depends on.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from topos.cli import _derive_slug, app

runner = CliRunner()


def test_derive_slug_from_prompt():
    assert _derive_slug("a palace-style three-drawer cabinet") == "palace_style_three_drawer"
    assert _derive_slug("THE of and") == "object"   # all stopwords → fallback
    assert _derive_slug("!!!") == "object"


def test_make_writes_intent_and_fixed_plan(tmp_path):
    res = runner.invoke(
        app, ["make", "a small wooden stool", "--no-run", "--base", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output

    ws = tmp_path / "small_wooden_stool"
    # prompt goes verbatim to intent.md (the design agent's input) — no spec step
    assert (ws / "prompts" / "intent.md").read_text().strip() == "a small wooden stool"

    plan = json.loads((ws / "plan.json").read_text())
    assert plan["project"] == "small_wooden_stool"
    ids = [t["id"] for t in plan["tasks"]]
    assert ids[0] == "01_agent_design"
    assert any(t["kind"] == "subgraph" for t in plan["tasks"]), "parts must expand at runtime"

    # No spec-agent leftovers
    assert not (ws / "spec.yaml").exists()

    # The emitted plan must be accepted by the loader (else auto-run would fail)
    from topos.orchestrator.plan_schema import load_plan
    assert load_plan(ws / "plan.json") is not None


def test_make_copies_reference_images_for_all_parts(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG fake-bytes")
    res = runner.invoke(
        app,
        ["make", "a cabinet like this", "-i", str(img),
         "--no-run", "--slug", "cab_ref", "--base", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output

    # all_* prefix is what the part-agent auto-discovery globs for
    copied = tmp_path / "cab_ref" / "prompts" / "references" / "all_ref.png"
    assert copied.is_file()
    assert copied.read_bytes() == b"\x89PNG fake-bytes"


def test_make_warns_on_missing_image_but_succeeds(tmp_path):
    res = runner.invoke(
        app,
        ["make", "a box", "-i", str(tmp_path / "nope.png"),
         "--no-run", "--slug", "boxy", "--base", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output
    assert "not found" in res.output
    # workspace still created, just no references dir
    assert (tmp_path / "boxy" / "plan.json").is_file()
