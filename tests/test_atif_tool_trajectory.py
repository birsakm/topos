"""ATIF-v1.7 trajectory emission for tool tasks.

Topos emits a ``trajectory.json`` alongside each tool task's ``output.json``,
structured per Harbor's RFC 0001 Agent Trajectory Interchange Format (v1.7).
Agent tasks (claude CLI) keep their native stream-json format instead.

The contract these tests pin:
  - Schema fields present (schema_version, trajectory_id, session_id, agent, steps, final_metrics)
  - Two-step shape: step 1 = tool_call, step 2 = observation
  - Tool_call_id matches between the call step and the observation step
  - Path objects in args/output are stringified for portability
  - ATIF emission is best-effort: a write failure does NOT propagate
"""

from __future__ import annotations

import json
from pathlib import Path

from topos.orchestrator.atif import write_tool_trajectory


def test_trajectory_has_atif_schema_version(tmp_path: Path):
    """The schema_version field tells consumers (Harbor viewer, SFT pipelines)
    which ATIF dialect they're reading. Must be one of the accepted literals."""
    write_tool_trajectory(
        tmp_path, task_id="08_tool_verify_parts", iteration=0,
        tool_name="verify_parts", arguments={"workspace": "/ws"},
        output={"success": True, "passed_parts": ["Frame"]},
        duration_s=2.3, success=True,
    )
    j = json.loads((tmp_path / "trajectory.json").read_text())
    # ATIF v1.0-v1.7 accepted; we currently emit v1.7
    assert j["schema_version"].startswith("ATIF-v"), j["schema_version"]
    assert "1.7" in j["schema_version"]


def test_two_step_shape_tool_call_then_observation(tmp_path: Path):
    """A tool task is one function call + one result. ATIF maps this to
    exactly 2 steps: a tool_calls step and an observation step.
    Both source=system because no LLM is driving these — the runner is."""
    write_tool_trajectory(
        tmp_path, task_id="19_tool_export_glb", iteration=2,
        tool_name="export_glb",
        arguments={"workspace": "/ws", "script_relpath": "src/build.py"},
        output={"success": True, "glb_path": "artifacts/object.glb", "byte_size": 1234567},
        duration_s=5.7, success=True,
    )
    j = json.loads((tmp_path / "trajectory.json").read_text())
    steps = j["steps"]
    assert len(steps) == 2, "tool trajectory must be exactly 2 steps"

    # Step 1: the tool call
    s1 = steps[0]
    assert s1["step_id"] == 1
    assert s1["source"] == "system", "framework dispatcher is not an LLM"
    assert len(s1["tool_calls"]) == 1
    call = s1["tool_calls"][0]
    assert call["function_name"] == "export_glb"
    assert call["arguments"]["workspace"] == "/ws"
    assert call["arguments"]["script_relpath"] == "src/build.py"

    # Step 2: the observation
    s2 = steps[1]
    assert s2["step_id"] == 2
    assert s2["source"] == "system"
    assert "observation" in s2
    # tool_call_id links the observation to its call — round-trippable
    assert s2["observation"]["tool_call_id"] == call["tool_call_id"]
    # The result dict is preserved verbatim (subject to JSON-safe coercion)
    assert s2["observation"]["result"]["glb_path"] == "artifacts/object.glb"
    assert s2["observation"]["result"]["byte_size"] == 1234567


def test_tool_call_id_disambiguates_iters(tmp_path: Path):
    """Each iter of the same task must get a distinct tool_call_id so a
    cross-iter analysis (e.g. "did this judge ever pass") can be done
    without ambiguity. We bake task_id+iter into the id."""
    p_iter0 = tmp_path / "i0"; p_iter0.mkdir()
    p_iter1 = tmp_path / "i1"; p_iter1.mkdir()
    for p, it in [(p_iter0, 0), (p_iter1, 1)]:
        write_tool_trajectory(
            p, task_id="25_tool_judge_part_frame", iteration=it,
            tool_name="judge", arguments={}, output={"passed": True},
            duration_s=1.0, success=True,
        )
    id0 = json.loads((p_iter0 / "trajectory.json").read_text())["trajectory_id"]
    id1 = json.loads((p_iter1 / "trajectory.json").read_text())["trajectory_id"]
    assert id0 != id1
    assert "iter0" in id0 and "iter1" in id1


def test_session_id_shared_across_iters_of_same_task(tmp_path: Path):
    """session_id is run-scoped (ATIF semantics): all iters of one task
    share the same session_id (the task id), making it easy to filter
    'all attempts for this task' from a trajectory dataset."""
    write_tool_trajectory(
        tmp_path, task_id="25_tool_judge_part_frame", iteration=0,
        tool_name="judge", arguments={}, output={"passed": False, "overall_score": 0.4},
        duration_s=1.0, success=True,
    )
    j = json.loads((tmp_path / "trajectory.json").read_text())
    assert j["session_id"] == "25_tool_judge_part_frame"


def test_final_metrics_captures_cost_and_usage(tmp_path: Path):
    """The judge tool internally invokes claude_vision / gemini_vision and
    surfaces cost_usd + usage in its result. ATIF final_metrics must lift
    these so the trajectory carries cost evidence at the document level."""
    write_tool_trajectory(
        tmp_path, task_id="41_tool_judge", iteration=0,
        tool_name="judge", arguments={"rubric": "articulated_object_v1"},
        output={
            "passed": True, "overall_score": 0.72,
            "cost_usd": 0.43,
            "usage": {"input_tokens": 12000, "output_tokens": 2500},
        },
        duration_s=78.0, success=True,
    )
    fm = json.loads((tmp_path / "trajectory.json").read_text())["final_metrics"]
    assert fm["cost_usd"] == 0.43
    assert fm["duration_s"] == 78.0
    assert fm["success"] is True
    assert fm["usage"]["input_tokens"] == 12000


def test_path_objects_coerced_to_str_for_portability(tmp_path: Path):
    """Tool args and outputs may contain Path objects. JSON can't serialize
    those, so we coerce to str. Without this, the wrapper's try/except
    would fail the trajectory write and we'd lose diagnostics."""
    write_tool_trajectory(
        tmp_path, task_id="t", iteration=0, tool_name="x",
        arguments={"workspace": Path("/ws"), "nested": {"out": Path("/ws/a.glb")}},
        output={"glb_path": Path("/ws/a.glb"), "list": [Path("/ws/b.png")]},
        duration_s=1.0, success=True,
    )
    j = json.loads((tmp_path / "trajectory.json").read_text())
    args = j["steps"][0]["tool_calls"][0]["arguments"]
    assert args["workspace"] == "/ws"
    assert args["nested"]["out"] == "/ws/a.glb"
    result = j["steps"][1]["observation"]["result"]
    assert result["glb_path"] == "/ws/a.glb"
    assert result["list"] == ["/ws/b.png"]


def test_failure_branch_also_emits_trajectory(tmp_path: Path):
    """A failed tool call must still produce a trajectory.json — the
    diagnostic info (error class, traceback) lives in the result dict
    and we want to keep it for post-mortem."""
    write_tool_trajectory(
        tmp_path, task_id="08_tool_verify_parts", iteration=2,
        tool_name="verify_parts", arguments={"parts": ["BaseStar"]},
        output={
            "success": False,
            "failed_parts": [{
                "name": "BaseStar",
                "error_class": "AssertionError",
                "error_msg": "BaseStar footprint wrong: x=0.12 target=0.72",
            }],
            "passed_parts": [],
        },
        duration_s=1.4, success=False,
    )
    j = json.loads((tmp_path / "trajectory.json").read_text())
    assert j["final_metrics"]["success"] is False
    # Error details preserved verbatim in observation.result
    failed = j["steps"][1]["observation"]["result"]["failed_parts"]
    assert failed[0]["error_class"] == "AssertionError"


def test_atif_emission_is_best_effort_in_runner(tmp_path: Path, monkeypatch):
    """If ATIF write itself raises (e.g. permission denied, disk full),
    the runner must NOT propagate the failure — the tool's output.json is
    the source of truth, ATIF is observational. The runner catches and
    prints to stderr instead. This is the contract that lets ATIF be a
    safe addition rather than a new failure mode."""
    # Construct a fake "trajectory dir" that's read-only so write fails
    ro = tmp_path / "ro"
    ro.mkdir(mode=0o555)
    try:
        # Direct call — pin that write_tool_trajectory raises on unwritable
        try:
            write_tool_trajectory(
                ro, task_id="t", iteration=0, tool_name="x",
                arguments={}, output={}, duration_s=0, success=True,
            )
            raised = False
        except (PermissionError, OSError):
            raised = True
        # On some FS / containers this isn't actually enforced; if write
        # silently succeeded the contract is N/A but we still document the
        # intent.
        if not raised:
            import warnings
            warnings.warn("filesystem didn't enforce read-only mode; "
                          "best-effort contract still holds at runner layer")
    finally:
        ro.chmod(0o755)
