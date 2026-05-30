"""Sliding-window rate limiter shared across CLI agent backends.

Why this is here, not at the runner level:

The runner can cap *concurrent* parallel tasks (``max_parallel_tasks``)
but doesn't know about **requests per unit time**. With 17 part agents
finishing fast, max_parallel=4 still bursts all 17 through within ~3 min
— easily blowing through Claude Pro's 50-msg/5h subscription window in
one shot.

A token bucket sits inside each backend's ``run()`` method. Before the
CLI is spawned, ``acquire()`` blocks until a token is available. The
bucket refills at a steady rate so long-running pipelines stay under
the subscription's long-term average even when bursty by nature.

Defaults: bucket is disabled (``rate_per_minute=None``) so the existing
high-throughput behavior is unchanged on Max-tier accounts. Set
``backends.claude.rate_per_minute=6`` (or similar) for Pro accounts to
stay below ~50 msg / 5h.

Thread-safe — the runner's ThreadPoolExecutor calls ``run()`` from
multiple threads simultaneously.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    """Classic token bucket: ``capacity`` tokens, refilled at
    ``refill_per_sec`` tokens/second. Always-full at construction so a
    cold start gets a single burst up to ``capacity`` before throttling
    kicks in."""
    capacity: float
    refill_per_sec: float
    _tokens: float = 0.0
    _last: float = 0.0
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, *, cost: float = 1.0, max_wait_s: float | None = None) -> float:
        """Block until ``cost`` tokens are available, then consume them.

        Returns the seconds actually waited (zero if there was capacity).
        Raises ``TimeoutError`` if waiting would exceed ``max_wait_s``."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(
                    self.capacity, self._tokens + elapsed * self.refill_per_sec
                )
                self._last = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return waited
                deficit = cost - self._tokens
                wait_for = deficit / self.refill_per_sec if self.refill_per_sec > 0 else float("inf")
            if max_wait_s is not None and waited + wait_for > max_wait_s:
                raise TimeoutError(
                    f"TokenBucket: would wait {wait_for:.1f}s for {cost} token(s); "
                    f"max_wait_s={max_wait_s}"
                )
            # Cap individual sleeps at 5s so a long wait is still
            # interruptible (Ctrl-C) and we recompute the deficit fresh
            # in case other consumers freed tokens.
            sleep_s = min(wait_for, 5.0)
            time.sleep(sleep_s)
            waited += sleep_s


def make_bucket_from_config(rate_per_minute: float | None) -> TokenBucket | None:
    """Build a TokenBucket sized for ``rate_per_minute`` requests, or
    return ``None`` if rate_per_minute is None/0/negative (= disabled).

    Bucket capacity equals rate_per_minute so a burst of up to that many
    can fire immediately on a cold pipeline. Refill rate keeps the
    long-term average at rate_per_minute / 60."""
    if not rate_per_minute or rate_per_minute <= 0:
        return None
    rpm = float(rate_per_minute)
    return TokenBucket(capacity=rpm, refill_per_sec=rpm / 60.0)


# HTTP status codes that signal a TRANSIENT failure worth retrying.
# Distinct from generic 4xx errors (400 bad request, 401 unauthorized, etc.)
# which won't fix themselves on retry.
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({
    408,  # Request Timeout
    429,  # Too Many Requests — the canonical rate-limit signal
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
})


def is_retryable_http_status(code: int) -> bool:
    """True for 429 (rate limit) + 5xx (transient server faults) + 408."""
    return code in RETRYABLE_HTTP_STATUSES


def sleep_for_backoff(attempt: int, base_wait_s: float, max_wait_s: float = 600.0) -> float:
    """Sleep for ``base_wait_s * 2**attempt`` seconds (capped at
    ``max_wait_s``) and return the actual seconds slept.

    Used by HTTP-API critics (gemini_vision, openai_vision) which can't
    share the AgentRunResult retry path in claude_cli.py. Exponential
    backoff is the standard idiom for API rate limits — gives the
    upstream service time to refresh its window before each retry."""
    import time as _t
    wait = min(base_wait_s * (2 ** attempt), max_wait_s)
    _t.sleep(wait)
    return wait
