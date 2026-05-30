"""Verify the ``judge`` tool auto-injects ``prompts/intent.md`` as
``role_hint`` when the caller hasn't set one.

This closes the design gap that surfaced 2026-05-11: without intent
plumbing, vision critics graded a turbofan engine cutaway as a
"grinder or shaker" because the articulated rubric is identity-agnostic.
The fix lives in ``topos/tools/judge.py``; this test pins the contract.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from topos.agents.visual_critic.base import (
    Criterion,
    CriticInputs,
    CriticResult,
    Rubric,
)
from topos.tools.judge import judge


def _stub_rubric() -> Rubric:
    return Rubric(
        id="r",
        judge_backend="gemini_vision",
        pass_threshold=0.5,
        criteria=[Criterion(id="x", prompt="y", weight=1.0)],
    )


def _stub_result() -> CriticResult:
    return CriticResult(
        passed=True, overall_score=0.9,
        per_criterion={}, suggested_fixes=[],
        raw_response="", cost_usd=0.0, usage={},
    )


def _setup_ws(tmp_path: Path, *, with_intent: bool) -> Path:
    """Build a minimal workspace with one image and optionally an intent.md."""
    ws = tmp_path / "ws"
    (ws / "artifacts" / "object_render").mkdir(parents=True)
    (ws / "artifacts" / "object_render" / "view_00.png").write_bytes(b"fake")
    if with_intent:
        (ws / "prompts").mkdir()
        (ws / "prompts" / "intent.md").write_text(
            "# Modern high-bypass turbofan jet engine (cutaway)\n\n"
            "PW1100G-class. Show internals."
        )
    return ws


def test_intent_md_is_injected_as_role_hint_when_unset(tmp_path: Path):
    """Without a caller-provided role_hint, the workspace's prompts/intent.md
    must reach the critic so it knows what was supposed to be built."""
    ws = _setup_ws(tmp_path, with_intent=True)

    captured: dict = {}
    mock_critic = MagicMock()
    mock_critic.evaluate.side_effect = lambda inp, rub: (
        captured.update(metadata=inp.metadata) or _stub_result()
    )

    with patch("topos.tools.judge.load_rubric", return_value=_stub_rubric()), \
         patch("topos.tools.judge.make_critic", return_value=mock_critic):
        judge(
            workspace=str(ws),
            rubric="anything",
            image_pattern="artifacts/object_render/view_*.png",
            metadata=None,
        )

    role_hint = captured["metadata"].get("role_hint")
    assert role_hint is not None, "intent.md should have populated role_hint"
    assert "PW1100G" in role_hint
    assert "turbofan" in role_hint
    # Should also include the framing instruction so the judge knows
    # to evaluate critically rather than assume conformance.
    assert "produced object matches" in role_hint
    assert "Be critical" in role_hint


def test_caller_role_hint_is_not_overridden(tmp_path: Path):
    """Per-part judges set their own role_hint upstream; the intent.md
    autowiring must not clobber an explicit caller-provided value."""
    ws = _setup_ws(tmp_path, with_intent=True)

    captured: dict = {}
    mock_critic = MagicMock()
    mock_critic.evaluate.side_effect = lambda inp, rub: (
        captured.update(metadata=inp.metadata) or _stub_result()
    )

    explicit_hint = "This is the 'Nacelle' part — a single cowl ring."

    with patch("topos.tools.judge.load_rubric", return_value=_stub_rubric()), \
         patch("topos.tools.judge.make_critic", return_value=mock_critic):
        judge(
            workspace=str(ws),
            rubric="anything",
            image_pattern="artifacts/object_render/view_*.png",
            metadata={"role_hint": explicit_hint},
        )

    assert captured["metadata"]["role_hint"] == explicit_hint, \
        "explicit caller role_hint must take precedence over intent.md autowiring"


def test_missing_intent_md_is_silently_skipped(tmp_path: Path):
    """``topos run`` on a hand-built workspace (no prompts/intent.md) must
    still work — role_hint just stays unset and the judge runs in its
    pre-existing identity-agnostic mode."""
    ws = _setup_ws(tmp_path, with_intent=False)

    captured: dict = {}
    mock_critic = MagicMock()
    mock_critic.evaluate.side_effect = lambda inp, rub: (
        captured.update(metadata=inp.metadata) or _stub_result()
    )

    with patch("topos.tools.judge.load_rubric", return_value=_stub_rubric()), \
         patch("topos.tools.judge.make_critic", return_value=mock_critic):
        judge(
            workspace=str(ws),
            rubric="anything",
            image_pattern="artifacts/object_render/view_*.png",
            metadata=None,
        )

    assert "role_hint" not in captured["metadata"], \
        "absent intent.md must not produce a role_hint key"
