"""Blender execution. Default is a stateless ``blender --background --python``
subprocess per call; hot-pool path is reserved for a later opt-in cache.

Reproducibility (ADR 0002): hot-pool results must match the stateless path
identically; integration tests always run with ``hot_pool=False``.

Artifact detection: any file in ``cwd`` whose mtime is newer than the start of
the call (or that didn't exist before) is reported as an artifact. This is the
contract tools rely on for ``BlenderResult.artifacts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import config as cfg
from .._fs_diff import new_or_modified, snapshot_mtimes
from ..process import run_process


@dataclass
class BlenderResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float
    timed_out: bool
    artifacts: list[Path] = field(default_factory=list)


def _repo_root() -> Path:
    """Repo root = the parent of the installed ``topos`` package. For an
    editable (``pip install -e .``) checkout this is the project directory, so
    a vendored ``./vendor/blender/`` resolves correctly regardless of cwd."""
    return Path(__file__).resolve().parents[2]


def resolve_blender_path(raw: str) -> str:
    """Normalise a configured blender-binary value. Exactly two forms:

    - an ABSOLUTE path is used as-is;
    - anything else is PROJECT-RELATIVE and resolved against the repo root
      (e.g. ``./vendor/blender/blender``), so a vendored Blender works from
      any cwd.
    """
    p = Path(raw)
    return str(p if p.is_absolute() else (_repo_root() / p).resolve())


def resolve_blender_binary(explicit: str | None = None) -> str:
    raw = explicit or (cfg.load_effective_config().get("blender") or {}).get("binary")
    if not raw:
        raise RuntimeError(
            "blender.binary not configured; run "
            "`topos config set blender.binary <path>` or set TOPOS__BLENDER__BINARY"
        )
    return resolve_blender_path(raw)


def run_blender(
    script: Path,
    *,
    cwd: Path,
    hot_pool: bool = False,
    env: dict[str, str] | None = None,
    timeout_s: int = 120,
    script_args: list[str] | None = None,
    binary: str | None = None,
) -> BlenderResult:
    """Run ``blender --background --python <script>`` inside ``cwd``.

    Anything appended after ``--`` is delivered to the script as ``sys.argv``.
    The current implementation always runs stateless; ``hot_pool=True`` raises
    ``NotImplementedError`` so callers fail loudly when the optimization is
    requested before it exists.
    """
    if hot_pool:
        raise NotImplementedError(
            "hot-pool execution is reserved for later; pass hot_pool=False"
        )
    if not script.is_file():
        raise FileNotFoundError(f"blender script not found: {script}")
    cwd = cwd.resolve()
    if not cwd.is_dir():
        raise NotADirectoryError(f"cwd is not a directory: {cwd}")

    bin_path = resolve_blender_binary(binary)
    cmd = [bin_path, "--background", "--python", str(script)]
    if script_args:
        cmd.append("--")
        cmd.extend(script_args)

    before = snapshot_mtimes(cwd)
    result = run_process(cmd, cwd=cwd, env=env, timeout_s=timeout_s)
    artifacts = new_or_modified(cwd, before)

    return BlenderResult(
        success=(result.returncode == 0 and not result.timed_out),
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        duration_s=result.duration_s,
        timed_out=result.timed_out,
        artifacts=artifacts,
    )
