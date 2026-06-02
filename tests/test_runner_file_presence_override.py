"""Unit tests for runner's file-presence success override.

Pinned bug: cab_gemini_pro_palace5_v2 (2026-05-13) — gemini-3-flash emitted
an empty final-turn response after writing a complete 4 KB part .py file
via write_file. CLI labeled the run "Invalid stream" → topos's classify_exit
saw both that and a stale 429 in stderr → exit_reason="quota" → success=False
→ build agent depended on this part → cascade-skipped 15+ downstream tasks
→ artifacts/ ended up empty even though all work was on disk.

The override in `_run_agent`: when CLI reports failure but `files_modified`
contains real src/ output, trust the disk over the envelope. Downstream
verify_parts/judges still validate the work product itself.
"""

from __future__ import annotations

from pathlib import Path

from topos.backends.base import AgentRunResult
from topos.orchestrator.plan_schema import Plan
from topos.orchestrator.runner import _missing_expected_outputs, _real_work_products, Runner
from topos.orchestrator.tasks import AgentTask
from topos.workspace import Workspace


def _touch(p: Path, content: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_real_src_files_count_as_work(tmp_path: Path):
    real = _touch(tmp_path / "src" / "parts" / "frame.py",
                  "import bpy\ndef build_frame(): return None\n")
    out = _real_work_products([real], tmp_path)
    assert out == [real]


def test_skill_cache_does_not_count(tmp_path: Path):
    """gemini-cli often re-saves the .topos_skills/ files it Reads; those
    show up in files_modified but aren't 'work product'."""
    skill = _touch(tmp_path / ".topos_skills" / "topos_part_geometry.md", "# skill")
    src = _touch(tmp_path / "src" / "parts" / "x.py", "import bpy\n")
    out = _real_work_products([skill, src], tmp_path)
    assert out == [src]


def test_artifacts_dir_does_not_count(tmp_path: Path):
    """Tool tasks (export_glb/render) write to artifacts/. An agent
    sometimes peeks at those — modifications shouldn't trigger override."""
    art = _touch(tmp_path / "artifacts" / "object.glb", "\x00\x00")
    src = _touch(tmp_path / "src" / "build.py", "from parts.frame import build_frame\n")
    out = _real_work_products([art, src], tmp_path)
    assert out == [src]


def test_empty_file_does_not_count(tmp_path: Path):
    """A zero-byte file is not a meaningful work product. Common if model
    started a write_file then crashed — the CLI may still leave an empty
    placeholder. (Rare in practice; tool-call atomicity usually prevents it.)"""
    empty = _touch(tmp_path / "src" / "parts" / "x.py", "")
    out = _real_work_products([empty], tmp_path)
    assert out == []


def test_outside_workspace_does_not_count(tmp_path: Path):
    """Defensive: if an agent somehow writes outside the workspace,
    that file doesn't count (paths under src/ only)."""
    rogue = _touch(tmp_path.parent / "outside.py", "x")
    out = _real_work_products([rogue], tmp_path)
    assert out == []


def test_only_design_or_joints_or_build_count(tmp_path: Path):
    """Design/build/joints agents write specific top-level src/ files —
    each one alone should be enough to override."""
    for path in ("src/design.json", "src/build.py", "src/joints.yaml"):
        p = _touch(tmp_path / path, "{}\n")
        assert _real_work_products([p], tmp_path) == [p], f"{path} should count"


def test_nonexistent_file_skipped(tmp_path: Path):
    """files_modified is computed from mtime snapshots; if a path was
    written then removed within the same agent run, .is_file() is False
    and we skip rather than crash."""
    ghost = tmp_path / "src" / "parts" / "removed.py"
    out = _real_work_products([ghost], tmp_path)
    assert out == []


def test_textures_count_as_work(tmp_path: Path):
    """topos_texture_creator stages PNGs under src/textures/ — those are
    legitimate agent output (image-gen tool wrote them on agent's behalf)."""
    tex = _touch(tmp_path / "src" / "textures" / "frame.png", "PNG\x89")
    out = _real_work_products([tex], tmp_path)
    assert out == [tex]


# ---------------- no-op guard: expected_outputs validation ----------------
#
# The complement of the file-presence override. gemini-cli intermittently
# ends a turn reporting success while doing nothing (no Write tool call). A
# no-op design agent that "succeeds" without writing src/design.json used to
# crash the run at subgraph expansion. expected_outputs makes the runner
# retry-once then fail loud.

def test_missing_expected_outputs_when_absent(tmp_path: Path):
    assert _missing_expected_outputs(["src/design.json"], tmp_path) == ["src/design.json"]


def test_missing_expected_outputs_when_present(tmp_path: Path):
    _touch(tmp_path / "src" / "design.json", "{}\n")
    assert _missing_expected_outputs(["src/design.json"], tmp_path) == []


def test_missing_expected_outputs_empty_file_counts_as_missing(tmp_path: Path):
    """A zero-byte write is not a result — a truncated/empty design.json
    would still crash expansion downstream."""
    _touch(tmp_path / "src" / "design.json", "")
    assert _missing_expected_outputs(["src/design.json"], tmp_path) == ["src/design.json"]


def test_no_expected_outputs_never_missing(tmp_path: Path):
    assert _missing_expected_outputs([], tmp_path) == []


class _NoOpBackend:
    """Reports success but writes nothing unless told to on a given attempt —
    models gemini-cli's intermittent no-op turn."""

    def __init__(self, write_on_attempt: int | None = None):
        self.calls = 0
        self.write_on_attempt = write_on_attempt

    def run(self, *, prompt, workspace, allowed_tools, mcp_servers, timeout_s,
            trajectory_dir, system_prompt_append):
        self.calls += 1
        if self.write_on_attempt is not None and self.calls >= self.write_on_attempt:
            (workspace / "src").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "design.json").write_text("{}\n")
        return AgentRunResult(
            success=True, files_modified=[], stdout="", stderr="",
            transcript_path=trajectory_dir / "transcript.json",
            exit_reason="completed",
        )


def _runner_with_backend(tmp_path: Path, backend) -> Runner:
    ws = Workspace.create("p", "articulated", base=tmp_path)
    r = Runner.__new__(Runner)
    r.ws = ws
    r.plan = Plan(project="p", tasks=[])
    r.backends = {"claude": backend}
    r.resume = False
    r._cost_accumulator = 0.0
    return r


def test_noop_agent_failed_after_one_retry(tmp_path: Path):
    """Envelope says success but the declared output never appears → retry
    exactly once, then mark the task failed (loud) rather than letting a
    silent no-op propagate and crash expansion."""
    be = _NoOpBackend(write_on_attempt=None)  # never writes
    runner = _runner_with_backend(tmp_path, be)
    task = AgentTask(id="01_agent_design", goal="design it",
                     expected_outputs=["src/design.json"])
    res = runner._run_agent(task, iteration=0)
    assert be.calls == 2, "should retry exactly once on a no-op turn"
    assert res.success is False
    assert "no-op" in (res.note or "").lower()


def test_noop_guard_recovers_when_retry_writes(tmp_path: Path):
    """The retry is the whole point: if the second attempt writes the file,
    the task succeeds and the run continues — a transient no-op shouldn't
    fail the run outright."""
    be = _NoOpBackend(write_on_attempt=2)
    runner = _runner_with_backend(tmp_path, be)
    task = AgentTask(id="01_agent_design", goal="design it",
                     expected_outputs=["src/design.json"])
    res = runner._run_agent(task, iteration=0)
    assert be.calls == 2
    assert res.success is True


def test_no_expected_outputs_means_no_retry(tmp_path: Path):
    """Tasks that don't declare expected_outputs keep the prior single-call
    behavior — envelope success is honored, no extra attempt."""
    be = _NoOpBackend(write_on_attempt=None)
    runner = _runner_with_backend(tmp_path, be)
    task = AgentTask(id="x_agent", goal="do something")
    res = runner._run_agent(task, iteration=0)
    assert be.calls == 1
    assert res.success is True
