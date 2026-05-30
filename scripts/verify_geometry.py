"""Standalone CLI wrapper for the ``verify_geometry`` tool.

Run against any project workspace to get a deterministic numeric audit
of its ``src/design.json``:

  python scripts/verify_geometry.py outputs/cab_gemini_flash_palace5

Returns exit 0 when all assertions pass, exit 1 when any fail. Use in
CI / batch scripts to gate runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from topos.tools.registry import _ensure_default_tools_imported, get


def main(workspace: Path) -> int:
    _ensure_default_tools_imported()
    spec = get("verify_geometry")
    out = spec.func(workspace=str(workspace))
    total = out["total"]
    failed = out["failed_parts"]
    passed = out["passed_assertions"]
    print(f"=== verify_geometry: {workspace.name} ===")
    print(f"  passed: {len(passed)} / {total}")
    print(f"  failed: {len(failed)}")
    if passed:
        for a in passed:
            print(f"    ✓ {a}")
    if failed:
        print()
        for fp in failed:
            print(f"  ✗ [{fp['stage']}] {fp['name']}")
            print(f"      {fp['error_msg']}")
    return 0 if out["success"] else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/verify_geometry.py <workspace>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1]).resolve()))
