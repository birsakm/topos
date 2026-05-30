"""Shared success-judgment for tools that exec user-supplied Python inside
a Blender subprocess.

The naive ``ok = (returncode == 0)`` fails silently in a specific way:
Blender's ``--background --python script.py`` exits 0 even when ``script.py``
raises a Python exception. The exception's traceback goes to stderr; stdout
typically shows ``Blender quit`` and nothing else. The wrapper then returns
``success=True`` despite producing zero output.

Downstream tasks then trust that success, dispatch on it, and crash later
when they try to consume artifacts that don't exist.

The detector here adds two signals:

  1. A Python traceback marker in stderr (``Traceback (most recent call last):``)
     — definitive evidence the agent's script raised.
  2. An artifact count check — if the tool *was supposed to* produce files
     and zero were produced, that's effectively a silent failure regardless
     of exit code.

Both signals are necessary because:
  - exit_code alone misses traceback-but-clean-exit.
  - traceback alone misses cases where the script silently produces no
    output without raising (e.g. early return before the export step).
  - artifact count alone misses cases where the script does produce SOME
    files but raises after, leaving partial output.
"""

from __future__ import annotations

import re


_PY_TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\):", re.MULTILINE)


def stderr_has_python_crash(stderr: str | None) -> bool:
    """True when stderr contains the canonical Python traceback header.

    Conservative: matches only ``Traceback (most recent call last):`` at the
    start of a line. Avoids false positives from log prose mentioning the
    word ``traceback``. Blender itself uses this exact phrase when a script
    raises, and so does cpython's default exception handler — that's our
    target.
    """
    if not stderr:
        return False
    return bool(_PY_TRACEBACK_RE.search(stderr))


def judge_subprocess_success(
    *,
    returncode: int,
    timed_out: bool,
    stderr: str | None,
    artifacts: list | None = None,
    expects_artifacts: bool = True,
) -> bool:
    """Return True only when all four signals agree the subprocess succeeded.

    Args:
        returncode: Process exit code (0 = exit-clean).
        timed_out: Whether the process was killed for exceeding its budget.
        stderr: Captured stderr buffer (we scan the full text, not just the
            tail — a traceback near the start is the canonical signal).
        artifacts: List of output files the wrapper produced. Pass ``None``
            if the tool isn't artifact-producing.
        expects_artifacts: When True, an empty artifacts list (despite
            ``returncode == 0``) is treated as failure. Set False for tools
            whose primary effect is in-memory or where empty output is valid.

    The judgment is short-circuit AND. The order matches diagnostic value:
    timeout/exit_code first (loud), then crash detector (silent failure
    inside the subprocess), then artifact count (silent failure that didn't
    raise).
    """
    if timed_out:
        return False
    if returncode != 0:
        return False
    if stderr_has_python_crash(stderr):
        return False
    if expects_artifacts and not artifacts:
        return False
    return True
