"""bpy docs RAG — local Blender introspection index + search.

The index is built once by `topos bpy-docs index` (invokes Blender to walk
its own Python API and dump JSON). Stored at the path in
``config.bpy_docs.index_path`` (default ``~/.config/topos/bpy_docs.json``).
The ``topos bpy-docs search`` CLI (agent-facing, run via Bash) queries this
index through ``search()`` below — keyword + substring ranking for now;
embedding-based ranking can be added later.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import config as cfg


def index_path() -> Path:
    """Resolve the index JSON path from config."""
    effective = cfg.load_effective_config()
    raw = (effective.get("bpy_docs") or {}).get("index_path")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".config" / "topos" / "bpy_docs.json"


def load_index() -> dict:
    p = index_path()
    if not p.is_file():
        raise FileNotFoundError(
            f"bpy docs index not found at {p}. "
            f"Run `topos bpy-docs index` first."
        )
    return json.loads(p.read_text(encoding="utf-8"))


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def search(query: str, top_k: int = 5, *, kinds: list[str] | None = None) -> list[dict]:
    """Return the top-k matching symbols for ``query``.

    Ranking: name substring matches count more than docstring matches.
    Cheap heuristic; replace with embeddings later if it proves
    insufficient.
    """
    idx = load_index()
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []

    scored: list[tuple[float, str, dict]] = []
    for symbol, info in idx.get("symbols", {}).items():
        if kinds and info.get("kind") not in kinds:
            continue
        name_lower = symbol.lower()
        doc_lower = (info.get("short_doc") or "").lower() + " " + (info.get("long_doc") or "").lower()
        score = 0.0
        for tok in q_tokens:
            if tok in name_lower:
                score += 5.0
                # bonus if the token IS the trailing piece of the dotted path
                if name_lower.endswith("." + tok) or name_lower.endswith(tok):
                    score += 3.0
            if tok in doc_lower:
                score += 1.0
        if score > 0:
            scored.append((score, symbol, info))

    scored.sort(key=lambda x: -x[0])
    return [
        {"symbol": s, "score": round(score, 2), **info}
        for score, s, info in scored[:top_k]
    ]
