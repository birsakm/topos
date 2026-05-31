"""Codex CLI backend (OpenAI).

Calibrated against codex-cli 0.128.0 (verified 2026-05-11).
Spawns ``codex exec <PROMPT>`` — the subcommand-style headless entry point.

Auth: ``codex`` reads credentials from `~/.codex/config.toml` (after
``codex login``) or from ``OPENAI_API_KEY`` env var. We require the env var
when ``auth_mode='api_key'`` and otherwise trust the CLI's session handling.

Config override surface:
    backends:
      codex:
        cli: codex
        model: gpt-5
        sandbox: workspace-write     # read-only | workspace-write | danger-full-access
        bypass_approvals: true       # --dangerously-bypass-approvals-and-sandbox
        config_overrides:            # passed via -c key=value (TOML)
          - "model_reasoning_effort=medium"
        extra_args: []

Limits known at time of writing:
- No structured JSON output flag in `codex exec`; transcript is plain text.
  cost_usd is therefore not populated (parsing token usage from text output
  is unreliable; we return 0.0 and rely on session inspection elsewhere).
- MCP support exists via `codex mcp`-managed servers in user config; this
  backend does NOT wire ad-hoc MCP server configs from the orchestrator
  call. A WARNING is logged when ``mcp_servers`` is non-empty.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config as cfg
from .._fs_diff import new_or_modified, snapshot_mtimes
from ..process import run_process
from ._retry import run_with_retries
from ._utils import assert_prompt_within_limit, classify_exit
from .base import AgentRunResult, AuthMode, McpServerConfig


@dataclass
class CodexCLIBackend:
    name: str = "codex"
    auth_mode: AuthMode = "api_key"
    cli: str = "codex"
    model: str | None = None
    sandbox: str = "workspace-write"  # read-only | workspace-write | danger-full-access
    bypass_approvals: bool = True      # framework owns the workspace; full auto
    config_overrides: tuple[str, ...] = field(default_factory=tuple)
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    default_timeout_s: int = 600
    max_quota_retries: int = 3
    quota_retry_wait_s: float = 60.0

    @classmethod
    def from_config(cls, conf: dict | None = None) -> "CodexCLIBackend":
        conf = conf or {}
        effective = cfg.load_effective_config()
        codex_conf = (effective.get("backends") or {}).get("codex") or {}
        merged = {**codex_conf, **conf}
        return cls(
            name="codex",
            auth_mode=merged.get("auth", "api_key"),
            cli=merged.get("cli", "codex"),
            model=merged.get("model") or None,
            sandbox=merged.get("sandbox", "workspace-write"),
            bypass_approvals=bool(merged.get("bypass_approvals", True)),
            config_overrides=tuple(merged.get("config_overrides") or ()),
            extra_args=tuple(merged.get("extra_args") or ()),
            default_timeout_s=int(merged.get("timeout_s") or 600),
            max_quota_retries=int(merged.get("max_quota_retries", 3)),
            quota_retry_wait_s=float(merged.get("quota_retry_wait_s", 60.0)),
        )

    def build_cmd(self, prompt: str, workspace: Path) -> list[str]:
        """Construct the actual codex exec invocation. Centralised so tests
        can verify the command shape without spawning a subprocess."""
        cmd: list[str] = [self.cli, "exec"]
        # codex uses -C/--cd for working dir (uppercase C)
        cmd.extend(["-C", str(workspace)])
        if self.model:
            cmd.extend(["-m", self.model])
        cmd.extend(["-s", self.sandbox])
        if self.bypass_approvals:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        for kv in self.config_overrides:
            cmd.extend(["-c", kv])
        cmd.extend(self.extra_args)
        # Prompt is POSITIONAL in `codex exec` — must be last so any further
        # positional args (none currently) don't get confused
        cmd.append(prompt)
        return cmd

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
    ) -> AgentRunResult:
        """One-shot invocation in ``_run_once``; the shared retry loop adds
        quota-aware retry (codex used to fail outright on a quota hit)."""
        return run_with_retries(
            lambda: self._run_once(
                prompt=prompt,
                workspace=workspace,
                allowed_tools=allowed_tools,
                mcp_servers=mcp_servers,
                timeout_s=timeout_s,
                env=env,
                system_prompt_append=system_prompt_append,
                trajectory_dir=trajectory_dir,
            ),
            retryable={"quota": self.max_quota_retries},
            base_wait_s={"quota": self.quota_retry_wait_s},
            label="codex_cli",
        )

    def _run_once(
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
    ) -> AgentRunResult:
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise NotADirectoryError(f"workspace must exist: {workspace}")
        trajectory_dir = trajectory_dir or (workspace / ".trajectory")
        trajectory_dir.mkdir(parents=True, exist_ok=True)

        if self.auth_mode == "api_key" and not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "CodexCLIBackend(auth_mode='api_key') requires OPENAI_API_KEY in env "
                "(or run `codex login` and set auth_mode='subscription')"
            )

        if mcp_servers:
            print(
                f"[CodexCLIBackend] WARNING: {len(mcp_servers)} MCP server(s) requested. "
                f"codex uses persistent MCP servers managed via `codex mcp`; ad-hoc "
                f"server injection per-call is not supported by this backend. Configure "
                f"shared MCP servers globally instead."
            )

        if system_prompt_append:
            # codex exec doesn't have an explicit append-system-prompt flag.
            # Embed it in the user prompt as a prefix block — best-effort.
            prompt = (
                "## Additional rules for this task\n\n"
                f"{system_prompt_append}\n\n"
                "## Your task\n\n"
                f"{prompt}"
            )

        assert_prompt_within_limit(prompt, self.name)
        cmd = self.build_cmd(prompt, workspace)

        before = snapshot_mtimes(workspace)
        start = time.monotonic()
        proc = run_process(
            cmd, cwd=workspace, env=env,
            timeout_s=timeout_s or self.default_timeout_s,
        )
        duration_s = time.monotonic() - start
        files_modified = new_or_modified(workspace, before)

        # codex exec produces plain text output, not JSON envelope.
        transcript_path = trajectory_dir / "transcript.txt"
        transcript_path.write_text(proc.stdout, encoding="utf-8")
        (trajectory_dir / "stderr.log").write_text(proc.stderr, encoding="utf-8")

        # codex exec is plain text — no envelope to check; fall through to
        # stdout/stderr keyword scan only.
        exit_reason = classify_exit(
            proc.returncode, proc.timed_out,
            stderr=proc.stderr,
            stdout=proc.stdout,
            have_envelope=False,
        )
        return AgentRunResult(
            success=(exit_reason == "completed"),
            files_modified=files_modified,
            stdout=proc.stdout,
            stderr=proc.stderr,
            transcript_path=transcript_path,
            exit_reason=exit_reason,
            duration_s=duration_s,
            cost_usd=0.0,    # codex exec doesn't surface cost in stdout
            usage={},
        )
