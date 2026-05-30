"""Cover the ``extract_contract_warnings`` helper that lifts ``[*_WARN]``
lines from Blender stdout into a structured ``warnings: list[str]`` field.

Background: build.py emits ``[ATTACHMENT_WARN]`` / ``[COLLISION_WARN]`` /
``[HOLLOW_WARN]`` / ``[FIT_WARN]`` / etc. into stdout when geometry contracts
trip. The full subprocess stdout reaches the tool result dict now (no
truncation), but agents shouldn't have to grep ~100KB of Blender output to
find them — the extractor surfaces them as a clean list on the side."""

from __future__ import annotations

from topos.tools._warnings import extract_contract_warnings


def test_extract_multi_warning_in_order():
    stdout = (
        "=== bbox contract validation ===\n"
        "=== inter-part collision contract ===\n"
        "[COLLISION_WARN] LeftThigh <-> RightThigh: AABB overlap 5850.0 cm³ (10%)\n"
        "=== fixed-joint attachment contract ===\n"
        "[ATTACHMENT_WARN] pelvis_to_torso (Torso→Pelvis, fixed): min gap 21.9mm\n"
        "[ATTACHMENT_WARN] torso_to_left_smokestack: min gap 80.2mm\n"
        "INFO: gltf export starting\n"
        "INFO: Primitives created: 1\n"
    )
    warnings = extract_contract_warnings(stdout)
    assert len(warnings) == 3, f"got {len(warnings)}: {warnings!r}"
    # Order matters — agents read top-to-bottom; same order as build.py emitted
    assert warnings[0].startswith("[COLLISION_WARN] LeftThigh")
    assert warnings[1].startswith("[ATTACHMENT_WARN] pelvis_to_torso")
    assert warnings[2].startswith("[ATTACHMENT_WARN] torso_to_left_smokestack")


def test_survives_post_warning_info_spam():
    """The whole point: a warning at the START of stdout should still be
    recovered when 10KB of gltf INFO follows. Tail-truncation only loses
    it if we read the tail."""
    info_spam = "\n".join(f"INFO: Primitives created: {i}" for i in range(500))
    stdout = (
        "[HOLLOW_WARN] Frame: spec declares cavity but actual_vol >= 80% of outer\n"
        + info_spam
    )
    warnings = extract_contract_warnings(stdout)
    assert warnings == ["[HOLLOW_WARN] Frame: spec declares cavity but actual_vol >= 80% of outer"]


def test_no_warnings_returns_empty_list():
    stdout = "INFO: blender starting\nINFO: object created\n=== bbox validation OK ===\n"
    assert extract_contract_warnings(stdout) == []


def test_empty_or_none_stdout():
    assert extract_contract_warnings("") == []
    assert extract_contract_warnings(None) == []


def test_word_warn_in_prose_does_not_match():
    """A loose ``WARN`` in log prose (e.g. ``WARN: filesystem retry``) must
    not be lifted — only ``[TAG_WARN]`` shape qualifies. Otherwise every
    Blender warning would pollute the structured field."""
    stdout = (
        "INFO: WARN about to retry filesystem op\n"
        "Some prose: this WARN is just text\n"
        "[ATTACHMENT_WARN] real one: gap 5mm\n"
    )
    warnings = extract_contract_warnings(stdout)
    assert warnings == ["[ATTACHMENT_WARN] real one: gap 5mm"]


def test_leading_whitespace_stripped():
    """Blender often prefixes stdout with a timestamp or pipe. We canonicalize
    by stripping leading whitespace so the saved warning line matches what
    the contract emitted."""
    stdout = "    [FIT_WARN] Drawer: 8mm of slop in cabinet cavity\n"
    warnings = extract_contract_warnings(stdout)
    assert warnings == ["[FIT_WARN] Drawer: 8mm of slop in cabinet cavity"]
