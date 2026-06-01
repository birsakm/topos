"""Subprocess helper with timeout, env injection, and bounded output capture.

Two timeout modes:

- ``run_process(... timeout_s=N)`` — classic HARD timeout via ``subprocess.run``.
  Kills the process at exactly N seconds regardless of whether the work is
  in progress. Use when the work is bounded and predictable (Blender renders,
  exporters).

- ``run_process_with_watchdog(... soft_timeout_s=N, idle_grace_s=G, ...)`` —
  soft timeout with activity-based extension. After N seconds, the process
  is only killed if it has been *idle* for ``idle_grace_s`` seconds.
  "Idle" = stdout/stderr byte count did not grow (or, with
  ``activity_event_substrings``, the count of meaningful events did not
  grow). Works for streaming CLIs like ``claude --output-format stream-json``,
  ``codex exec``, build/render subprocesses that print as they go.
  ``hard_max_s`` is an absolute ceiling regardless of activity.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool


def run_process(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_s: int | None = None,
    input_text: str | None = None,
) -> ProcessResult:
    """Run `cmd` and capture stdout/stderr as text. Inherits PATH unless `env` overrides.

    Behavior:
    - If `env` is given, it is merged onto os.environ (full replacement is rarely what callers want).
    - On timeout, the process is killed; returncode is set to -1 and timed_out=True.
    - stdout/stderr are decoded as utf-8 with errors='replace'.
    """
    merged_env = None
    if env is not None:
        merged_env = os.environ.copy()
        merged_env.update({k: str(v) for k, v in env.items()})

    start = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            env=merged_env,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
            errors="replace",
        )
        returncode = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = -1
        stdout = (exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
        stderr = (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))

    duration_s = time.monotonic() - start
    return ProcessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration_s,
        timed_out=timed_out,
    )


def run_process_with_watchdog(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    soft_timeout_s: int,
    idle_grace_s: int = 300,
    hard_max_s: int = 3600,
    input_text: str | None = None,
    progress_log_interval_s: int = 60,
    poll_interval_s: float = 2.0,   # how often the watchdog wakes to check
                                    # activity/timeouts; tests scale it down.
    activity_event_substrings: list[str] | None = None,
    activity_stderr_substrings: list[str] | None = None,
    tool_pending_substrings: tuple[str, str] | None = None,
    done_event_substring: str | None = None,
) -> ProcessResult:
    """Run ``cmd`` under a watchdog that distinguishes "slow but working"
    from "stuck or done".

    Termination conditions, in priority order:

    1. Process exits naturally (any returncode) → return normally.
    2. ``hard_max_s`` reached → kill regardless of activity.
       Default 1 hour. Absolute safety ceiling.
    3. ``soft_timeout_s`` reached AND no activity for ``idle_grace_s`` →
       kill. The process is considered "active" if stdout/stderr byte
       count grew since the last check (or, when ``activity_event_substrings``
       is set, the count of those substrings in stdout grew — see below).

    Activity signal modes:

    - **Default (byte growth)**: any new stdout/stderr byte counts. Works
      for blender/codex/gemini and any subprocess whose output correlates
      1:1 with progress.
    - **Event-aware (``activity_event_substrings``)**: count occurrences
      of these substrings in stdout instead of raw bytes. Use for
      stream-json-style CLIs where some events are heartbeats, not work.
      Example for claude ``--output-format stream-json``: pass
      ``['"type":"assistant"', '"type":"user"']`` — those are real work
      (model turn + tool result). ``"type":"rate_limit_event"`` and
      ``"type":"system"`` then DON'T register as activity, so a CLI stuck
      emitting only heartbeats will idle-trip the watchdog.

    Stderr-side activity (``activity_stderr_substrings``):

    Event-aware mode ignores stderr entirely — but some CLIs report
    transient-failure-and-retry on stderr only (gemini CLI prints
    ``Attempt N failed with status 503. Retrying with backoff`` to
    stderr while the API is overloaded). Without this signal those
    retry rounds look idle and the watchdog falsely kills a process
    that's still healthy. Pass the retry-shaped substrings here to keep
    them counted as activity even in event-aware mode.

    Tool-call awareness (``tool_pending_substrings``):

    An ``(open_substr, close_substr)`` pair. When set, the watchdog tracks
    ``count(open) - count(close)`` in stdout. When the count is > 0, the
    agent has emitted a tool call (e.g. a Bash invocation) and is waiting
    for the tool to return — *blocked, not idle*. In this state, idle-kill
    is suppressed; only ``hard_max_s`` will terminate the process.

    For claude ``--output-format stream-json``, pass
    ``('"type":"tool_use"', '"type":"tool_result"')``. Each assistant turn
    that calls a tool emits ``"type":"tool_use"``; the matching response
    from the framework emits ``"type":"tool_result"``. If the tool itself
    takes longer than ``idle_grace_s`` (e.g. a 10-minute Blender render
    spawned via Bash) the stream goes silent between the two — without
    this signal the watchdog would falsely conclude "agent is idle" and
    kill what's actually productive waiting.

    Optional ``done_event_substring``: a substring that, when seen anywhere
    in stdout, means the agent reported a terminal result (e.g.
    ``'"type":"result"'`` for claude). Doesn't change kill logic, but the
    fact is logged in the killed-stderr tag so postmortem can distinguish
    "agent reported done before kill" from "agent never reported done"
    (the first is usually a benign race, the second is a real wedge).

    Past ``soft_timeout_s`` a progress line is logged every
    ``progress_log_interval_s`` seconds so the operator (or a watching
    monitor) can see what's happening.

    Returns a ``ProcessResult`` with ``timed_out=True`` when the watchdog
    killed the process (either via idle-grace or hard-max ceiling).
    """
    merged_env = None
    if env is not None:
        merged_env = os.environ.copy()
        merged_env.update({k: str(v) for k, v in env.items()})

    start = time.monotonic()

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=merged_env,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    def _kill_proc_tree():
        # SIGKILL the entire process group so children (e.g., a bash script's
        # ``sleep`` child) don't outlive the parent and keep the stdout pipe
        # open. Without this, ``readline`` on the drain thread blocks waiting
        # for the grandchild to exit, even after we kill the immediate child.
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass

    # Feed stdin in a background thread (don't block the watchdog poll loop).
    if input_text is not None:
        def _feed():
            try:
                proc.stdin.write(input_text.encode("utf-8", errors="replace"))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        threading.Thread(target=_feed, daemon=True).start()

    # Drain stdout / stderr in background so the OS pipe buffer never fills
    # (which would block the child process).
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def _drain(stream, buf):
        # Read line-by-line so small/sparse output (a print every 0.5s)
        # flushes to our buffer promptly. ``stream.read(N)`` blocks waiting
        # for N bytes which can stall the watchdog's activity detection
        # for many seconds at low throughput.
        try:
            for line in iter(stream.readline, b""):
                buf.append(line)
        finally:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
    t_out.start()
    t_err.start()

    timed_out = False
    kill_reason = ""
    last_activity = time.monotonic()
    last_stream_bytes = 0
    last_event_count = 0
    next_progress_log = start + soft_timeout_s + progress_log_interval_s
    use_event_counter = bool(activity_event_substrings)

    def _count_events(buf: bytes) -> int:
        # Cheap substring count — avoids JSON-parsing the whole buffer on
        # every poll. Each meaningful stream-json line contains exactly one
        # ``"type":"..."`` occurrence, so summing the wanted substrings is
        # equivalent to "number of meaningful events seen so far".
        return sum(buf.count(s.encode("utf-8")) for s in activity_event_substrings)

    use_stderr_signals = bool(activity_stderr_substrings)
    _stderr_signals_b = (
        [s.encode("utf-8") for s in (activity_stderr_substrings or [])]
        if use_stderr_signals else []
    )

    def _count_stderr_signals(buf: bytes) -> int:
        return sum(buf.count(s) for s in _stderr_signals_b)

    last_stderr_signal_count = 0

    _tool_open_b = (
        tool_pending_substrings[0].encode("utf-8")
        if tool_pending_substrings else None
    )
    _tool_close_b = (
        tool_pending_substrings[1].encode("utf-8")
        if tool_pending_substrings else None
    )

    def _blocked_on_tool(buf: bytes) -> bool:
        # Pending tools = open events that haven't been closed yet. A long
        # external tool call (e.g. agent invokes ``Bash`` which runs Blender
        # for 10 min) makes the stream go silent between tool_use and
        # tool_result — counting tells us the agent is *waiting*, not idle.
        if _tool_open_b is None or _tool_close_b is None:
            return False
        return buf.count(_tool_open_b) > buf.count(_tool_close_b)

    while True:
        rc = proc.poll()
        if rc is not None:
            break  # natural exit

        now = time.monotonic()
        elapsed = now - start

        # 2. Hard ceiling.
        if elapsed > hard_max_s:
            kill_reason = f"hard_max_s={hard_max_s}s reached"
            timed_out = True
            _kill_proc_tree()
            break

        # 3. Soft timeout + idle grace check.
        # Activity signal: event-count growth (if substrings configured),
        # else raw byte growth.
        cur_bytes = sum(len(c) for c in stdout_chunks) + sum(len(c) for c in stderr_chunks)
        if use_event_counter:
            cur_event_count = _count_events(b"".join(stdout_chunks))
            stream_grew = cur_event_count > last_event_count
            cur_progress_metric: int | float = cur_event_count
        else:
            cur_event_count = 0
            stream_grew = cur_bytes > last_stream_bytes
            cur_progress_metric = cur_bytes
        # In event-aware mode, also count stderr retry markers as activity so
        # a CLI looping on transient 503/throttle (gemini-cli's Google API
        # retry-with-backoff lines) doesn't get killed for "idle stdout".
        stderr_signaled = False
        if use_stderr_signals:
            cur_stderr_sig = _count_stderr_signals(b"".join(stderr_chunks))
            stderr_signaled = cur_stderr_sig > last_stderr_signal_count
            last_stderr_signal_count = cur_stderr_sig
        if stream_grew or stderr_signaled:
            last_activity = now
            last_stream_bytes = cur_bytes
            last_event_count = cur_event_count

        if elapsed > soft_timeout_s:
            idle_for = now - last_activity
            blocked_on_tool = _blocked_on_tool(b"".join(stdout_chunks))
            if idle_for > idle_grace_s and not blocked_on_tool:
                kill_reason = (
                    f"idle {idle_for:.0f}s past soft_timeout_s={soft_timeout_s}s "
                    f"(idle_grace_s={idle_grace_s}s)"
                )
                timed_out = True
                _kill_proc_tree()
                break
            # Still active OR blocked on a long tool — log progress periodically.
            if now >= next_progress_log:
                metric_label = "events" if use_event_counter else "bytes"
                if blocked_on_tool:
                    print(
                        f"[watchdog] over soft_timeout ({elapsed:.0f}s / "
                        f"{soft_timeout_s}s) but agent is blocked on a pending "
                        f"tool call (idle_grace suppressed; only hard_max_s="
                        f"{hard_max_s}s will kill)"
                    )
                else:
                    print(
                        f"[watchdog] over soft_timeout ({elapsed:.0f}s / "
                        f"{soft_timeout_s}s) but still active: last activity "
                        f"{idle_for:.0f}s ago, stream {metric_label}={cur_progress_metric}"
                    )
                next_progress_log = now + progress_log_interval_s

        time.sleep(poll_interval_s)

    # Wait for drain threads to finish (process is gone, pipes will EOF).
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    proc.wait()

    duration_s = time.monotonic() - start
    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")

    if timed_out and kill_reason:
        # Tag the stderr tail so callers / postmortem can see WHY.
        suffix = ""
        if done_event_substring:
            seen_done = done_event_substring.encode("utf-8") in b"".join(stdout_chunks)
            # If the agent emitted its terminal-result event before we killed
            # it, this is almost always a benign race: the process was in the
            # last millisecond of normal teardown. Surface that so callers
            # (and the human reading stderr.log) can tell apart wedged-and-
            # silent from finished-and-about-to-exit.
            suffix = (
                " (agent reported terminal result before kill — likely benign race)"
                if seen_done
                else " (no terminal-result event seen — agent appears truly stuck)"
            )
        stderr = (stderr + f"\n[run_process_with_watchdog] killed: {kill_reason}{suffix}\n").rstrip() + "\n"

    return ProcessResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration_s,
        timed_out=timed_out,
    )
