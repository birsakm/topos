# ADR 0003 — Coding agents are invoked as CLI subprocesses; claude CLI is the default backend

- **Date:** 2026-05-10
- **Status:** Accepted

## Context

Coding agents that produce multi-file Python code can be driven via (a) a CLI subprocess (`claude -p ...`, `codex ...`, `gemini ...`), (b) a hand-rolled agent loop over the Anthropic SDK, or (c) a hybrid. The framework needs an abstraction that allows multiple providers and both subscription and API-key auth.

## Decision

Coding agents are invoked through a pluggable `AgentBackend` protocol (`topos/backends/base.py`). The first and default implementation is `ClaudeCLIBackend`, which spawns `claude` as a subprocess and consumes its JSON output. `CodexCLIBackend` and `GeminiCLIBackend` are reserved placeholders for the same protocol. Auth mode (`subscription` or `api_key`) is per-backend config.

## Alternatives considered

1. **Anthropic SDK agent loop.** Rejected for the default path: requires reimplementing Read/Edit/Write/Bash/MCP tooling that the claude CLI already provides; loses skill ecosystem.
2. **Hybrid (SDK outer + CLI inner).** Considered for the orchestrator/planner role specifically; deferred. The orchestrator stays a deterministic Python program for now, with optional `planner_agent` invocation as a SubgraphTask later.

## Consequences

- Each `AgentTask` execution = one `claude` subprocess invocation with a constructed prompt, scoped MCP tools, and an isolated workspace.
- The framework cannot easily inspect intermediate reasoning; it only sees the final transcript and the file diff.
- Adding codex/gemini = implementing `AgentBackend.run` for that CLI.
- Quota / cost depend on the user's subscription or API key; the framework records `exit_reason: "quota"` distinctly from `"error"`.
