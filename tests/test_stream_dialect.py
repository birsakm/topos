"""The per-provider stream dialect is the single source of the watchdog event
vocabulary, so claude and gemini stay consistent (gemini used to silently lack
the partial-message `stream_event` activity signal claude has)."""

from __future__ import annotations

from topos.backends._dialect import CLAUDE_STREAM, GEMINI_STREAM, StreamDialect


def test_watchdog_kwargs_shape():
    d = StreamDialect(
        activity_events=('"type":"assistant"',),
        tool_open='"type":"tool_use"',
        tool_close='"type":"tool_result"',
        done_event='"type":"result"',
    )
    kw = d.watchdog_kwargs()
    assert kw["activity_event_substrings"] == ['"type":"assistant"']
    assert kw["tool_pending_substrings"] == ('"type":"tool_use"', '"type":"tool_result"')
    assert kw["done_event_substring"] == '"type":"result"'
    # no stderr signals → key omitted
    assert "activity_stderr_substrings" not in kw


def test_both_stream_backends_count_partial_stream_events():
    # the whole point: a long generating turn streams stream_event deltas that
    # must reset the idle timer — both providers, not just claude.
    for dialect in (CLAUDE_STREAM, GEMINI_STREAM):
        assert '"type":"stream_event"' in dialect.activity_events


def test_gemini_keeps_its_stderr_retry_signals():
    kw = GEMINI_STREAM.watchdog_kwargs()
    assert "activity_stderr_substrings" in kw
    assert "Retrying with backoff" in kw["activity_stderr_substrings"]
