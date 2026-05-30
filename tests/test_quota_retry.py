"""Quota retry + rate-limit token bucket coverage.

Both protect long-running pipelines from the Claude subscription rate
limit (Pro: ~50 msg / 5h rolling). Together: token bucket spreads
requests so the limit isn't hit gratuitously; retry backs off and
retries when it IS hit anyway."""

from __future__ import annotations

import time
from unittest.mock import patch

from topos.backends._rate_limit import TokenBucket, make_bucket_from_config


# --- TokenBucket ---------------------------------------------------------


def test_bucket_starts_full():
    """Cold start should let a burst of ``capacity`` pass instantly so
    short pipelines aren't penalized by the throttle."""
    b = TokenBucket(capacity=5, refill_per_sec=1.0)
    for _ in range(5):
        assert b.acquire() == 0.0  # no wait


def test_bucket_blocks_when_drained():
    """Once empty, the 6th call must wait for refill."""
    b = TokenBucket(capacity=2, refill_per_sec=10.0)  # 10/sec refill → 100ms per token
    b.acquire()
    b.acquire()
    start = time.monotonic()
    waited = b.acquire()
    elapsed = time.monotonic() - start
    # Should have waited ~0.1s for one token to refill (with a generous bound
    # for CI scheduler jitter)
    assert waited >= 0.05, f"expected to wait, got {waited:.3f}s"
    assert elapsed < 0.5, f"unexpectedly slow refill: {elapsed:.3f}s"


def test_bucket_max_wait_raises_timeout():
    """``max_wait_s`` upper-bounds blocking time — useful when the
    backend wants to fail fast rather than freeze the whole iteration."""
    import pytest
    b = TokenBucket(capacity=1, refill_per_sec=0.1)  # 10s per refill
    b.acquire()  # drain
    with pytest.raises(TimeoutError):
        b.acquire(max_wait_s=0.5)


def test_make_bucket_disabled_when_rate_is_falsy():
    """``rate_per_minute=None`` / 0 / negative all disable the bucket
    so the high-throughput default path stays cost-free."""
    assert make_bucket_from_config(None) is None
    assert make_bucket_from_config(0) is None
    assert make_bucket_from_config(-5) is None


def test_make_bucket_sizes_capacity_to_rate():
    """Capacity == rate_per_minute → bursts up to that many fire instantly,
    long-term average is rate/min."""
    b = make_bucket_from_config(10.0)
    assert b is not None
    assert b.capacity == 10.0
    # 10 / 60 = 0.1667 tokens per second
    assert abs(b.refill_per_sec - 10.0 / 60.0) < 1e-6


# --- ClaudeCLIBackend retry behavior -------------------------------------


def _make_result(exit_reason: str):
    """Minimal AgentRunResult stand-in for retry-loop coverage."""
    from topos.backends.base import AgentRunResult
    return AgentRunResult(
        success=(exit_reason == "completed"),
        files_modified=[],
        stdout="",
        stderr="",
        transcript_path=__import__("pathlib").Path("/dev/null"),
        exit_reason=exit_reason,  # type: ignore[arg-type]
        duration_s=0.1,
    )


def test_run_returns_completed_immediately_no_retry():
    from topos.backends.claude_cli import ClaudeCLIBackend
    backend = ClaudeCLIBackend(name="claude", max_quota_retries=3, quota_retry_wait_s=0.01)
    call_count = {"n": 0}

    def fake_once(**kwargs):
        call_count["n"] += 1
        return _make_result("completed")

    with patch.object(backend, "_run_once", side_effect=fake_once):
        r = backend.run(
            prompt="x", workspace=__import__("pathlib").Path("/tmp"),
            allowed_tools=[], mcp_servers=[],
        )
    assert r.exit_reason == "completed"
    assert call_count["n"] == 1, "no retry expected on completed"


def test_run_retries_on_quota_then_succeeds():
    from topos.backends.claude_cli import ClaudeCLIBackend
    backend = ClaudeCLIBackend(name="claude", max_quota_retries=3, quota_retry_wait_s=0.01)
    call_count = {"n": 0}

    def fake_once(**kwargs):
        call_count["n"] += 1
        return _make_result("quota") if call_count["n"] < 3 else _make_result("completed")

    with patch.object(backend, "_run_once", side_effect=fake_once), \
         patch("topos.backends.claude_cli.time.sleep") as fake_sleep:
        r = backend.run(
            prompt="x", workspace=__import__("pathlib").Path("/tmp"),
            allowed_tools=[], mcp_servers=[],
        )
    assert r.exit_reason == "completed"
    assert call_count["n"] == 3
    assert fake_sleep.call_count == 2
    sleeps = [c.args[0] for c in fake_sleep.call_args_list]
    assert sleeps[1] == 2 * sleeps[0], f"backoff should double: {sleeps}"


def test_run_gives_up_after_max_retries():
    from topos.backends.claude_cli import ClaudeCLIBackend
    backend = ClaudeCLIBackend(name="claude", max_quota_retries=2, quota_retry_wait_s=0.01)
    call_count = {"n": 0}

    def fake_once(**kwargs):
        call_count["n"] += 1
        return _make_result("quota")

    with patch.object(backend, "_run_once", side_effect=fake_once), \
         patch("topos.backends.claude_cli.time.sleep"):
        r = backend.run(
            prompt="x", workspace=__import__("pathlib").Path("/tmp"),
            allowed_tools=[], mcp_servers=[],
        )
    assert r.exit_reason == "quota"
    assert call_count["n"] == 3


def test_make_critic_honors_global_override():
    """Setting ``visual_critic.default`` overrides every rubric's
    ``judge_backend:`` field. One-knob flip from Claude → Gemini for the
    whole pipeline without editing per-rubric YAMLs."""
    from topos.agents.visual_critic.base import Criterion, Rubric, make_critic
    from topos.agents.visual_critic.gemini_vision import GeminiVisionCritic
    rubric_says_claude = Rubric(
        id="r", judge_backend="claude_vision",
        pass_threshold=0.6,
        criteria=[Criterion(id="x", prompt="y", weight=1.0)],
    )
    fake_config = {"visual_critic": {"default": "gemini_vision"}}
    with patch("topos.config.load_effective_config", return_value=fake_config):
        critic = make_critic(rubric_says_claude)
    assert isinstance(critic, GeminiVisionCritic), (
        f"override should have dispatched to gemini_vision; got {type(critic).__name__}"
    )


def test_make_critic_no_override_honors_rubric():
    """When ``visual_critic.default`` is unset, the rubric's own
    ``judge_backend:`` field decides."""
    from topos.agents.visual_critic.base import Criterion, Rubric, make_critic
    from topos.agents.visual_critic.gemini_vision import GeminiVisionCritic
    rubric_says_gemini = Rubric(
        id="r", judge_backend="gemini_vision",
        pass_threshold=0.6,
        criteria=[Criterion(id="x", prompt="y", weight=1.0)],
    )
    fake_config = {"visual_critic": {}}  # no default set
    with patch("topos.config.load_effective_config", return_value=fake_config):
        critic = make_critic(rubric_says_gemini)
    assert isinstance(critic, GeminiVisionCritic)


def test_run_does_not_retry_on_error():
    """Non-quota errors rarely fix themselves on retry; don't burn budget."""
    from topos.backends.claude_cli import ClaudeCLIBackend
    backend = ClaudeCLIBackend(name="claude", max_quota_retries=3, quota_retry_wait_s=0.01)
    call_count = {"n": 0}

    def fake_once(**kwargs):
        call_count["n"] += 1
        return _make_result("error")

    with patch.object(backend, "_run_once", side_effect=fake_once):
        r = backend.run(
            prompt="x", workspace=__import__("pathlib").Path("/tmp"),
            allowed_tools=[], mcp_servers=[],
        )
    assert r.exit_reason == "error"
    assert call_count["n"] == 1
