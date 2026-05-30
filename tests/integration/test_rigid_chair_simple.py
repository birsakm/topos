"""End-to-end smoke: build a simple wooden 4-leg chair.

This is the canonical smoke test — it exercises the full Topos pipeline on
something more semantically meaningful than a cube. Spawns 2 real ``claude``
subprocesses (agent + vision judge) and 1 Blender subprocess; ~50s typical.
Skipped if either ``claude`` or the configured Blender binary is missing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from topos.tools._blender_subprocess import resolve_blender_binary


def _claude_available() -> bool:
    return shutil.which("claude") is not None


def _blender_available() -> bool:
    try:
        binary = resolve_blender_binary()
    except RuntimeError:
        return False
    return Path(binary).is_file()


pytestmark = [
    pytest.mark.skipif(not _claude_available(), reason="claude CLI not on PATH"),
    pytest.mark.skipif(not _blender_available(), reason="blender.binary not configured"),
]


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_smoke_rigid_chair_simple(tmp_path: Path):
    base = tmp_path / "projects"
    base.mkdir()

    # 1. Init from example. Run with cwd=REPO_ROOT so the example dir is found.
    init_rc = subprocess.run(
        [
            sys.executable, "-m", "topos.cli", "init", "chair",
            "--domain", "rigid", "--from-example", "rigid_chair_simple",
            "--base", str(base),
        ],
        cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=30,
    )
    assert init_rc.returncode == 0, f"init failed: stdout={init_rc.stdout}\nstderr={init_rc.stderr}"
    ws_root = base / "chair"
    assert (ws_root / "plan.json").is_file()
    assert (ws_root / "spec.yaml").is_file()

    # 2. Run
    run_rc = subprocess.run(
        [
            sys.executable, "-m", "topos.cli", "run", "chair",
            "--base", str(base),
        ],
        cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=540,
    )
    print("RUN stdout:", run_rc.stdout)
    print("RUN stderr:", run_rc.stderr)

    # 3. Stage-0 validates *wiring*: agent wrote code → blender ran → judge
    #    produced a well-formed score. LLM-based judges have intrinsic
    #    variance, so we do NOT assert score.passed here. That belongs to
    #    Stage 1+ where iter_policy + auto-fix kick in. We only require the
    #    judge to have actually evaluated *something* (overall_score > 0).
    src_build = ws_root / "src" / "build.py"
    assert src_build.is_file(), "agent did not write src/build.py"

    renders = list((ws_root / "artifacts").glob("*.png"))
    assert renders, f"no render found in {ws_root / 'artifacts'}"

    score_path = ws_root / "trajectories" / "03_tool_judge_iter0" / "score.json"
    assert score_path.is_file(), "judge score.json missing"
    score = json.loads(score_path.read_text())
    for key in ("passed", "overall_score", "per_criterion", "suggested_fixes"):
        assert key in score, f"score.json missing field: {key}"
    assert isinstance(score["per_criterion"], dict) and score["per_criterion"], (
        "judge produced no per-criterion scores"
    )
    assert float(score["overall_score"]) > 0.0, (
        f"judge gave 0 — likely framework wiring issue, not model variance.\n"
        f"per_criterion={json.dumps(score.get('per_criterion'), indent=2)}"
    )

    # transcripts must be on disk (proof claude CLI ran)
    assert (ws_root / "trajectories" / "01_agent_geom_iter0" / "transcript.json").is_file()
    assert (ws_root / "trajectories" / "03_tool_judge_iter0" / "output.json").is_file()
