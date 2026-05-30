"""Pin classify_exit's 4 decision branches.

``classify_exit`` is the single helper every backend uses to map
(rc, timeout, stderr/stdout tails, envelope_error) → ExitReason.
Behavior shifts here propagate to claude / codex / gemini all at once,
so the four branches and their quota-keyword scoping are tested directly
rather than transitively through backend tests.

The most subtle case is the codex one — rc==0 + envelope_error=False
but the plain-text stdout contains a quota keyword (because the model is
*answering a question about quotas*, not failing on quota). The current
behavior surfaces this as "quota". The "codex_clean_stdout_quota_keyword"
case below pins that so any future softening is deliberate.
"""

from __future__ import annotations

import pytest

from topos.backends._utils import (
    PROMPT_BYTES_LIMIT,
    assert_prompt_within_limit,
    classify_exit,
    has_quota_keywords,
)


# ---------- classify_exit: 4 branches × the quota-keyword scoping rules ----

# Each row: (label, rc, timed_out, stderr, stdout, envelope_error, have_envelope, expected)
_CASES = [
    # Precedence 1: timeout dominates everything.
    ("timeout_clean",          1, True,  "",                       "",                                            False, True,  "timeout"),
    ("timeout_with_quota",     1, True,  "quota exceeded",         "",                                            False, True,  "timeout"),

    # Precedence 2: rc != 0 — scan BOTH stderr and stdout for quota keywords.
    ("rc_nonzero_clean",       1, False, "",                       "",                                            False, True,  "error"),
    ("rc_nonzero_stderr_quota",1, False, "rate limit hit",         "",                                            False, True,  "quota"),
    ("rc_nonzero_stdout_quota",1, False, "",                       "insufficient_quota",                          False, True,  "quota"),

    # Precedence 3: envelope_error — scan stderr ONLY (stdout IS the envelope).
    ("env_err_clean",          0, False, "",                       "",                                            True,  True,  "error"),
    ("env_err_stderr_quota",   0, False, "resource_exhausted",     "",                                            True,  True,  "quota"),
    # Model-written error JSON mentioning "quota" must NOT flip to quota — topic, not failure.
    ("env_err_stdout_quota_is_not_quota",
                               0, False, "",                       '{"is_error": true, "error": "user asked about quota policy"}',
                                                                                                                  True,  True,  "error"),

    # Precedence 4a: clean exit, have_envelope=True (claude/gemini json or
    # stream-json). Envelope is the truth — no fallback scan in either
    # direction. stderr chatter or stdout rate_limit_event heartbeats
    # don't flip the result; if there were a real quota failure the
    # envelope's is_error / error / subtype would have flagged it.
    ("clean_envelope_completed",
                               0, False, "",                       "",                                            False, True,  "completed"),
    ("clean_envelope_stderr_chatter_is_benign",
                               0, False, "billing problem",        "",                                            False, True,  "completed"),
    ("clean_envelope_stdout_rate_limit_event_is_benign",
                               0, False, "",                       '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}',
                                                                                                                  False, True,  "completed"),

    # Precedence 4b: clean exit, have_envelope=False (codex plain-text).
    # Last-ditch scan of BOTH streams — false-positive risk if the model
    # is answering about billing/rate-limit topics.
    ("clean_codex_completed",  0, False, "",                       "",                                            False, False, "completed"),
    ("clean_codex_stderr_quota",
                               0, False, "billing problem",        "",                                            False, False, "quota"),
    ("clean_codex_stdout_quota_keyword",
                               0, False, "",                       "Sure, here is how rate limit headers work...", False, False, "quota"),
]


@pytest.mark.parametrize(
    "rc,timed_out,stderr,stdout,envelope_error,have_envelope,expected",
    [(c[1], c[2], c[3], c[4], c[5], c[6], c[7]) for c in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_classify_exit_branches(rc, timed_out, stderr, stdout,
                                  envelope_error, have_envelope, expected):
    """Cover all 4 branches and the quota-keyword scoping. The case ids
    (visible in pytest -v output) document each rule. If you add a branch,
    add a labelled case here rather than a new top-level test."""
    assert classify_exit(
        rc, timed_out,
        stderr=stderr, stdout=stdout,
        envelope_error=envelope_error,
        have_envelope=have_envelope,
    ) == expected


# ---------- has_quota_keywords ---------------------------------------------


@pytest.mark.parametrize("text,expected", [
    # Positive: account-level quota / usage caps
    ("quota", True),
    ("QUOTA EXCEEDED", True),
    ("rate limit", True),
    ("rate_limit", True),
    ("credit balance is zero", True),
    ("billing required", True),
    ("insufficient_quota", True),
    ("RESOURCE_EXHAUSTED", True),
    # Positive: transient server-side throttling — routes through the same
    # retry path because the right response is identical (wait + retry).
    # Anthropic API: "Server is temporarily limiting requests (not your
    # usage limit)" and 529 "Overloaded" — both short-lived.
    ("Server is temporarily limiting requests (not your usage limit)", True),
    ("API Error: Overloaded", True),
    ("HTTP 529 Overloaded", True),
    ("Service Unavailable", True),
    # Negative: empty / unrelated / partial-keyword false-positive guards
    ("ok", False),
    ("", False),
    # "rate" alone must NOT match — only "rate limit" / "rate_limit"
    ("at this rate we'll finish soon", False),
])
def test_has_quota_keywords(text, expected):
    assert has_quota_keywords(text) is expected


# ---------- assert_prompt_within_limit -------------------------------------


def test_prompt_limit_boundary_passes_strict_greater_than():
    """The check is strict greater-than, so exactly at the limit passes."""
    assert_prompt_within_limit("x" * PROMPT_BYTES_LIMIT, "claude")
    # Just under: also fine
    assert_prompt_within_limit("x" * (PROMPT_BYTES_LIMIT - 1), "claude")


def test_prompt_over_limit_raises_with_backend_name():
    payload = "x" * (PROMPT_BYTES_LIMIT + 1)
    with pytest.raises(ValueError, match="codex"):
        assert_prompt_within_limit(payload, "codex")


def test_prompt_size_counts_utf8_bytes_not_characters():
    """UTF-8 multi-byte characters count by byte width, not char count.
    100k CJK chars × 3 bytes/char = 300k bytes — well over limit."""
    payload = "好" * 100_000
    with pytest.raises(ValueError, match=r"\d+ bytes"):
        assert_prompt_within_limit(payload, "claude")
