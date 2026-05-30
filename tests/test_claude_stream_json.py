"""Stream-json parsing for the claude CLI backend.

After switching ``--output-format json`` to ``stream-json``, claude CLI
emits one JSON event per stdout line covering each turn (system /
assistant / tool_use / tool_result / rate_limit / final result). Topos
persists the raw stream as ``transcript.jsonl`` and ALSO extracts the
trailing ``type: result`` event into ``transcript.json`` for backward
compatibility with the existing cli_critic + spec consumers that expect
the single-envelope shape.

This test pins the parser contract:
  - Find the LAST ``type: result`` event in the stream
  - Skip lines that aren't valid JSON (truncated buffers, blank lines)
  - Skip non-result event types (system, assistant, rate_limit)
  - Return None when no result event exists (early crash case)
"""

from __future__ import annotations

import json

from topos.backends.claude_cli import _parse_stream_json_final_result


def test_extract_final_result_from_typical_stream():
    """Realistic 5-event stream: system init / assistant message / rate_limit /
    intermediate result-ish event (but not type=result) / final result."""
    events = [
        {"type": "system", "subtype": "init", "session_id": "s1"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
        {"type": "result", "subtype": "success",
         "total_cost_usd": 0.08, "num_turns": 1,
         "usage": {"input_tokens": 6, "output_tokens": 5}},
    ]
    stdout = "\n".join(json.dumps(e) for e in events) + "\n"

    parsed = _parse_stream_json_final_result(stdout)
    assert parsed is not None
    assert parsed["type"] == "result"
    assert parsed["total_cost_usd"] == 0.08
    assert parsed["num_turns"] == 1
    assert parsed["usage"]["input_tokens"] == 6


def test_finds_last_result_when_multiple_present():
    """In some traces (e.g. session re-init scenarios) multiple result events
    can appear. We always take the LAST one — it represents the final
    state of the run."""
    events = [
        {"type": "result", "subtype": "success", "total_cost_usd": 0.01, "num_turns": 1},
        {"type": "system", "subtype": "continue"},
        {"type": "result", "subtype": "success", "total_cost_usd": 0.05, "num_turns": 3},
    ]
    stdout = "\n".join(json.dumps(e) for e in events) + "\n"
    parsed = _parse_stream_json_final_result(stdout)
    assert parsed["total_cost_usd"] == 0.05
    assert parsed["num_turns"] == 3


def test_returns_none_on_empty_or_whitespace_stdout():
    assert _parse_stream_json_final_result("") is None
    assert _parse_stream_json_final_result("   \n  \n") is None
    assert _parse_stream_json_final_result(None) is None  # type: ignore


def test_skips_invalid_json_lines():
    """A truncated event (e.g. process killed mid-write) leaves a partial
    JSON line. Walk past it and find the valid trailing events."""
    stdout = (
        '{"type":"system","subtype":"init"}\n'
        '{"type":"assistant","message":{"content":[{"type":"text"\n'    # truncated
        '{"type":"result","subtype":"success","total_cost_usd":0.12}\n'
    )
    parsed = _parse_stream_json_final_result(stdout)
    assert parsed is not None
    assert parsed["total_cost_usd"] == 0.12


def test_returns_none_when_no_result_event():
    """Early CLI crash before producing a result event → return None so the
    backend can fall through to its 'save raw stdout' fallback path."""
    stdout = (
        '{"type":"system","subtype":"init"}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working..."}]}}\n'
    )
    parsed = _parse_stream_json_final_result(stdout)
    assert parsed is None


def test_extracts_full_result_shape_for_classify_exit_compatibility():
    """The downstream ``classify_exit`` + envelope-error check + cost
    extraction all read keys from the SAME envelope shape that the old
    --output-format json mode produced. The result event in stream-json
    has identical key set, so parsed dict is drop-in compatible."""
    result_event = {
        "type": "result", "subtype": "success",
        "is_error": False,
        "duration_ms": 2366, "duration_api_ms": 2327,
        "num_turns": 7,
        "result": "the final assistant text",
        "stop_reason": "end_turn",
        "session_id": "abc",
        "total_cost_usd": 0.31,
        "usage": {"input_tokens": 6, "output_tokens": 10},
        "modelUsage": {"claude-opus-4-7[1m]": {"costUSD": 0.31}},
        "terminal_reason": "completed",
    }
    stdout = json.dumps(result_event) + "\n"
    parsed = _parse_stream_json_final_result(stdout)
    # Keys the rest of the backend reads must all be present
    for k in ("total_cost_usd", "usage", "modelUsage", "is_error",
              "subtype", "terminal_reason"):
        assert k in parsed, f"result envelope missing key {k!r}"


def test_jsonl_format_one_event_per_line_no_pretty_printing():
    """Sanity: real claude CLI emits ONE event per line (no pretty
    printing across lines). Our parser splits on newlines — would break
    if events were multi-line. This is a contract check on input format."""
    # A typical event from real claude output (single line)
    line = ('{"type":"result","subtype":"success","duration_ms":2366,'
            '"num_turns":1,"total_cost_usd":0.08,"usage":{"input_tokens":6}}')
    stdout = line + "\n"
    parsed = _parse_stream_json_final_result(stdout)
    assert parsed is not None
    assert parsed["num_turns"] == 1
