"""When the user provided a reference image, the assembly judge passes it to
the critic as a comparison target (so it can catch structure/orientation
mismatches a text rubric alone misses — e.g. wheels facing the wrong way)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from topos.agents.visual_critic.base import Criterion, CriticInputs, CriticResult, Rubric
from topos.tools.judge import _gather_reference_images, judge


def _stub_rubric():
    return Rubric(id="t", judge_backend="claude_vision", pass_threshold=0.6,
                  criteria=[Criterion(id="a", prompt="p", weight=1.0)])


def _stub_result():
    return CriticResult(passed=True, overall_score=0.9, per_criterion={"a": 0.9},
                        suggested_fixes=[])


def _ws(tmp_path: Path, *, with_reference: bool) -> Path:
    ws = tmp_path / "ws"
    (ws / "artifacts" / "object_render").mkdir(parents=True)
    (ws / "artifacts" / "object_render" / "view_00.png").write_bytes(b"render")
    if with_reference:
        (ws / "prompts" / "references").mkdir(parents=True)
        (ws / "prompts" / "references" / "all_reference.png").write_bytes(b"refimg")
    return ws


def test_gather_finds_all_prefixed_references(tmp_path: Path):
    ws = _ws(tmp_path, with_reference=True)
    refs = _gather_reference_images(ws)
    assert [p.name for p in refs] == ["all_reference.png"]


def test_compare_to_reference_passes_reference_to_critic(tmp_path: Path):
    ws = _ws(tmp_path, with_reference=True)
    captured: dict = {}
    mock_critic = MagicMock()
    mock_critic.evaluate.side_effect = lambda inp, rub: (
        captured.update(refs=inp.reference_images) or _stub_result()
    )
    with patch("topos.tools.judge.load_rubric", return_value=_stub_rubric()), \
         patch("topos.tools.judge.make_critic", return_value=mock_critic):
        judge(workspace=str(ws), rubric="x",
              image_pattern="artifacts/object_render/view_*.png",
              compare_to_reference=True)
    assert [p.name for p in captured["refs"]] == ["all_reference.png"]


def test_no_compare_flag_means_no_reference(tmp_path: Path):
    ws = _ws(tmp_path, with_reference=True)
    captured: dict = {}
    mock_critic = MagicMock()
    mock_critic.evaluate.side_effect = lambda inp, rub: (
        captured.update(refs=inp.reference_images) or _stub_result()
    )
    with patch("topos.tools.judge.load_rubric", return_value=_stub_rubric()), \
         patch("topos.tools.judge.make_critic", return_value=mock_critic):
        judge(workspace=str(ws), rubric="x",
              image_pattern="artifacts/object_render/view_*.png",
              compare_to_reference=False)
    assert captured["refs"] == []  # opt-in only
