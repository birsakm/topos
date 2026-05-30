"""Inside-Blender introspection script.

Runs as ``blender --background --python topos/bpy_docs/introspect.py -- --output <path.json>``.
Walks the installed Blender's exposed Python API and dumps a JSON index
keyed by dotted-path symbol → {kind, signature, short_doc, long_doc}.

The index is pinned to whatever Blender version is invoking this script,
so the API surface matches the user's actual `bpy` runtime.

No ``topos`` imports — runs in Blender's bundled Python.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import time
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    if "--" not in sys.argv:
        raise SystemExit("docs_introspect: no '--' separator in argv")
    raw = sys.argv[sys.argv.index("--") + 1:]
    p = argparse.ArgumentParser(prog="docs_introspect")
    p.add_argument("--output", required=True, help="Output path for the JSON index")
    p.add_argument("--include-bpy-types", action="store_true",
                   help="Also walk bpy.types (huge — ~thousands of classes). Off by default.")
    p.add_argument("--max-doc-chars", type=int, default=2000,
                   help="Truncate each long_doc field to this many chars")
    return p.parse_args(raw)


def _short_doc(text: str) -> str:
    if not text:
        return ""
    # First paragraph (split on empty line)
    parts = text.strip().split("\n\n", 1)
    return parts[0].strip().replace("\n", " ")[:300]


def _index_bpy_ops(index: dict, max_doc: int) -> None:
    """Walk bpy.ops.<module>.<op_name>. Each is a callable with __doc__."""
    import bpy
    for mod_name in dir(bpy.ops):
        if mod_name.startswith("_"):
            continue
        try:
            mod = getattr(bpy.ops, mod_name)
        except Exception:
            continue
        for op_name in dir(mod):
            if op_name.startswith("_"):
                continue
            try:
                op = getattr(mod, op_name)
            except Exception:
                continue
            if not callable(op):
                continue
            full = f"bpy.ops.{mod_name}.{op_name}"
            doc = op.__doc__ or ""
            index[full] = {
                "kind": "op",
                "signature": _extract_op_signature(doc),
                "short_doc": _short_doc(doc),
                "long_doc": doc[:max_doc],
            }


def _extract_op_signature(doc: str) -> str:
    """bpy ops docstrings start with a signature line like
    ``primitive_cube_add(size=2.0, calc_uvs=True, ..., location=(0,0,0), ...)``."""
    if not doc:
        return ""
    first_line = doc.strip().split("\n", 1)[0]
    return first_line[:400]


def _index_module(index: dict, module, prefix: str, max_doc: int, max_depth: int = 2) -> None:
    """Walk a module's top-level callables. ``max_depth=1`` keeps it shallow."""
    if max_depth <= 0:
        return
    for name in dir(module):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(module, name)
        except Exception:
            continue
        full = f"{prefix}.{name}"
        if callable(attr) and not inspect.ismodule(attr) and not inspect.isclass(attr):
            doc = attr.__doc__ or ""
            try:
                sig = str(inspect.signature(attr))
            except (TypeError, ValueError):
                sig = "(...)"
            index[full] = {
                "kind": "function",
                "signature": f"{name}{sig}",
                "short_doc": _short_doc(doc),
                "long_doc": doc[:max_doc],
            }
        elif inspect.isclass(attr):
            doc = attr.__doc__ or ""
            index[full] = {
                "kind": "class",
                "signature": full,
                "short_doc": _short_doc(doc),
                "long_doc": doc[:max_doc],
            }
            # Walk one level into class methods
            if max_depth > 1:
                for mname in dir(attr):
                    if mname.startswith("_"):
                        continue
                    try:
                        method = getattr(attr, mname)
                    except Exception:
                        continue
                    if not callable(method):
                        continue
                    mfull = f"{full}.{mname}"
                    mdoc = method.__doc__ or ""
                    try:
                        msig = str(inspect.signature(method))
                    except (TypeError, ValueError):
                        msig = "(...)"
                    index[mfull] = {
                        "kind": "method",
                        "signature": f"{mname}{msig}",
                        "short_doc": _short_doc(mdoc),
                        "long_doc": mdoc[:max_doc],
                    }


def _index_bmesh_ops(index: dict, max_doc: int) -> None:
    import bmesh
    if not hasattr(bmesh, "ops"):
        return
    for name in dir(bmesh.ops):
        if name.startswith("_"):
            continue
        try:
            op = getattr(bmesh.ops, name)
        except Exception:
            continue
        if not callable(op):
            continue
        full = f"bmesh.ops.{name}"
        doc = op.__doc__ or ""
        index[full] = {
            "kind": "bmesh_op",
            "signature": _extract_op_signature(doc) or name,
            "short_doc": _short_doc(doc),
            "long_doc": doc[:max_doc],
        }


def main() -> int:
    args = _parse_args()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    import bpy
    blender_version = ".".join(str(x) for x in bpy.app.version)
    started = time.monotonic()

    index: dict = {}
    print(f"[docs_introspect] indexing bpy.ops...")
    _index_bpy_ops(index, args.max_doc_chars)
    print(f"  + {len(index)} bpy ops")

    n_ops = len(index)
    print(f"[docs_introspect] indexing bmesh.ops...")
    _index_bmesh_ops(index, args.max_doc_chars)
    print(f"  + {len(index) - n_ops} bmesh ops")

    n_before = len(index)
    print(f"[docs_introspect] indexing mathutils...")
    import mathutils
    _index_module(index, mathutils, "mathutils", args.max_doc_chars, max_depth=2)
    print(f"  + {len(index) - n_before} mathutils symbols")

    if args.include_bpy_types:
        n_before = len(index)
        print(f"[docs_introspect] indexing bpy.types (large)...")
        _index_module(index, bpy.types, "bpy.types", args.max_doc_chars, max_depth=1)
        print(f"  + {len(index) - n_before} bpy.types symbols")

    doc = {
        "blender_version": blender_version,
        "generated_at_unix": int(time.time()),
        "duration_s": round(time.monotonic() - started, 2),
        "symbol_count": len(index),
        "symbols": index,
    }
    out_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"[docs_introspect] wrote {out_path} — {len(index)} symbols, Blender {blender_version}, {out_path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
