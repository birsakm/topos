"""Shared helpers across CLI agent backends (claude/codex/gemini).

These were three identical copies until 2026-05-11; lifted here to remove
~75 LOC of duplication. Each backend still owns its envelope-specific
"is this an error response?" check — that part can't be generic since
the JSON envelope shape differs per CLI.
"""

from __future__ import annotations

from .base import ExitReason


QUOTA_KEYWORDS: tuple[str, ...] = (
    # Subscription / API quota exhaustion — the user's account hit a cap.
    "quota",
    "rate limit",
    "rate_limit",
    "credit",
    "billing",
    "insufficient_quota",
    "resource_exhausted",
    # Transient server-side throttling — distinct from quota exhaustion but
    # the right response is identical (retry with backoff). The Anthropic
    # API surfaces these as: "Server is temporarily limiting requests
    # (not your usage limit)" and 529 "Overloaded" responses; both are
    # short-lived (~30-60s) and disappear on retry. Classifying them as
    # "quota" routes through the same retry path (max_quota_retries + backoff)
    # so a momentary server hiccup doesn't fail an otherwise-healthy run.
    "temporarily limiting",
    "overloaded",
    "529",
    "service unavailable",
)


# Defensive ceiling on per-call prompt size. Linux ARG_MAX is typically ~2 MB
# but Windows / macOS / containerized envs run as low as 32 KB; passing a
# huge prompt as a CLI argv risks E2BIG. 200 KB leaves headroom for the rest
# of the argv (flags, paths) on the tightest realistic platforms. If you're
# hitting this, externalize content into workspace files the agent can Read.
PROMPT_BYTES_LIMIT = 200_000


def assert_prompt_within_limit(prompt: str, backend_name: str) -> None:
    """Raise ``ValueError`` if the prompt would risk argv overflow when
    passed via CLI. Cheap upfront check beats a cryptic ``E2BIG`` from the
    OS at exec() time."""
    size = len(prompt.encode("utf-8"))
    if size > PROMPT_BYTES_LIMIT:
        raise ValueError(
            f"{backend_name}: prompt is {size} bytes (limit {PROMPT_BYTES_LIMIT}). "
            "Risk of OS argv overflow on tight platforms. Externalize heavy "
            "content into workspace files (the agent reads them via Read)."
        )


def has_quota_keywords(text: str) -> bool:
    """Case-insensitive substring match against any QUOTA_KEYWORDS entry."""
    text = text.lower()
    return any(s in text for s in QUOTA_KEYWORDS)


def classify_exit(
    returncode: int,
    timed_out: bool,
    *,
    stderr: str = "",
    stdout: str = "",
    envelope_error: bool = False,
    have_envelope: bool = True,
) -> ExitReason:
    """Map (rc, timeout, output streams, envelope_error) → ``ExitReason``.

    ``stdout`` / ``stderr`` are passed as full strings; this function scans
    them for quota keywords as needed (case-insensitive substring match).

    ``envelope_error`` is True when the CLI returned rc=0 but its JSON
    envelope flagged a failure (claude ``is_error``, gemini ``error``,
    etc.) — each backend computes this from its own envelope shape and
    passes it in. We classify quota separately from generic error so
    upstream can branch on it.

    ``have_envelope`` is True when the backend has parsed a structured
    envelope from stdout (claude/gemini JSON or stream-json). In that case
    ``envelope_error`` is the authoritative success signal and we do NOT
    do a fallback keyword scan of stdout — stream-json emits benign
    ``rate_limit_event`` heartbeats that look identical to error messages
    under keyword matching. Plain-text backends (codex) pass False and
    still get the fallback scan.

    Quota-keyword scanning is scoped:
      - rc != 0: scan stdout + stderr (any source may print the reason).
      - envelope_error: scan **stderr only** — stdout IS the envelope JSON,
        and quoting the model's own error text could false-match a
        ``"quota"`` substring (e.g. the model talking about the topic).
      - rc == 0 + no envelope error + no envelope at all: scan both as a
        last-ditch heuristic for plain-text CLIs that just print errors
        and exit clean.
    """
    if timed_out:
        return "timeout"
    if returncode != 0:
        return "quota" if has_quota_keywords(stderr + "\n" + stdout) else "error"
    if envelope_error:
        return "quota" if has_quota_keywords(stderr) else "error"
    if not have_envelope and has_quota_keywords(stdout + "\n" + stderr):
        # Plain-text CLI exited 0 but printed a quota message — surface it.
        return "quota"
    return "completed"
