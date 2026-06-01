"""Gemini CLI backend (Google).

Calibrated against gemini-cli 0.41.2 (verified 2026-05-11).
Spawns ``gemini -p <PROMPT> -y -o stream-json`` — flag-based headless invocation.

Auth: ``gemini`` reads credentials from its own user config (after auth
flow) or from ``GOOGLE_API_KEY`` / ``GEMINI_API_KEY`` env vars. We accept
either env name when ``auth_mode='api_key'``.

Config override surface:
    backends:
      gemini:
        cli: gemini
        model: gemini-3-pro          # pick a recent flagship per gemini --list-models
        approval_mode: yolo          # default | auto_edit | yolo | plan
        sandbox: false
        include_directories: []      # comma-separated extras
        extra_args: []

Notes on the surface:
- gemini-cli uses ``--approval-mode yolo`` (or ``-y``) to auto-approve all
  tool calls — equivalent to claude's bypassPermissions.
- ``--allowed-tools`` is marked deprecated in 0.41.x in favor of Policy Engine.
  We don't pass it; if you need per-task tool restriction, use the policy
  engine via ``--policy <file>`` instead.
- gemini-cli has no CWD flag — process cwd is used. We chdir to workspace
  via the subprocess ``cwd=`` param.
- Output format is fixed to ``stream-json``. Each agent task captures
  per-turn JSONL events into ``transcript.jsonl`` and the trailing
  ``type: result`` event into ``transcript.json`` (downstream readers —
  e.g. cli_critic — read the latter). The legacy ``-o json`` / ``-o text``
  envelope shapes were dropped because (a) gemini never returns USD cost in
  any shape — pricing is computed from the result event's stats — and
  (b) only ``stream-json`` exposes the per-event tokens needed to do that.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config as cfg
from .._fs_diff import new_or_modified, snapshot_mtimes
from ..process import run_process_with_watchdog
from .dialect import GEMINI_STREAM
from ._utils import assert_prompt_within_limit, classify_exit
from .base import AgentRunResult, AuthMode, McpServerConfig


def _envelope_is_error(envelope: dict | None) -> bool:
    """True if gemini-cli's stream-json result event flags an error.
    gemini-cli may exit 0 on internal errors, so the ``status: error``
    field of the trailing result event is the only reliable signal.
    ``error`` / ``is_error`` are also accepted for forward-compat with
    future event shapes.
    """
    if not isinstance(envelope, dict):
        return False
    if envelope.get("error") or envelope.get("is_error"):
        return True
    status = (envelope.get("status") or "").lower()
    return status in ("error", "failed")


# Substrings that flag a TRANSIENT model-output glitch — the kind where
# gemini-3.x-preview emits malformed/empty stream once but a fresh call
# usually succeeds. Distinct from 503 (Google capacity, handled by
# gemini-cli's own retry) and from quota (429, no point retrying). Observed
# 2026-05-13 on cab_gemini_pro_palace5_v2 — handle2 agent died at 12s with
# "Invalid stream: empty response or malformed tool call", cascading-skipped
# 15+ downstream tasks. Worth retrying once or twice before giving up.
_TRANSIENT_GEMINI_ERROR_PATTERNS: tuple[str, ...] = (
    "Invalid stream",
    "empty response",
    "malformed tool call",
    "no candidates",
    "RECITATION",            # Google's content filter occasionally false-positives
    "INTERNAL",              # generic INTERNAL_ERROR
)


def _extract_error_events(stdout: str) -> list[dict]:
    """Walk gemini-cli's stream-json buffer for ``type: error`` events.

    The CLI emits these alongside the final ``type: result`` event when the
    model's stream is malformed/empty. The result event only carries
    ``status: error`` with no message — the actual diagnostic is in the
    error event. Returns all error events in order, empty list if none.
    """
    if not stdout or not stdout.strip():
        return []
    out: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "error":
            out.append(ev)
    return out


def _is_transient_gemini_error(error_events: list[dict]) -> str | None:
    """If any ``type: error`` event matches a known transient pattern,
    return the diagnostic message (used as the retry-log line). None
    means "either no error events or none match — caller should NOT retry".
    """
    for ev in error_events:
        msg = (ev.get("message") or "").lower()
        for pat in _TRANSIENT_GEMINI_ERROR_PATTERNS:
            if pat.lower() in msg:
                return ev.get("message") or pat
    return None


def _parse_stream_json_final_result(stdout: str) -> dict | None:
    """Walk gemini-cli's stream-json buffer (one JSON event per line) and
    return the final ``type: result`` event.

    Event shape examples emitted by gemini CLI:
      {"type":"init",    "timestamp":..., "session_id":..., "model":"auto-gemini-3"}
      {"type":"message", "timestamp":..., "role":"user",      "content":"..."}
      {"type":"message", "timestamp":..., "role":"assistant", "content":"...", "delta":true}
      {"type":"result",  "timestamp":..., "status":"success", "stats":{...}}

    Same parser pattern as ``backends.claude_cli._parse_stream_json_final_result``
    — split on newlines, skip blanks and malformed JSON, return the LAST
    ``type: result`` line. Returns None when no result event is recoverable
    (early CLI crash before result emission).
    """
    if not stdout or not stdout.strip():
        return None
    last_result: dict | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            # Skip blank lines and non-JSON noise (e.g. npm notice trailers,
            # YOLO mode banners — gemini interleaves those before the stream).
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            last_result = ev
    return last_result


@dataclass
class GeminiCLIBackend:
    name: str = "gemini"
    auth_mode: AuthMode = "api_key"
    cli: str = "gemini"
    model: str | None = None
    approval_mode: str = "yolo"        # default | auto_edit | yolo | plan
    sandbox: bool = False
    include_directories: tuple[str, ...] = field(default_factory=tuple)
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    default_timeout_s: int = 600
    # Watchdog config — mirrors claude_cli's. ``timeout_s`` is the SOFT
    # deadline (expected completion). Past it, kill only if neither
    # activity signal moved for ``watchdog_idle_grace_s`` seconds.
    # ``watchdog_hard_max_s`` is the absolute ceiling regardless of
    # activity. Generous on idle_grace because Google AI Studio's
    # gemini-3.1-pro preview occasionally hits 503 "high demand" and
    # gemini-cli retries internally; we want to let those retries play
    # out instead of killing a healthy-but-throttled run.
    watchdog_idle_grace_s: int = 300
    watchdog_hard_max_s: int = 3600
    # Transient-model-glitch retry — for gemini-3.x preview models that
    # occasionally emit "Invalid stream / empty response / malformed tool
    # call" on a single turn. Separate from gemini-cli's own 503 retry
    # (which handles Google capacity exhaustion). Default 2 retries =
    # 3 total attempts; 5s base wait keeps the retry pipeline fast since
    # these glitches usually resolve on the next call.
    max_transient_retries: int = 2
    transient_retry_wait_s: float = 5.0

    @classmethod
    def from_config(cls, conf: dict | None = None) -> "GeminiCLIBackend":
        conf = conf or {}
        effective = cfg.load_effective_config()
        gem_conf = (effective.get("backends") or {}).get("gemini") or {}
        merged = {**gem_conf, **conf}
        return cls(
            name="gemini",
            auth_mode=merged.get("auth", "api_key"),
            cli=merged.get("cli", "gemini"),
            model=merged.get("model") or None,
            approval_mode=merged.get("approval_mode", "yolo"),
            sandbox=bool(merged.get("sandbox", False)),
            include_directories=tuple(merged.get("include_directories") or ()),
            extra_args=tuple(merged.get("extra_args") or ()),
            default_timeout_s=int(merged.get("timeout_s") or 600),
            watchdog_idle_grace_s=int(merged.get("watchdog_idle_grace_s") or 300),
            watchdog_hard_max_s=int(merged.get("watchdog_hard_max_s") or 3600),
            max_transient_retries=int(merged.get("max_transient_retries", 2)),
            transient_retry_wait_s=float(merged.get("transient_retry_wait_s", 5.0)),
        )

    def build_cmd(self, prompt: str) -> list[str]:
        """Construct the actual gemini invocation. Note: no --cd flag; the
        subprocess runs with cwd=workspace (set by run())."""
        cmd: list[str] = [self.cli]
        cmd.extend(["-p", prompt])
        if self.model:
            cmd.extend(["-m", self.model])
        if self.approval_mode:
            cmd.extend(["--approval-mode", self.approval_mode])
        # Output format is fixed: stream-json is the only shape that carries
        # the per-event token counts gemini_cost_usd needs (gemini never
        # returns USD natively). See module docstring.
        cmd.extend(["-o", "stream-json"])
        if self.sandbox:
            cmd.append("-s")
        # We manage workspace trust externally — `--skip-trust` prevents the CLI
        # downgrading approval-mode to "default" on un-trusted dirs (like /tmp).
        cmd.append("--skip-trust")
        if self.include_directories:
            cmd.extend(["--include-directories", ",".join(self.include_directories)])
        cmd.extend(self.extra_args)
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
        """Run with transient-model-glitch retry.

        The actual subprocess invocation is in ``_run_once``. This wrapper
        catches gemini-cli's ``type: error`` stream-json events with known
        transient patterns (Invalid stream / empty response / malformed
        tool call / no candidates / RECITATION) and retries up to
        ``max_transient_retries`` times. Quota / 503 / hard timeouts are
        already handled at lower layers (gemini-cli internal retry,
        watchdog idle-grace, watchdog hard-max) — don't double-retry those."""
        last_result: AgentRunResult | None = None
        for attempt in range(self.max_transient_retries + 1):
            last_result = self._run_once(
                prompt=prompt,
                workspace=workspace,
                allowed_tools=allowed_tools,
                mcp_servers=mcp_servers,
                timeout_s=timeout_s,
                env=env,
                system_prompt_append=system_prompt_append,
                trajectory_dir=trajectory_dir,
            )
            if last_result.success:
                return last_result
            # Scan stdout (preserved on the result) for type:error events to
            # see whether this failure is the kind that benefits from a retry.
            error_events = _extract_error_events(last_result.stdout)
            transient_msg = _is_transient_gemini_error(error_events)
            if transient_msg is None or attempt == self.max_transient_retries:
                return last_result
            print(
                f"[gemini_cli] transient model glitch (attempt {attempt + 1}/"
                f"{self.max_transient_retries + 1}): {transient_msg[:140]} — "
                f"sleeping {self.transient_retry_wait_s:.0f}s before retry."
            )
            time.sleep(self.transient_retry_wait_s)
        return last_result  # type: ignore[return-value]

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

        # Auth: prefer env var, fall back to lifting the key from Topos config
        # (image_gen.gemini.api_key, which the user has already set via
        # `topos config set` for Nano Banana 2 image-gen).
        env = dict(env) if env else dict(os.environ)
        if self.auth_mode == "api_key":
            have_env = bool(env.get("GOOGLE_API_KEY") or env.get("GEMINI_API_KEY"))
            if not have_env:
                cfg_key = (cfg.load_effective_config()
                           .get("image_gen", {}).get("gemini", {}).get("api_key"))
                if cfg_key:
                    env["GEMINI_API_KEY"] = cfg_key
                else:
                    raise RuntimeError(
                        "GeminiCLIBackend(auth_mode='api_key') needs an API key. "
                        "Either set GEMINI_API_KEY / GOOGLE_API_KEY in env, or "
                        "configure via `topos config set image_gen.gemini.api_key <key>`."
                    )

        if mcp_servers:
            print(
                f"[GeminiCLIBackend] WARNING: {len(mcp_servers)} MCP server(s) requested. "
                f"gemini-cli manages MCP via persistent `gemini mcp` config; ad-hoc "
                f"per-call server injection is not supported by this backend."
            )

        if system_prompt_append:
            # gemini-cli doesn't have an explicit append-system-prompt flag.
            # Embed in user prompt as a prefix block.
            prompt = (
                "## Additional rules for this task\n\n"
                f"{system_prompt_append}\n\n"
                "## Your task\n\n"
                f"{prompt}"
            )

        assert_prompt_within_limit(prompt, self.name)
        cmd = self.build_cmd(prompt)

        before = snapshot_mtimes(workspace)
        start = time.monotonic()
        # Use the same activity-aware watchdog as claude_cli — soft_timeout
        # for "expected completion", idle_grace for "long-but-progressing",
        # hard_max as absolute ceiling. stream-json events on stdout are the
        # primary activity signal; gemini-cli's stderr-side
        # ``Attempt N failed with status 503`` retry markers also count, so
        # Google AI Studio overload bursts don't trip a false-timeout.
        proc = run_process_with_watchdog(
            cmd,
            cwd=workspace,
            env=env,
            soft_timeout_s=timeout_s or self.default_timeout_s,
            idle_grace_s=self.watchdog_idle_grace_s,
            hard_max_s=self.watchdog_hard_max_s,
            **GEMINI_STREAM.watchdog_kwargs(),
        )
        duration_s = time.monotonic() - start
        files_modified = new_or_modified(workspace, before)

        # stream-json is the only shape we emit. Save the raw JSONL and
        # extract the trailing ``type: result`` event for downstream
        # readers (cli_critic, spec, _envelope_is_error). On early crash
        # the result event is missing; preserve the raw stream so
        # postmortem still has something.
        parsed: dict | None = None
        (trajectory_dir / "transcript.jsonl").write_text(proc.stdout, encoding="utf-8")
        transcript_path = trajectory_dir / "transcript.json"
        if proc.stdout.strip():
            parsed = _parse_stream_json_final_result(proc.stdout)
        if parsed is not None:
            transcript_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        else:
            transcript_path.write_text(proc.stdout, encoding="utf-8")
        # Lift stream-json ``type: error`` events into stderr so the cause
        # of a generic ``exit_reason=error`` is visible without digging into
        # transcript.jsonl. result envelope only carries ``status: error``
        # with no message — the diagnostic is on the error event.
        error_events = _extract_error_events(proc.stdout)
        annotated_stderr = proc.stderr
        if error_events:
            block = "\n=== gemini_cli: stream-json type:error events ===\n"
            for ev in error_events:
                ts = ev.get("timestamp", "")
                msg = ev.get("message", "")
                sev = ev.get("severity", "error")
                block += f"[{ts}] {sev}: {msg}\n"
            annotated_stderr = annotated_stderr.rstrip() + "\n" + block
        (trajectory_dir / "stderr.log").write_text(annotated_stderr, encoding="utf-8")

        # Gemini never returns a USD cost field — compute it from the
        # result event's stats × the per-model price table.
        cost_usd = 0.0
        usage_dict: dict = {}
        stats = parsed.get("stats") if isinstance(parsed, dict) else None
        if isinstance(stats, dict):
            usage_dict = {
                "input_tokens": stats.get("input_tokens"),
                "output_tokens": stats.get("output_tokens"),
                "total_tokens": stats.get("total_tokens"),
                "cached_input_tokens": stats.get("cached"),
                "duration_ms": stats.get("duration_ms"),
                "tool_calls": stats.get("tool_calls"),
                "models": stats.get("models"),
            }
            from ._pricing import gemini_cost_usd
            cost_usd = gemini_cost_usd(
                self.model,
                input_tokens=stats.get("input_tokens") or 0,
                output_tokens=stats.get("output_tokens") or 0,
                cached_input_tokens=stats.get("cached") or 0,
            )

        exit_reason = classify_exit(
            proc.returncode, proc.timed_out,
            stderr=proc.stderr,
            stdout=proc.stdout,
            envelope_error=_envelope_is_error(parsed if isinstance(parsed, dict) else None),
            have_envelope=isinstance(parsed, dict),
        )
        return AgentRunResult(
            success=(exit_reason == "completed"),
            files_modified=files_modified,
            stdout=proc.stdout,
            stderr=annotated_stderr,
            transcript_path=transcript_path,
            exit_reason=exit_reason,
            duration_s=duration_s,
            cost_usd=cost_usd,
            usage=usage_dict,
        )
