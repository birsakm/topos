"""PascalCase → snake_case conversion for per-part render imports.

The render_part tool feeds part names (PascalCase, e.g. ``IntakeLip``,
``FanBlade_0``) to the wrapper, which must convert them to the
snake_case file names the part agents actually wrote
(``parts/intake_lip.py``, ``parts/fan_blade_0.py``).

Plain ``str.lower()`` collapses to ``intakelip`` (no underscore), which
broke jet_engine_v2 entirely. This test pins the correct conversion."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


# The wrapper isn't importable normally — it depends on bpy which isn't in
# the test venv. Pull the helper out via importlib + module-spec exec, but
# guard against the bpy import at module load by injecting a stub first.
def _load_pascal_to_snake():
    import sys
    import types
    if "bpy" not in sys.modules:
        sys.modules["bpy"] = types.ModuleType("bpy")
        sys.modules["bpy.context"] = types.ModuleType("bpy.context")
        sys.modules["bpy.data"] = types.ModuleType("bpy.data")
        sys.modules["bpy.ops"] = types.ModuleType("bpy.ops")
    if "mathutils" not in sys.modules:
        sys.modules["mathutils"] = types.ModuleType("mathutils")
        sys.modules["mathutils"].Vector = lambda *a, **k: None  # not actually used
    path = Path(__file__).parent.parent / "topos/tools/blender_render/wrapper.py"
    spec = importlib.util.spec_from_file_location("render_wrapper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._pascal_to_snake


_pascal_to_snake = _load_pascal_to_snake()


# The function under test is a pure two-pass inflection (the same pattern
# Django/Rails ship). It has NO knowledge of jet engines, furniture, or any
# project domain — it just splits camelCase + acronym boundaries. The cases
# below are organized by the *rule* each one exercises, not by the project
# that happened to surface them.

@pytest.mark.parametrize("pascal,snake", [
    # --- Rule A: single word — lowercase passthrough, no underscore inserted
    ("Nacelle",     "nacelle"),
    ("Frame",       "frame"),
    ("Drawer",      "drawer"),
    ("Handle",      "handle"),
    # --- Rule B: standard camelCase split (lower/digit → Upper)
    ("IntakeLip",   "intake_lip"),
    ("FanHub",      "fan_hub"),
    ("TailCone",    "tail_cone"),
    ("MountPylon",  "mount_pylon"),
    ("CombustorCasing", "combustor_casing"),
    ("TurbineDisk", "turbine_disk"),
    # --- Rule C: acronym → word boundary (consecutive uppercase followed by
    #     a normal word). Without the (?<!^)(?=[A-Z][a-z]) pass, these all
    #     collapse to e.g. "lpcompressor" because no lower→upper transition
    #     exists inside the leading acronym.
    ("LPCompressor", "lp_compressor"),
    ("HPTurbine",    "hp_turbine"),
    ("XMLParser",    "xml_parser"),
    ("ABCWord",      "abc_word"),
    ("URLEncoder",   "url_encoder"),
    ("IOError",      "io_error"),
    # --- Rule D: existing underscores + trailing digits (PartFoo_0 — common
    #     when the spec agent enumerates instance copies)
    ("FanBlade_0",  "fan_blade_0"),
    ("FanBlade_15", "fan_blade_15"),
    ("Leg_3",       "leg_3"),
])
def test_pascal_to_snake(pascal: str, snake: str):
    assert _pascal_to_snake(pascal) == snake


def test_pascal_to_snake_already_snake():
    """Already-snake_case input should round-trip unchanged."""
    assert _pascal_to_snake("fan_blade_0") == "fan_blade_0"
    assert _pascal_to_snake("frame") == "frame"


def test_pascal_to_snake_no_double_underscore():
    """Existing underscores must not create double-underscores after insertion."""
    # FanBlade_0 has an underscore before the index; our regex must NOT add
    # another _ before 0 since 0 is not uppercase.
    assert "__" not in _pascal_to_snake("FanBlade_0")
    assert "__" not in _pascal_to_snake("IntakeLip_v2")
