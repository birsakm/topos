"""Filesystem mtime-diff helpers — used to detect which files a subprocess
wrote / modified during its run.

Lifted out of three identical copies in ``backends/*_cli.py`` and one in
``tools/_blender_subprocess.py``. The contract is the same in every caller:

    before = snapshot_mtimes(root)
    ... run subprocess that may write under root ...
    artifacts = new_or_modified(root, before)
"""

from __future__ import annotations

from pathlib import Path


# Guards against floating-point equality noise when comparing a file's mtime
# before vs after — NOT against filesystem coarseness (real FS resolutions
# are orders of magnitude coarser than this and don't affect detection of
# actual writes). A 1μs threshold is well below any real write interval.
MTIME_EPSILON = 1e-6


def snapshot_mtimes(root: Path) -> dict[Path, float]:
    """Map every regular file under ``root`` to its current mtime."""
    return {p: p.stat().st_mtime for p in root.rglob("*") if p.is_file()}


def new_or_modified(root: Path, before: dict[Path, float]) -> list[Path]:
    """Files under ``root`` that are new or whose mtime advanced past
    ``before``. Sorted by path so callers get deterministic output."""
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        prev = before.get(p)
        if prev is None or p.stat().st_mtime > prev + MTIME_EPSILON:
            out.append(p)
    return sorted(out)
