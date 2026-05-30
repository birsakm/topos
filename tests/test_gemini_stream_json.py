"""Gemini CLI stream-json parsing.

After switching the gemini backend's default to ``-o stream-json``, gemini
CLI emits one JSON event per line covering each turn (init / message /
tool_use / tool_result / final result). Topos persists the raw stream as
``transcript.jsonl`` and also extracts the trailing ``type: result`` event
into ``transcript.json`` for backward compatibility with existing readers.

Mirrors ``test_claude_stream_json`` but pins the gemini-specific event
shape (``status`` + ``stats`` vs claude's ``total_cost_usd`` + ``usage``).
"""

from __future__ import annotations

import json

from topos.backends.gemini_cli import (
    _envelope_is_error,
    _parse_stream_json_final_result,
)


# --- parser: finds the result event ---------------------------------------


def test_extract_final_result_from_typical_gemini_stream():
    """Realistic event sequence: init / user message / assistant message /
    result. We expect the parser to pick the trailing result event."""
    events = [
        {"type": "init", "session_id": "s1", "model": "auto-gemini-3"},
        {"type": "message", "role": "user", "content": "Reply HI"},
        {"type": "message", "role": "assistant", "content": "HI", "delta": True},
        {"type": "result", "status": "success", "stats": {
            "total_tokens": 16657, "input_tokens": 16119, "output_tokens": 32,
            "duration_ms": 5856, "tool_calls": 0,
        }},
    ]
    stdout = "\n".join(json.dumps(e) for e in events) + "\n"
    parsed = _parse_stream_json_final_result(stdout)
    assert parsed is not None
    assert parsed["type"] == "result"
    assert parsed["status"] == "success"
    assert parsed["stats"]["input_tokens"] == 16119


def test_skips_non_json_noise_lines():
    """Gemini interleaves npm notices, YOLO banners, and ripgrep warnings
    BEFORE the JSON stream begins. The parser must skip those without
    erroring."""
    stdout = (
        "Warning: True color (24-bit) support not detected.\n"
        "YOLO mode is enabled. All tool calls will be automatically approved.\n"
        "Ripgrep is not available. Falling back to GrepTool.\n"
        '{"type":"init","session_id":"s1"}\n'
        '{"type":"result","status":"success","stats":{"input_tokens":100}}\n'
        "npm notice New minor version of npm available!\n"
    )
    parsed = _parse_stream_json_final_result(stdout)
    assert parsed is not None
    assert parsed["status"] == "success"
    assert parsed["stats"]["input_tokens"] == 100


def test_returns_none_on_empty_or_no_result():
    assert _parse_stream_json_final_result("") is None
    assert _parse_stream_json_final_result(None) is None  # type: ignore
    no_result = '{"type":"init","session_id":"s1"}\n'
    assert _parse_stream_json_final_result(no_result) is None


def test_handles_truncated_partial_json_mid_stream():
    """A killed process leaves a partial JSON line. Parser must skip
    it and find any valid trailing events."""
    stdout = (
        '{"type":"init"}\n'
        '{"type":"message","role":"assistant","content":"partial...\n'   # truncated
        '{"type":"result","status":"success","stats":{}}\n'
    )
    parsed = _parse_stream_json_final_result(stdout)
    assert parsed is not None
    assert parsed["type"] == "result"


def test_finds_last_result_when_multiple():
    """Defensive: if gemini ever emits multiple result events (e.g. resume),
    we take the last one — the latest state of the run."""
    events = [
        {"type": "result", "status": "success", "stats": {"input_tokens": 10}},
        {"type": "result", "status": "success", "stats": {"input_tokens": 50}},
    ]
    stdout = "\n".join(json.dumps(e) for e in events) + "\n"
    parsed = _parse_stream_json_final_result(stdout)
    assert parsed["stats"]["input_tokens"] == 50


# --- _envelope_is_error: covers BOTH json and stream-json shapes ----------


def test_envelope_error_detects_status_error_from_stream_json():
    """The stream-json result event uses ``status: error`` instead of the
    older json mode's ``error`` / ``is_error`` keys. Both must be caught."""
    assert _envelope_is_error({"type": "result", "status": "error"}) is True
    assert _envelope_is_error({"type": "result", "status": "failed"}) is True


def test_envelope_error_detects_json_mode_keys_too():
    """Legacy json mode shape still supported."""
    assert _envelope_is_error({"error": "something broke"}) is True
    assert _envelope_is_error({"is_error": True}) is True


def test_envelope_no_error_on_success():
    assert _envelope_is_error({"type": "result", "status": "success"}) is False
    assert _envelope_is_error({"type": "init"}) is False
    assert _envelope_is_error(None) is False
    assert _envelope_is_error({}) is False


# --- Default config -------------------------------------------------------


def test_gemini_backend_always_emits_stream_json():
    """Output format is no longer a knob — stream-json is hardcoded into
    build_cmd because it's the only shape that exposes per-event token
    counts (gemini never returns USD natively, so pricing depends on it).
    This test pins the structural contract: the cmd always carries
    ``-o stream-json``."""
    from topos.backends.gemini_cli import GeminiCLIBackend
    b = GeminiCLIBackend()
    cmd = b.build_cmd("p")
    assert "-o" in cmd
    assert cmd[cmd.index("-o") + 1] == "stream-json"
