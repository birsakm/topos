"""``bpy_docs_search`` tool: query the local bpy docs index for matching
symbols. Used by agents that need to verify an API signature or learn
about a less common op (bmesh, mathutils, etc.).

The index is version-pinned to the user's installed Blender (built once
via ``topos bpy-docs index``).
"""

from __future__ import annotations

from typing import Any

from ..bpy_docs import search as _docs_search
from .registry import tool


@tool(
    "bpy_docs_search",
    description=(
        "Search the local Blender Python API docs (bpy.ops, bmesh.ops, "
        "mathutils — version-pinned to the installed Blender). Use when "
        "you need to verify an exact API signature or discover a less "
        "common op. Returns ranked matches with signature + docstring."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language query or symbol fragment, e.g. 'bevel modifier' or 'primitive cube' or 'transform apply'",
            },
            "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
            "kinds": {
                "type": "array",
                "items": {"type": "string", "enum": ["op", "bmesh_op", "class", "method", "function"]},
                "description": "Restrict results to these symbol kinds. Default: all.",
            },
        },
        "required": ["query"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "kind": {"type": "string"},
                        "score": {"type": "number"},
                        "signature": {"type": "string"},
                        "short_doc": {"type": "string"},
                    },
                },
            },
            "error": {"type": "string"},
        },
    },
    side_effects=False,
)
def bpy_docs_search(*, query: str, top_k: int = 5, kinds: list[str] | None = None,
                     workspace: str | None = None) -> dict[str, Any]:
    """Look up matching bpy / bmesh / mathutils symbols."""
    try:
        matches = _docs_search(query, top_k=top_k, kinds=kinds)
    except FileNotFoundError as e:
        return {"success": False, "matches": [], "error": str(e)}
    # Strip the long_doc from results — agents can request a specific
    # symbol's full doc via a follow-up call if needed
    out = []
    for m in matches:
        out.append({
            "symbol": m["symbol"],
            "kind": m["kind"],
            "score": m["score"],
            "signature": m.get("signature", ""),
            "short_doc": m.get("short_doc", ""),
        })
    return {"success": True, "matches": out}
