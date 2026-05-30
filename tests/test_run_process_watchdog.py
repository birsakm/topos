"""Watchdog behaviour for ``run_process_with_watchdog``.

The watchdog keeps a long-running coding-agent CLI call alive past its
naive timeout *as long as it's still streaming output* (stream-json
events for claude/codex/gemini; raw bytes for build/render subprocesses),
and kills it only when truly idle.

Three cases that must hold:

1. **Process exits naturally before soft_timeout** → normal exit, no kill,
   no watchdog interference.

2. **Process runs past soft_timeout but is still emitting output** →
   watchdog extends; process gets to finish (subject to hard_max_s).

3. **Process runs past soft_timeout and is genuinely idle** → watchdog
   kills it after ``idle_grace_s``.

We use small shell scripts in a tmp dir as proxies for the agent CLI so
the tests run in <10 s without touching claude / blender.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from topos.process import run_process_with_watchdog


def _write_script(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text(body)
    p.chmod(0o755)
    return p


def test_natural_exit_before_soft_timeout(tmp_path: Path):
    """Fast-finishing process: returns normally, timed_out=False."""
    script = _write_script(tmp_path, "fast.sh", "#!/bin/bash\necho hi\nexit 0\n")
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=5,
        idle_grace_s=5,
        hard_max_s=20,
    )
    assert r.returncode == 0
    assert r.timed_out is False
    assert "hi" in r.stdout
    assert r.duration_s < 4


def test_idle_kill_past_soft_timeout(tmp_path: Path):
    """Process that sleeps silently past soft_timeout with no file edits →
    watchdog kills after idle_grace_s. The kill reason is appended to
    stderr so postmortem can see why."""
    # Sleeps 15s without doing anything. soft=2s, idle_grace=2s,
    # hard_max=20s. After 2s+2s=4s of total nothing, watchdog kills.
    script = _write_script(tmp_path, "idle.sh", "#!/bin/bash\nsleep 15\n")
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=2,
        idle_grace_s=2,
        hard_max_s=20,
    )
    assert r.timed_out is True
    assert r.duration_s < 10  # killed well before its 15s sleep
    assert "killed: idle" in r.stderr or "killed:" in r.stderr


def test_hard_max_caps_runaway(tmp_path: Path):
    """Even an actively-streaming process gets killed at hard_max_s — last
    resort safety so a runaway can't run forever."""
    # Prints a line every 0.3s "forever" (60 iterations = 18s). hard_max=3s
    # cuts it off.
    script = _write_script(
        tmp_path, "runaway.sh",
        "#!/bin/bash\n"
        "for i in $(seq 1 60); do echo $i ; sleep 0.3 ; done\n",
    )
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=1,
        idle_grace_s=10,    # very lenient, would never trip
        hard_max_s=3,       # hard ceiling
    )
    assert r.timed_out is True
    assert r.duration_s < 5     # killed near the 3s ceiling
    assert "killed: hard_max_s" in r.stderr


def test_stdout_growth_counts_as_activity(tmp_path: Path):
    """A process producing stdout past the soft deadline counts as active.
    This is the default activity signal — covers tools/builds that print
    as they go but don't emit structured stream-json events."""
    # Prints a line every 0.5s for 3s (6 lines), then exits.
    script = _write_script(
        tmp_path, "talkative.sh",
        "#!/bin/bash\nfor i in $(seq 1 6); do echo line $i ; sleep 0.5 ; done\n",
    )
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=1,
        idle_grace_s=2,
        hard_max_s=10,
    )
    assert r.timed_out is False
    assert "line 6" in r.stdout


def test_event_counter_ignores_heartbeats(tmp_path: Path):
    """When ``activity_event_substrings`` is set, only matching events count
    as activity — a CLI emitting only heartbeats should idle-trip even
    though stdout bytes keep growing. Mirrors the stream-json wedge case
    where ``rate_limit_event`` pings tick stdout but no real work is
    happening."""
    # Emits only rate_limit_event-shaped lines, ~every 0.5s for 30s, no
    # assistant/tool_result events. soft=2s + idle_grace=3s → should kill
    # at ~5s. Without event-counter, raw bytes would keep the process
    # alive past soft_timeout indefinitely.
    script = _write_script(
        tmp_path, "heartbeats.sh",
        "#!/bin/bash\n"
        "for i in $(seq 1 60); do "
        "echo '{\"type\":\"rate_limit_event\",\"i\":'$i'}' ; "
        "sleep 0.5 ; "
        "done\n",
    )
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=2,
        idle_grace_s=3,
        hard_max_s=30,
        activity_event_substrings=['"type":"assistant"', '"type":"user"'],
    )
    assert r.timed_out is True
    assert r.duration_s < 10
    assert "killed: idle" in r.stderr
    # Confirm the buffer truly contained the heartbeats we ignored.
    assert '"type":"rate_limit_event"' in r.stdout


def test_event_counter_counts_real_events(tmp_path: Path):
    """A process emitting genuine ``"type":"assistant"`` events should
    survive past soft_timeout even with the event counter active."""
    # Mix heartbeats with a real assistant event every 0.5s for 3s
    # (6 iterations). Tightened from 1s × 10 to keep the contract — survives
    # past soft_timeout — while running 4× faster.
    script = _write_script(
        tmp_path, "mixed_stream.sh",
        "#!/bin/bash\n"
        "for i in $(seq 1 6); do "
        "echo '{\"type\":\"rate_limit_event\",\"i\":'$i'}' ; "
        "echo '{\"type\":\"assistant\",\"i\":'$i'}' ; "
        "sleep 0.5 ; "
        "done\n",
    )
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=1,
        idle_grace_s=2,
        hard_max_s=15,
        activity_event_substrings=['"type":"assistant"', '"type":"user"'],
    )
    assert r.timed_out is False, f"event counter killed an active process: {r.stderr}"
    assert r.duration_s >= 2


def test_done_substring_tagged_in_kill_reason(tmp_path: Path):
    """If the agent emitted its terminal-result event before the watchdog
    kill, the stderr tag should mark the kill as a benign teardown race
    rather than a true wedge — important so postmortem can tell apart
    'finished but tore down slowly' from 'truly stuck'."""
    # Emit a result event, then sit silent past idle_grace.
    script = _write_script(
        tmp_path, "done_then_sleep.sh",
        "#!/bin/bash\n"
        "echo '{\"type\":\"assistant\",\"content\":\"hi\"}'\n"
        "echo '{\"type\":\"result\",\"subtype\":\"success\"}'\n"
        "sleep 30\n",
    )
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=2,
        idle_grace_s=2,
        hard_max_s=15,
        activity_event_substrings=['"type":"assistant"', '"type":"user"'],
        done_event_substring='"type":"result"',
    )
    assert r.timed_out is True
    assert "reported terminal result before kill" in r.stderr


def test_pending_tool_call_suppresses_idle_kill(tmp_path: Path):
    """The long-tool-call case: agent emits a ``tool_use`` event, then the
    stream goes silent while the tool runs externally. Without
    ``tool_pending_substrings`` the watchdog would idle-kill at
    soft_timeout_s + idle_grace_s; with it, the wait counts as blocked
    (not idle) and only ``hard_max_s`` applies."""
    # Emit one assistant turn with a tool_use block, then sit silent for
    # 8 seconds — well past soft_timeout (1s) + idle_grace (2s).
    script = _write_script(
        tmp_path, "tool_then_wait.sh",
        "#!/bin/bash\n"
        "echo '{\"type\":\"assistant\",\"content\":[{\"type\":\"tool_use\",\"id\":\"toolu_1\",\"name\":\"Bash\"}]}'\n"
        "sleep 8\n"
        "echo '{\"type\":\"user\",\"content\":[{\"type\":\"tool_result\",\"tool_use_id\":\"toolu_1\"}]}'\n"
        "echo '{\"type\":\"result\",\"subtype\":\"success\"}'\n",
    )
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=1,
        idle_grace_s=2,
        hard_max_s=20,
        activity_event_substrings=['"type":"assistant"', '"type":"user"'],
        tool_pending_substrings=('"type":"tool_use"', '"type":"tool_result"'),
        done_event_substring='"type":"result"',
    )
    assert r.timed_out is False, (
        f"watchdog killed an agent that was blocked on a tool call: {r.stderr}"
    )
    assert r.returncode == 0
    # Survived past the 8s sleep — required tool_pending detection to keep alive.
    assert r.duration_s >= 7


def test_balanced_tool_then_idle_still_kills(tmp_path: Path):
    """After a tool_use/tool_result pair completes (counts balance), the
    agent has no pending tool — subsequent silence is real idle and the
    watchdog must kill normally. Guards against "blocked-on-tool" turning
    into "free pass forever"."""
    # Emit a balanced tool_use + tool_result pair (no pending tool), then
    # go silent. soft=1s + idle_grace=2s → kill at ~3s. Total sleep is 10s
    # which would never end on its own.
    script = _write_script(
        tmp_path, "balanced_then_idle.sh",
        "#!/bin/bash\n"
        "echo '{\"type\":\"assistant\",\"content\":[{\"type\":\"tool_use\",\"id\":\"toolu_1\",\"name\":\"Bash\"}]}'\n"
        "echo '{\"type\":\"user\",\"content\":[{\"type\":\"tool_result\",\"tool_use_id\":\"toolu_1\"}]}'\n"
        "sleep 10\n",
    )
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=1,
        idle_grace_s=2,
        hard_max_s=20,
        activity_event_substrings=['"type":"assistant"', '"type":"user"'],
        tool_pending_substrings=('"type":"tool_use"', '"type":"tool_result"'),
    )
    assert r.timed_out is True
    assert r.duration_s < 8     # killed well before the 10s sleep
    assert "killed: idle" in r.stderr


def test_hard_max_still_kills_blocked_on_tool(tmp_path: Path):
    """Tool-pending suppresses *idle* kill but not the absolute ceiling.
    A truly stuck tool (network call that never returns, infinite loop)
    must still die at ``hard_max_s`` — that's the whole point of the
    second ceiling."""
    # Open a tool but never close it; sleep way past hard_max.
    script = _write_script(
        tmp_path, "stuck_tool.sh",
        "#!/bin/bash\n"
        "echo '{\"type\":\"assistant\",\"content\":[{\"type\":\"tool_use\",\"id\":\"toolu_X\",\"name\":\"Bash\"}]}'\n"
        "sleep 30\n",
    )
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=1,
        idle_grace_s=2,
        hard_max_s=3,    # absolute ceiling
        activity_event_substrings=['"type":"assistant"', '"type":"user"'],
        tool_pending_substrings=('"type":"tool_use"', '"type":"tool_result"'),
    )
    assert r.timed_out is True
    assert r.duration_s < 6     # killed near the 3s ceiling
    assert "killed: hard_max_s" in r.stderr


def test_no_done_substring_means_truly_stuck(tmp_path: Path):
    """Mirror case: agent never emitted a terminal-result event before
    being killed → stderr tag should mark as 'truly stuck' so postmortem
    knows this run probably needs a fix-rerun."""
    # Two assistant events to bypass idle for a bit, then silence forever.
    script = _write_script(
        tmp_path, "stuck.sh",
        "#!/bin/bash\n"
        "echo '{\"type\":\"assistant\",\"i\":1}'\n"
        "echo '{\"type\":\"assistant\",\"i\":2}'\n"
        "sleep 30\n",
    )
    r = run_process_with_watchdog(
        ["bash", str(script)],
        cwd=tmp_path,
        soft_timeout_s=2,
        idle_grace_s=2,
        hard_max_s=15,
        activity_event_substrings=['"type":"assistant"', '"type":"user"'],
        done_event_substring='"type":"result"',
    )
    assert r.timed_out is True
    assert "no terminal-result event seen" in r.stderr
