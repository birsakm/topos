"""Registry presence + schema sanity for diagnostic render tools.

The two diagnostic tools (`render_wireframe`, `render_cross_section`) are
otherwise covered by integration runs against real Blender. Here we only
check the registration surface — schemas exist, required keys present,
descriptions mention the diagnostic intent."""

from __future__ import annotations

import topos.tools.blender_render  # noqa: F401 — triggers @tool registration
from topos.tools.registry import get


def test_render_wireframe_registered():
    spec = get("render_wireframe")
    assert spec.name == "render_wireframe"
    assert "wireframe" in spec.description.lower()
    # The whole point of this tool is diagnostic / topology inspection
    assert "diagnostic" in spec.description.lower()
    # Required inputs
    props = spec.input_schema["properties"]
    for key in ("workspace", "script_relpath", "wire_thickness_frac"):
        assert key in props, f"render_wireframe missing input prop {key!r}"
    # Thickness must be a bounded number to avoid pathological values
    thickness = props["wire_thickness_frac"]
    assert thickness["type"] == "number"
    assert thickness["minimum"] > 0
    assert thickness["maximum"] <= 0.1


def test_render_cross_section_registered():
    spec = get("render_cross_section")
    assert spec.name == "render_cross_section"
    assert "cross" in spec.description.lower() or "section" in spec.description.lower()
    assert "diagnostic" in spec.description.lower()
    props = spec.input_schema["properties"]
    for key in ("workspace", "script_relpath", "section_axis", "section_frac"):
        assert key in props, f"render_cross_section missing input prop {key!r}"
    # Axis must be enumerated to prevent typos at the agent call site
    axis = props["section_axis"]
    assert set(axis["enum"]) == {"x", "y", "z"}
    # Fraction must be bounded to avoid cutting outside the object
    frac = props["section_frac"]
    assert frac["minimum"] >= 0
    assert frac["maximum"] <= 1
