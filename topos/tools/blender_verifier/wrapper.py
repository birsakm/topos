"""Inside-Blender buildability verification wrapper.

Invoked as:

    blender --background --python topos/tools/blender_verifier/wrapper.py -- \\
        --parts-dir <path-to-src/parts> \\
        --parts <Name1,Name2,...> \\
        --output-json <path-to-write-result.json>

For each requested part, this script:

1. Resets the Blender scene to a clean factory state (so no leak between parts)
2. Imports ``parts.<snake>`` from the given parts dir
3. Looks up ``build_<snake>`` and calls it
4. Asserts the return is a non-None ``bpy.types.Object`` of type ``MESH``
5. Records pass / fail + per-part error class, message, and full traceback

Always writes a structured result file (the framework reads this; non-zero
exit means catastrophic — partial failures are surfaced via the JSON, not
the exit code). The framework treats this as a LIGHT verify step that runs
BEFORE rendering — render is for rendering, not for catching code bugs.

This file MUST stay free of any ``topos`` imports — it runs in Blender's
bundled Python, not the host venv.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path

import bpy


def _pascal_to_snake(name: str) -> str:
    """PascalCase / mixed-case → snake_case, handling acronyms.

    Mirrors the render wrapper's two-pass conversion:
      1. ``(?<!^)(?=[A-Z][a-z])`` splits ``LPCompressor`` → ``LP_Compressor``
      2. ``(?<=[a-z0-9])(?=[A-Z])`` splits ``IntakeLip`` → ``Intake_Lip``

    Then lowercase + collapse doubled underscores."""
    s = re.sub(r'(?<!^)(?=[A-Z][a-z])', '_', name)
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', s).lower()
    s = re.sub(r'_+', '_', s)
    return s


def _parse_args() -> argparse.Namespace:
    if "--" not in sys.argv:
        raise SystemExit("verify_wrapper: no '--' separator in argv")
    raw = sys.argv[sys.argv.index("--") + 1:]
    p = argparse.ArgumentParser(prog="verify_wrapper")
    p.add_argument("--parts-dir", required=True,
                   help="directory containing parts/<lower>.py")
    p.add_argument("--parts", required=True,
                   help="comma-separated PascalCase part names")
    p.add_argument("--output-json", required=True,
                   help="absolute path where the per-part result JSON is written")
    return p.parse_args(raw)


def _verify_one(parts_dir: Path, name: str) -> dict:
    """Reset scene, import + build the named part, return a status record."""
    lower = _pascal_to_snake(name)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    record: dict = {"name": name, "lower_name": lower}

    # Import
    try:
        # Force a fresh import each time so a previously-loaded module
        # doesn't mask edits the fix-agent may have made.
        modname = f"parts.{lower}"
        if modname in sys.modules:
            del sys.modules[modname]
        module = __import__(modname, fromlist=[f"build_{lower}"])
    except Exception as e:
        record.update({
            "status": "failed",
            "stage": "import",
            "error_class": type(e).__name__,
            "error_msg": str(e),
            "traceback": traceback.format_exc(),
        })
        return record

    # Locate builder
    builder = getattr(module, f"build_{lower}", None)
    if builder is None:
        record.update({
            "status": "failed",
            "stage": "missing_builder",
            "error_class": "AttributeError",
            "error_msg": f"module parts.{lower} has no function build_{lower}()",
            "traceback": "",
        })
        return record

    # Call
    try:
        obj = builder()
    except Exception as e:
        record.update({
            "status": "failed",
            "stage": "build_call",
            "error_class": type(e).__name__,
            "error_msg": str(e),
            "traceback": traceback.format_exc(),
        })
        return record

    # Validate return
    if obj is None:
        record.update({
            "status": "failed",
            "stage": "null_return",
            "error_class": "ValueError",
            "error_msg": f"build_{lower}() returned None",
            "traceback": "",
        })
        return record
    if not hasattr(obj, "type"):
        record.update({
            "status": "failed",
            "stage": "wrong_type",
            "error_class": "TypeError",
            "error_msg": f"build_{lower}() returned non-Object of type {type(obj).__name__}",
            "traceback": "",
        })
        return record
    if obj.type != "MESH":
        record.update({
            "status": "failed",
            "stage": "wrong_type",
            "error_class": "TypeError",
            "error_msg": f"build_{lower}() returned obj.type={obj.type!r}, expected 'MESH'",
            "traceback": "",
        })
        return record

    # Success — capture a few cheap structural metrics for diagnostics
    try:
        n_verts = len(obj.data.vertices)
        n_polys = len(obj.data.polygons)
    except Exception:
        n_verts, n_polys = -1, -1

    record.update({
        "status": "passed",
        "vertex_count": n_verts,
        "polygon_count": n_polys,
    })
    return record


def main() -> int:
    args = _parse_args()
    parts_dir = Path(args.parts_dir).resolve()
    output_path = Path(args.output_json).resolve()
    src_dir = parts_dir.parent.resolve()
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    names = [n.strip() for n in args.parts.split(",") if n.strip()]
    results = [_verify_one(parts_dir, n) for n in names]

    summary = {
        "total": len(results),
        "passed_parts": [r["name"] for r in results if r.get("status") == "passed"],
        "failed_parts": [r for r in results if r.get("status") != "passed"],
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    n_passed = len(summary["passed_parts"])
    n_failed = len(summary["failed_parts"])
    print(f"[verify_wrapper] {n_passed}/{len(names)} part(s) verified OK; "
          f"{n_failed} failed. Result: {output_path}")
    # Exit 0 even on partial failure — the framework reads the JSON to
    # decide whether to trigger a runtime fix-loop. Hard-fail only on a
    # catastrophic situation (e.g. parts_dir not readable), which would
    # have raised earlier.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
