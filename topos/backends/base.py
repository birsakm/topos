"""Coding-agent backend protocol. New providers implement ``AgentBackend`` and
are registered for use from plan.json ``task.backend`` fields.

Standalone-output invariant (ADR 0001): backends should leave nothing inside
the workspace except the files the agent intends to keep. Temporary artifacts
go into the trajectory dir (which the orchestrator passes via ``trajectory_dir``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable


AuthMode = Literal["subscription", "api_key"]
ExitReason = Literal["completed", "timeout", "error", "quota"]


@dataclass
class McpServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def to_claude_dict(self) -> dict:
        d: dict = {"command": self.command, "args": list(self.args)}
        if self.env:
            d["env"] = dict(self.env)
        return d


@dataclass
class AgentRunResult:
    success: bool
    files_modified: list[Path]
    stdout: str                                       # FULL subprocess stdout — no truncation; consumers slice at display time if they want
    stderr: str                                       # FULL subprocess stderr — same policy
    transcript_path: Path
    exit_reason: ExitReason
    duration_s: float = 0.0
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)         # raw {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, ...}
    model_usage: dict = field(default_factory=dict)   # raw per-model breakdown from the envelope


@runtime_checkable
class AgentBackend(Protocol):
    name: str
    auth_mode: AuthMode

    def run(
        self,
        *,
        prompt: str,
        workspace: Path,
        allowed_tools: list[str],
        mcp_servers: list[McpServerConfig],
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
        system_prompt_append: str | None = None,
        trajectory_dir: Path | None = None,
    ) -> AgentRunResult: ...
