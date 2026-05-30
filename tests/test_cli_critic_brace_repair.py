"""Brace-balance auto-repair in cli_critic._extract_json.

Real-world failure: optimus_prime_v3 ``02_subgraph_parts__10_tool_judge_part_left_hand``
emitted a structurally-complete critique missing exactly one outer ``}``.
output_tokens=2290 (well below 64K max), stop_reason=end_turn — the LLM
thought it was done. The framework now repairs this on extract.
"""

from __future__ import annotations

import json

import pytest

from topos.agents.visual_critic.cli_critic import _extract_json, _try_brace_repair


_INNER_CRITIQUE = (
    '{"per_criterion":{"recognizable_as_role":{"score":0.7,"feedback":"ok"},'
    '"geometry_detail":{"score":0.5,"feedback":"meh"}},'
    '"overall_score":0.6,"passed":true,"suggested_fixes":["fix a","fix b"]}'
)


def _envelope(inner_payload: str) -> str:
    """Wrap a string as a claude CLI ``result`` envelope."""
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": inner_payload, "num_turns": 7,
    })


def test_well_formed_extracts_normally():
    text = _envelope(_INNER_CRITIQUE)
    out = _extract_json(text)
    assert out["per_criterion"]["recognizable_as_role"]["score"] == 0.7
    assert out["overall_score"] == 0.6


def test_repairs_missing_trailing_brace():
    """The exact left_hand failure shape — drop the final outer ``}``."""
    truncated = _INNER_CRITIQUE.rstrip("}")
    assert truncated.endswith('"]')      # array close survives
    text = _envelope(truncated)
    out = _extract_json(text)
    assert out["per_criterion"]["recognizable_as_role"]["score"] == 0.7
    assert out["suggested_fixes"] == ["fix a", "fix b"]


def test_repairs_missing_two_braces():
    """Strip two trailing closes — array end + outer end."""
    truncated = _INNER_CRITIQUE.rstrip("}").rstrip("]")
    text = _envelope(truncated)
    out = _extract_json(text)
    assert "per_criterion" in out


def test_repair_caps_at_six_appends():
    """Genuinely corrupt input (more than 6 unclosed) does NOT get repaired."""
    # 10 unclosed braces — past the cap
    junk = '{"per_criterion":{' * 10
    text = _envelope(junk)
    with pytest.raises(ValueError, match="could not extract critic JSON"):
        _extract_json(text)


def test_repair_ignores_braces_inside_strings():
    """Curly literal inside a feedback string must NOT count toward open braces."""
    s = '{"per_criterion":{"a":{"score":0.5,"feedback":"use {bevels} here"}}'
    # 4 opens, 2 closes → need 2 more to balance, well under cap
    out = _try_brace_repair(s)
    assert out is not None
    assert out["per_criterion"]["a"]["feedback"] == "use {bevels} here"


def test_repair_returns_none_when_already_valid():
    """Nothing to repair = no append, returns None (signals 'don't bother')."""
    assert _try_brace_repair(_INNER_CRITIQUE) is None


def test_repair_returns_none_for_garbage():
    """Truly invalid (mismatched brackets, not just unclosed) → no false-positive parse."""
    assert _try_brace_repair("not json at all") is None
    assert _try_brace_repair("}{") is None


def test_real_left_hand_truncation_recovers():
    """Synthetic recreation of the actual left_hand failure shape from v3.
    The model emitted everything except the outermost closing brace."""
    truncated_inner = (
        '{"per_criterion":'
        '{"recognizable_as_role":{"score":0.1,"feedback":"All four renders are uniform gray"},'
        '"geometry_detail":{"score":0.1,"feedback":"No geometric features visible"},'
        '"silhouette_correctness":{"score":0.1,"feedback":"No silhouette"},'
        '"material_quality":{"score":0.3,"feedback":"Cannot evaluate texture"},'
        '"no_obvious_errors":{"score":0.1,"feedback":"Several issues"}},'
        '"overall_score":0.12,"passed":false,'
        '"suggested_fixes":["Fix the per-part render camera framing",'
        '"Increase hand bbox","Verify the LeftHand mesh"'
        # missing closing ']}' → LLM dropped them
    )
    text = _envelope(truncated_inner)
    out = _extract_json(text)
    assert out["passed"] is False
    assert len(out["suggested_fixes"]) == 3
    assert "LeftHand" in out["suggested_fixes"][2]


def test_normalize_promotes_misnested_fields():
    """LLM occasionally puts overall_score / passed / suggested_fixes INSIDE
    per_criterion (observed on v3 left_hand). The extractor lifts them back."""
    misnested_inner = (
        '{"per_criterion":'
        '{"a":{"score":0.5,"feedback":"x"},'
        '"overall_score":0.12,'
        '"passed":false,'
        '"suggested_fixes":["fix one","fix two"]}}'
    )
    text = _envelope(misnested_inner)
    out = _extract_json(text)
    # Top-level fields lifted out
    assert out["overall_score"] == 0.12
    assert out["passed"] is False
    assert out["suggested_fixes"] == ["fix one", "fix two"]
    # Stripped from per_criterion so it only contains real criteria
    assert set(out["per_criterion"]) == {"a"}


def test_normalize_does_not_clobber_existing_top_level():
    """If both per_criterion and top-level have the field, top-level wins
    (LLM emitted correctly at top; per_criterion may have a stray duplicate)."""
    proper = (
        '{"per_criterion":{"a":{"score":0.9,"feedback":"good"}},'
        '"overall_score":0.9,"passed":true,"suggested_fixes":[]}'
    )
    text = _envelope(proper)
    out = _extract_json(text)
    assert out["overall_score"] == 0.9
    assert out["passed"] is True
    assert "overall_score" not in out["per_criterion"]


def test_real_v3_left_hand_transcript_recovers_end_to_end():
    """Smoke against the actual recorded failure: feeding the literal
    transcript.json from the v3 run through _extract_json must return a dict
    with the expected canonical shape (passed=False, overall_score≈0.12,
    suggested_fixes list)."""
    import os
    path = (
        "/lab/yipeng/topos/outputs/optimus_prime_v3/trajectories/"
        "02_subgraph_parts__10_tool_judge_part_left_hand_iter0/transcript.json"
    )
    if not os.path.exists(path):
        pytest.skip("v3 fixture not present in this checkout")
    text = open(path).read()
    out = _extract_json(text)
    assert out["passed"] is False
    assert abs(out["overall_score"] - 0.12) < 0.01
    assert isinstance(out["suggested_fixes"], list)
    assert len(out["suggested_fixes"]) > 0
    # And per_criterion has the 5 real criteria only (no overall_score etc. left inside)
    assert set(out["per_criterion"]) == {
        "recognizable_as_role", "geometry_detail",
        "silhouette_correctness", "material_quality", "no_obvious_errors",
    }
