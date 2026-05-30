"""Tests for gemini_cli's type:error detection + transient retry logic.

Observed on cab_gemini_pro_palace5_v2 2026-05-13: handle2 agent died at
12s with ``{"type":"error","severity":"error","message":"Invalid stream:
The model returned an empty response or malformed tool call."}`` and a
trailing ``{"type":"result","status":"error"}``. That single transient
glitch cascade-killed the run via 15+ downstream skipped tasks. These
tests pin the retry pathway so we don't regress."""

from __future__ import annotations

import json

from topos.backends.gemini_cli import (
    _extract_error_events,
    _is_transient_gemini_error,
)


def _stream(events: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_extract_error_events_finds_buried_event():
    stdout = _stream([
        {"type": "init", "model": "gemini-3.1-pro-preview"},
        {"type": "message", "role": "user", "content": "..."},
        {"type": "tool_use", "tool_name": "read_file"},
        {"type": "tool_result", "status": "success"},
        {"type": "error", "severity": "error",
         "message": "Invalid stream: The model returned an empty response or malformed tool call."},
        {"type": "result", "status": "error", "stats": {}},
    ])
    events = _extract_error_events(stdout)
    assert len(events) == 1
    assert "Invalid stream" in events[0]["message"]


def test_extract_error_events_empty_when_clean():
    stdout = _stream([
        {"type": "init"}, {"type": "tool_use", "tool_name": "x"},
        {"type": "result", "status": "success", "stats": {}},
    ])
    assert _extract_error_events(stdout) == []


def test_detect_invalid_stream_as_transient():
    err = [{"type": "error", "message": "Invalid stream: The model returned an empty response or malformed tool call."}]
    assert _is_transient_gemini_error(err) is not None


def test_detect_no_candidates_as_transient():
    """Sibling pattern — Gemini sometimes responds with zero candidates."""
    err = [{"type": "error", "message": "No candidates returned from model"}]
    assert _is_transient_gemini_error(err) is not None


def test_detect_recitation_as_transient():
    """RECITATION = Google's content-filter false-positive. Retry usually works."""
    err = [{"type": "error", "message": "Generation stopped due to RECITATION"}]
    assert _is_transient_gemini_error(err) is not None


def test_non_transient_error_returns_none():
    """A genuine config/auth error should NOT be retried."""
    err = [{"type": "error", "message": "API key is invalid"}]
    assert _is_transient_gemini_error(err) is None


def test_empty_error_list_returns_none():
    assert _is_transient_gemini_error([]) is None


def test_extract_handles_noise_lines():
    """gemini-cli interleaves npm notice and YOLO banner — parser must
    skip non-JSON lines instead of crashing."""
    stdout = (
        "npm notice New minor version of npm available!\n"
        "YOLO mode is enabled.\n"
        '{"type":"error","message":"Invalid stream"}\n'
        '{"type":"result","status":"error"}\n'
    )
    events = _extract_error_events(stdout)
    assert len(events) == 1
