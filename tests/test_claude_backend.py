"""ClaudeCLIBackend smoke test. Spawns one real ``claude -p`` call with no
tools. Minimal token usage. It is an INTEGRATION test (real LLM call, ~4s +
cost) so it's deselected from the default unit run (`addopts = -m 'not
integration'`); run it via `pytest -m integration`. Also skipped if the claude
CLI isn't on PATH.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from topos.backends.claude_cli import ClaudeCLIBackend


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude CLI not on PATH",
    ),
]


def test_claude_cli_backend_run_no_tools(tmp_path: Path):
    backend = ClaudeCLIBackend.from_config()
    trajectory = tmp_path / "trajectory"
    result = backend.run(
        prompt="Reply with exactly the single word HELLO and nothing else.",
        workspace=tmp_path,
        allowed_tools=[],
        mcp_servers=[],
        timeout_s=120,
        trajectory_dir=trajectory,
    )
    assert result.success, (
        f"claude run failed: exit_reason={result.exit_reason}\n"
        f"stdout (tail):\n{result.stdout[-2000:]}\n"
    )
    assert result.exit_reason == "completed"
    assert result.transcript_path.is_file()
    # transcript should be parseable JSON when --output-format=json succeeds
    data = json.loads(result.transcript_path.read_text())
    # We don't pin the exact schema (claude versions vary), but expect SOME
    # text containing "HELLO" somewhere in the result.
    serialized = json.dumps(data)
    assert "HELLO" in serialized.upper(), f"expected HELLO in response, got: {serialized[:500]}"
