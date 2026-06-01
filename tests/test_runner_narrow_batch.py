"""Incremental verify/render narrowing on fix iters (``Runner._narrow_batch_parts``).

On a fix iter the batch ``verify_parts`` / ``render_parts`` ToolTasks should
re-run Blender over only the re-fixed parts — every other part's
``src/parts/<name>.py`` is byte-identical to the prior iter, so its prior PNGs
and verify result stay valid. These tests pin that behavior, the load-bearing
"narrow from the full snapshot, never from a prior-narrowed list" property, and
the empty-intersection guard.
"""

from __future__ import annotations

from topos.orchestrator.runner import Runner
from topos.orchestrator.tasks import AgentTask, ToolTask

SG = "02_subgraph_parts"
FULL = ["Frame", "SeatPost", "Crankset"]


def _runner() -> Runner:
    # Bypass __init__ (no workspace/backends needed) — the method only touches
    # self._zz_full_parts, which we seed empty like the real constructor does.
    r = Runner.__new__(Runner)
    r._zz_full_parts = {}
    return r


def _zz_tasks() -> tuple[ToolTask, ToolTask]:
    verify = ToolTask(id=f"{SG}__zz_tool_verify_parts", tool="verify_parts",
                      args={"parts_dir_relpath": "src/parts", "parts": list(FULL)})
    render = ToolTask(id=f"{SG}__zz_tool_render_parts", tool="render_part",
                      args={"parts_dir_relpath": "src/parts", "parts": list(FULL)})
    return verify, render


def test_narrows_verify_and_render_to_single_refixed_part():
    """A 1-part fix narrows both batch tools to just that part; unrelated
    tasks are untouched."""
    r = _runner()
    verify, render = _zz_tasks()
    other = ToolTask(id="06_tool_export_glb", tool="export_glb", args={"parts": ["unrelated"]})
    log = r._narrow_batch_parts([verify, render, other], {"seat_post"})

    assert verify.args["parts"] == ["SeatPost"]
    assert render.args["parts"] == ["SeatPost"]
    # an export tool that isn't a zz batch tool must not be rewritten
    assert other.args["parts"] == ["unrelated"]
    assert set(dict(log)) == {verify.id, render.id}


def test_keeps_every_refixed_part_including_still_failing_ones():
    """A part re-fixed this iter (e.g. one that still failed its judge last iter
    and got another fix) must stay IN the narrowed list — narrowing is keyed on
    'was re-fixed this iter', not on frozen/passed state. Order is preserved."""
    r = _runner()
    verify, render = _zz_tasks()
    r._narrow_batch_parts([verify, render], {"seat_post", "crankset"})
    assert verify.args["parts"] == ["SeatPost", "Crankset"]   # FULL order preserved
    assert render.args["parts"] == ["SeatPost", "Crankset"]


def test_recomputes_from_full_snapshot_not_prior_narrowed_list():
    """The batch ToolTasks are the SAME objects across iters. Narrowing must
    recompute each iter from the original full list, never from the previously
    narrowed one — otherwise iter-2's re-fixed part (not re-fixed in iter 1)
    would be silently dropped and never re-rendered."""
    r = _runner()
    verify, render = _zz_tasks()

    # iter 1: only seat_post re-fixed
    r._narrow_batch_parts([verify, render], {"seat_post"})
    assert render.args["parts"] == ["SeatPost"]

    # iter 2 (same objects): only crankset re-fixed. If we narrowed the already
    # narrowed ['SeatPost'] list, the intersection would be empty and crankset
    # would never render. It must come back as ['Crankset'].
    r._narrow_batch_parts([verify, render], {"crankset"})
    assert render.args["parts"] == ["Crankset"]
    assert verify.args["parts"] == ["Crankset"]
    # the snapshot kept the complete original set
    assert r._zz_full_parts[render.id] == FULL


def test_assembly_fix_present_disables_narrowing():
    """The assembly fix agent (id 03_agent_build / 99_agent_fix, is_fix_rerun)
    is licensed to edit ANY src/parts/<name>.py but never enters refixed_parts.
    Its presence MUST disable narrowing — verify/render keep the FULL list — or
    a part the assembly agent rewrote would never be re-verified/re-rendered."""
    r = _runner()
    verify, render = _zz_tasks()
    assembly_fix = AgentTask(id="03_agent_build", goal="fix assembly",
                             backend="claude", is_fix_rerun=True)
    seatpost_fix = AgentTask(id=f"{SG}__02_agent_part_seat_post", goal="fix part",
                             backend="claude", is_fix_rerun=True)
    # seat_post WAS re-fixed, but the assembly agent is also present and may have
    # edited crankset.py — so we must not narrow to just [SeatPost].
    log = r._narrow_batch_parts([assembly_fix, seatpost_fix, verify, render], {"seat_post"})
    assert verify.args["parts"] == FULL
    assert render.args["parts"] == FULL
    assert log == []


def test_assembly_fix_restores_full_after_prior_narrowing():
    """If a prior pure-per-part iter narrowed these (persistent) tasks, an iter
    that then includes an assembly fix must RESTORE the full list, not leave the
    stale narrowing."""
    r = _runner()
    verify, render = _zz_tasks()
    # iter 1: pure per-part fix narrows to [SeatPost]
    r._narrow_batch_parts([AgentTask(id=f"{SG}__02_agent_part_seat_post", goal="x",
                                     backend="claude", is_fix_rerun=True), verify, render],
                          {"seat_post"})
    assert render.args["parts"] == ["SeatPost"]
    # iter 2: assembly fix present → restore FULL
    r._narrow_batch_parts([AgentTask(id="03_agent_build", goal="x", backend="claude",
                                     is_fix_rerun=True), verify, render], {"crankset"})
    assert verify.args["parts"] == FULL
    assert render.args["parts"] == FULL


def test_empty_intersection_restores_full_list():
    """If a later iter's refixed set matches no covered part, restore the FULL
    list (don't leave the prior iter's narrowing) and report nothing narrowed —
    the deterministic-skip path then decides whether the tool re-runs at all."""
    r = _runner()
    verify, render = _zz_tasks()
    # iter 1 snapshots FULL and narrows to seat_post
    r._narrow_batch_parts([verify, render], {"seat_post"})
    assert render.args["parts"] == ["SeatPost"]
    # iter 2: non-matching refixed → must restore FULL, not keep ['SeatPost']
    log = r._narrow_batch_parts([verify, render], {"ghost_part"})
    assert verify.args["parts"] == FULL
    assert render.args["parts"] == FULL
    assert log == []
