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

from topos.orchestrator.runner import _real_work_products


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
