"""Unit tests for the bpy docs RAG index + search.

These tests require an existing index at the configured location (built
once by `topos bpy-docs index`). If the index isn't present, the tests
skip rather than failing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.bpy_docs import index_path, load_index, search


def _has_index() -> bool:
    return index_path().is_file()


pytestmark = pytest.mark.skipif(not _has_index(),
                                  reason="bpy docs index not built; run `topos bpy-docs index`")


def test_index_has_expected_shape():
    idx = load_index()
    assert isinstance(idx, dict)
    assert "blender_version" in idx
    assert "symbols" in idx
    assert idx["symbol_count"] > 100, "index suspiciously small"


def test_search_primitive_cube_ranks_correct_op_first():
    results = search("primitive cube", top_k=3)
    assert results, "expected at least one match for 'primitive cube'"
    top = results[0]
    assert "primitive_cube_add" in top["symbol"], top["symbol"]
    assert top["kind"] == "op"
    assert "size=" in top["signature"], "signature should include parameters"


def test_search_kinds_filter():
    results = search("cube", top_k=10, kinds=["bmesh_op"])
    # All results, if any, should be bmesh_op
    for r in results:
        assert r["kind"] == "bmesh_op", f"got {r['kind']} for {r['symbol']}"


def test_search_empty_query_returns_nothing():
    assert search("", top_k=5) == []


def test_search_returns_short_doc_not_long_doc():
    results = search("transform apply", top_k=3)
    if results:
        # short_doc should be present and reasonably short
        assert "short_doc" in results[0]
        assert len(results[0]["short_doc"]) <= 320  # 300 cap + small overhead
