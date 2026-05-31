"""Per-provider stream-json event vocabulary ("dialect").

Each CLI backend that emits ``--output-format=stream-json`` declares ONE
``StreamDialect`` describing which event types mean "real work" (reset the idle
watchdog), which open/close a pending-tool slot, and which is the terminal
result. Previously every backend passed these substrings inline to
``run_process_with_watchdog`` — so when claude gained ``--include-partial-
messages`` + the ``stream_event`` activity signal, gemini silently kept the old
2-event list and the same idle-kill blind spot. Declaring the vocabulary in one
place per provider keeps them consistent and makes "add a new vendor" = write
one dialect.

Events NOT in ``activity_events`` (e.g. ``rate_limit_event`` / ``system``
heartbeats) deliberately do NOT reset the idle timer — a CLI emitting only
heartbeats while making no real progress should still idle-trip.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StreamDialect:
    activity_events: tuple[str, ...]      # substrings whose appearance = real work
    tool_open: str                         # opens a pending-tool slot (suppresses idle-kill)
    tool_close: str                        # closes it
    done_event: str                        # terminal result marker
    activity_stderr: tuple[str, ...] = ()  # stderr lines that also count as activity

    def watchdog_kwargs(self) -> dict:
        """Derive the ``run_process_with_watchdog`` event-signal kwargs."""
        kw: dict = {
            "activity_event_substrings": list(self.activity_events),
            "tool_pending_substrings": (self.tool_open, self.tool_close),
            "done_event_substring": self.done_event,
        }
        if self.activity_stderr:
            kw["activity_stderr_substrings"] = list(self.activity_stderr)
        return kw


# Anthropic claude CLI — run with --include-partial-messages, so in-progress
# turns stream ``stream_event`` deltas that keep the idle timer alive.
CLAUDE_STREAM = StreamDialect(
    activity_events=('"type":"assistant"', '"type":"user"', '"type":"stream_event"'),
    tool_open='"type":"tool_use"',
    tool_close='"type":"tool_result"',
    done_event='"type":"result"',
)

# Google gemini CLI — same stream-json event names. Include ``stream_event`` so
# a partial-streaming gemini build keeps the idle timer alive too (closes the
# blind spot claude already fixed); gemini also signals liveness on stderr via
# its own retry-with-backoff lines.
GEMINI_STREAM = StreamDialect(
    activity_events=('"type":"assistant"', '"type":"user"', '"type":"stream_event"'),
    tool_open='"type":"tool_use"',
    tool_close='"type":"tool_result"',
    done_event='"type":"result"',
    activity_stderr=("failed with status", "Retrying with backoff"),
)
