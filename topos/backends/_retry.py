"""Shared retry loop for agent backends.

Centralizes the framework's reaction to a normalized ``ExitReason``: which
reasons get retried, how many times, and the backoff. Every backend's ``run()``
delegates here so the policy lives in one place instead of drifting across
hand-rolled per-backend loops (codex, for one, used to not retry quota at all).

Internal helper — not part of the public ``topos.backends`` surface.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .base import AgentRunResult


def run_with_retries(
    run_once: Callable[[], AgentRunResult],
    *,
    retryable: dict[str, int],         # ExitReason -> how many times to retry it
    base_wait_s: dict[str, float],     # ExitReason -> initial backoff (doubled each retry)
    before_each: Callable[[], None] | None = None,
    label: str = "agent",
) -> AgentRunResult:
    """Call ``run_once`` until it returns a non-retryable ``exit_reason``.

    ``before_each`` runs prior to every attempt (e.g. a rate-limit token-bucket
    acquire). An ``exit_reason`` absent from ``retryable`` is returned as-is.
    """
    remaining = dict(retryable)
    wait = dict(base_wait_s)
    while True:
        if before_each is not None:
            before_each()
        result = run_once()
        reason = result.exit_reason
        if remaining.get(reason, 0) > 0:
            remaining[reason] -= 1
            w = wait.get(reason, 0.0)
            print(
                f"[{label}] exit_reason={reason}; retrying in {w:.0f}s "
                f"({remaining[reason]} more retr{'y' if remaining[reason] == 1 else 'ies'} allowed)"
            )
            time.sleep(w)
            wait[reason] = w * 2
            continue
        return result
