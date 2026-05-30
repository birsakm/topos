"""Workspace lifecycle for produced projects.

Each ``Workspace`` is an ``outputs/<slug>/`` directory containing the spec, the
rendered plan, the agent-written ``src/`` tree, derived ``artifacts/``, and
per-task ``trajectories/<task_id>/`` directories. ``outputs/`` is the single
drop zone — runs write here directly; failed / exploratory runs are pruned
manually (``rm -rf outputs/<slug>/``).

The standalone-output invariant (ADR 0001 + 0004) means ``src/`` and
``vendored/`` are the only directories that ship with a frozen project.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug {slug!r}; must match {_SLUG_RE.pattern} "
            "(lowercase alnum, _, -; ≤64 chars, must start with alnum)"
        )


def default_outputs_base(start: Path | None = None) -> Path:
    """Where to put new project workspaces. Defaults to ``./outputs/`` of cwd."""
    return (start or Path.cwd()) / "outputs"


@dataclass
class Workspace:
    root: Path
    slug: str

    # --- factories ---

    @classmethod
    def create(
        cls,
        slug: str,
        domain: str,
        *,
        base: Path | None = None,
        exist_ok: bool = False,
    ) -> "Workspace":
        _validate_slug(slug)
        if domain == "":
            raise ValueError("domain must be a non-empty string")
        base = base or default_outputs_base()
        root = base / slug
        if root.exists():
            if not exist_ok:
                raise FileExistsError(f"workspace already exists: {root}")
        else:
            root.mkdir(parents=True)
        for sub in ("src", "artifacts", "trajectories"):
            (root / sub).mkdir(exist_ok=True)
        ws = cls(root=root, slug=slug)
        if not ws.manifest_path.exists():
            ws.write_manifest({
                "slug": slug,
                "domain": domain,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "frozen": False,
                "schema_version": 1,
            })
        return ws

    @classmethod
    def locate(cls, slug: str, *, base: Path | None = None) -> "Workspace":
        _validate_slug(slug)
        base = base or default_outputs_base()
        root = base / slug
        if not (root / "manifest.json").is_file():
            raise FileNotFoundError(f"no workspace at {root} (manifest.json missing)")
        return cls(root=root, slug=slug)

    # --- paths ---

    @property
    def src_dir(self) -> Path:
        return self.root / "src"

    @property
    def trajectories_root(self) -> Path:
        return self.root / "trajectories"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def spec_path(self) -> Path:
        return self.root / "spec.yaml"

    @property
    def plan_path(self) -> Path:
        return self.root / "plan.json"

    def trajectory_dir(self, task_id: str) -> Path:
        d = self.trajectories_root / task_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- manifest ---

    def manifest(self) -> dict:
        if not self.manifest_path.is_file():
            return {}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def write_manifest(self, data: dict) -> None:
        self.manifest_path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")
