"""Texture is fully decoupled from geometry (image-default).

These pin the contract so a future edit can't silently re-couple them:
- part / fix agents write geometry only (no texture skill, no texture_<name>()),
- the design agent owns the look via design.json texture.prompt (no kind /
  image_relpath / procedural shader),
- build.py applies textures data-driven (_apply_texture globs the derived PNG).
"""

from __future__ import annotations

from pathlib import Path

from topos.orchestrator.expand import _PART_SKILLS, _skills_for_part

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "topos" / "prompts"
SKILLS = ROOT / "topos" / "skills"


def test_part_agents_get_no_texture_skill():
    assert "topos_texture_creator" not in _PART_SKILLS
    # no part flavour (plain / hardware / mechanical) should pull it in either
    for name in ("Frame", "Handle", "Crankset", "SeatPost"):
        assert "topos_texture_creator" not in _skills_for_part(name)


def test_part_geom_prompt_is_geometry_only():
    txt = (PROMPTS / "articulated" / "part_geom.md.j2").read_text()
    assert "GEOMETRY ONLY" in txt
    assert "topos_texture_creator" not in txt
    # the old instruction to author a sibling texture function must be gone
    assert "ALSO define a sibling" not in txt
    assert "shader-binding code" not in txt


def test_fix_part_prompts_dont_ask_for_texture_code():
    for fname in ("fix_part.md.j2", "fix_part_runtime.md.j2"):
        txt = (PROMPTS / "system" / fname).read_text()
        assert "texture_{{ part_name|lower }}(obj)" not in txt
        assert "texture_{{ lower_name }}(obj)" not in txt
        assert "topos_texture_creator" not in txt


def test_builder_template_applies_textures_data_driven():
    txt = (PROMPTS / "articulated" / "builder.md").read_text()
    assert "_apply_texture" in txt
    assert "_run_texture_pass" not in txt        # old reflective pass gone
    assert "textures" in txt and "smart_project" in txt


def test_designer_contract_drops_kind_and_image_relpath():
    txt = (PROMPTS / "articulated" / "designer.md.j2").read_text()
    assert "image_relpath" not in txt
    assert '"kind"' not in txt
    assert "texture.prompt" in txt or '"prompt"' in txt


def test_design_skill_teaches_prompt_only_image_default():
    txt = (SKILLS / "topos_design_articulated" / "SKILL.md").read_text()
    # the old kind-based decision table must be gone
    assert "kind: image" not in txt
    assert "kind: procedural" not in txt
    assert "{kind: image" not in txt
    # and the new image-default contract present
    assert "fully decoupled" in txt
    assert "texture.prompt" in txt
