"""cli_critic must give each call its own image-staging dir and use the
runner-supplied trajectory_dir, so N parallel judges don't collide on
shared workspace paths.

Background: Optimus Prime run 2026-05-18 exposed two symptoms (LLM reports
"Image API rejected the PNG reads", torso judge fails extract_first_json_dict)
that both traced to ``cli_critic`` racing on ``<workspace>/_critic_images/``
and ``<workspace>/.trajectory/transcript.json``. The fix plumbs ``_task_id``
and ``_trajectory_dir`` from Runner._run_tool through CriticInputs.metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from topos.agents.visual_critic.base import Criterion, CriticInputs, Rubric
from topos.agents.visual_critic.cli_critic import CliVisionCritic
from topos.backends.base import AgentRunResult, AuthMode


class _FakeBackend:
    """Captures every backend.run() call so the test can introspect both
    the staged paths and the trajectory_dir each evaluate() requested."""
    name = "fake"
    auth_mode: AuthMode = "subscription"

    def __init__(self, fake_result_json: str):
        self.fake_result_json = fake_result_json
        self.calls: list[dict[str, Any]] = []

    def run(self, *, prompt, workspace, allowed_tools, mcp_servers,
            timeout_s=None, env=None, system_prompt_append=None,
            trajectory_dir=None):
        target = trajectory_dir or (workspace / ".trajectory")
        target.mkdir(parents=True, exist_ok=True)
        transcript = target / "transcript.json"
        transcript.write_text(self.fake_result_json, encoding="utf-8")
        self.calls.append({
            "prompt": prompt,
            "workspace": workspace,
            "trajectory_dir": target,
            "transcript_path": transcript,
        })
        return AgentRunResult(
            success=True, exit_reason="ok",
            cost_usd=0.0, usage={},
            stdout="", stderr="",
            files_modified=[],
            transcript_path=transcript,
        )


_VALID_CRITIC_JSON = (
    '{"per_criterion": {"recognizable_as_role": {"score": 0.7, "feedback": "ok"}},'
    ' "suggested_fixes": []}'
)


def _rubric() -> Rubric:
    return Rubric(
        id="t", judge_backend="claude_vision",
        pass_threshold=0.5,
        criteria=[Criterion(id="recognizable_as_role", prompt="x", weight=1.0)],
    )


def _png(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return p


def test_critic_uses_per_task_image_subdir(tmp_path: Path):
    """When metadata carries _task_id, staging dir is namespaced — no
    collisions with sibling critics' image dirs."""
    ws = tmp_path / "ws"
    ws.mkdir()
    backend = _FakeBackend(_VALID_CRITIC_JSON)
    critic = CliVisionCritic(backend=backend, label="claude")
    img = _png(tmp_path, "shot.png")
    meta = {
        "workspace_path": str(ws),
        "_task_id": "02_subgraph_parts__01_tool_judge_part_head",
    }
    critic.evaluate(
        CriticInputs(images=[img], metadata=meta), _rubric(),
    )
    # The prompt should reference the namespaced subdir, not the bare default.
    prompt_text = backend.calls[0]["prompt"]
    assert "_critic_images_02_subgraph_parts__01_tool_judge_part_head/render_00.png" in prompt_text


def test_critic_uses_runner_supplied_trajectory_dir(tmp_path: Path):
    """The runner allocates trajectories/<task_id>_iter<N>/ per task and
    passes it via metadata. cli_critic must use that, not <ws>/.trajectory/."""
    ws = tmp_path / "ws"
    ws.mkdir()
    traj = tmp_path / "preallocated_traj_dir"
    backend = _FakeBackend(_VALID_CRITIC_JSON)
    critic = CliVisionCritic(backend=backend, label="claude")
    img = _png(tmp_path, "shot.png")
    meta = {
        "workspace_path": str(ws),
        "_task_id": "judge_part_torso",
        "_trajectory_dir": str(traj),
    }
    critic.evaluate(
        CriticInputs(images=[img], metadata=meta), _rubric(),
    )
    assert backend.calls[0]["trajectory_dir"].resolve() == traj.resolve()
    assert backend.calls[0]["transcript_path"].is_file()
    # Critically, NOT the legacy shared path:
    assert not (ws / ".trajectory" / "transcript.json").is_file()


def test_two_parallel_critics_dont_overlap(tmp_path: Path):
    """Two sequential evaluate() calls (simulating parallel judges) leave
    BOTH their transcripts on disk afterward — no overwrite."""
    ws = tmp_path / "ws"
    ws.mkdir()
    traj_a = tmp_path / "traj_a"
    traj_b = tmp_path / "traj_b"
    backend = _FakeBackend(_VALID_CRITIC_JSON)
    critic = CliVisionCritic(backend=backend, label="claude")
    img_a = _png(tmp_path, "a.png")
    img_b = _png(tmp_path, "b.png")

    critic.evaluate(
        CriticInputs(images=[img_a], metadata={
            "workspace_path": str(ws),
            "_task_id": "judge_a",
            "_trajectory_dir": str(traj_a),
        }), _rubric(),
    )
    critic.evaluate(
        CriticInputs(images=[img_b], metadata={
            "workspace_path": str(ws),
            "_task_id": "judge_b",
            "_trajectory_dir": str(traj_b),
        }), _rubric(),
    )
    # Both transcripts preserved — neither overwrote the other.
    assert (traj_a / "transcript.json").is_file()
    assert (traj_b / "transcript.json").is_file()


def test_critic_falls_back_to_legacy_paths_outside_runner(tmp_path: Path):
    """Backward-compat: scripts/tests calling cli_critic directly without
    metadata still work — they use the legacy fixed paths."""
    ws = tmp_path / "ws"
    ws.mkdir()
    backend = _FakeBackend(_VALID_CRITIC_JSON)
    critic = CliVisionCritic(backend=backend, label="claude")
    img = _png(tmp_path, "shot.png")
    critic.evaluate(
        CriticInputs(images=[img], metadata={"workspace_path": str(ws)}),
        _rubric(),
    )
    # No _task_id → legacy _critic_images/ subdir name
    prompt_text = backend.calls[0]["prompt"]
    assert "_critic_images/render_00.png" in prompt_text


def test_runner_injects_task_id_and_trajectory_into_metadata(tmp_path: Path):
    """Runner._run_tool must inject _task_id + _trajectory_dir into args.metadata
    for tools whose schema accepts metadata. This is the upstream half of the
    fix; without it the cli_critic side has nothing to consume."""
    import json
    from topos.orchestrator.plan_schema import Plan, load_plan
    from topos.orchestrator.runner import Runner
    from topos.orchestrator.tasks import ToolTask
    from topos.tools import registry as tool_registry
    from topos.workspace import Workspace

    # Register a stub tool that records what args it received.
    seen: dict[str, Any] = {}

    @tool_registry.tool(
        "_test_metadata_inject",
        description="test stub",
        input_schema={
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
        output_schema={"type": "object"},
        side_effects=False,
    )
    def _stub_tool(*, workspace: str, metadata: dict | None = None):
        seen["metadata"] = metadata
        return {"success": True}

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({
        "project": "t",
        "tasks": [{"id": "99_tool_metadata", "kind": "tool",
                   "tool": "_test_metadata_inject", "args": {}}],
    }))
    plan = load_plan(plan_path)
    ws = Workspace.create("p", "rigid", base=tmp_path / "outputs")
    runner = Runner(workspace=ws, plan=plan, backends={})
    task = ToolTask(id="99_tool_metadata", tool="_test_metadata_inject", args={})

    runner._run_tool(task, iteration=0)

    assert seen["metadata"] is not None
    assert seen["metadata"]["_task_id"] == "99_tool_metadata"
    expected_traj = (ws.root / "trajectories" / "99_tool_metadata_iter0").resolve()
    assert Path(seen["metadata"]["_trajectory_dir"]).resolve() == expected_traj
