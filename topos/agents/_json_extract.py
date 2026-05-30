"""Shared JSON extraction helpers used across agents and critics.

Lives at the ``agents/`` layer (not inside ``agents/visual_critic/``) so the
``spec`` agent — which is a sibling, not a critic — can reuse the same
helpers without forming an awkward agents → agents/visual_critic dependency.

Primitives:
  - ``FENCE_RE``               — markdown code-fence stripper
  - ``try_load(s)``            — best-effort direct + fenced JSON parse
  - ``_extract_balanced_json`` — find the first ``{...}`` balanced substring
                                  respecting JSON string escapes

High-level:
  - ``extract_first_json_dict(text, required_keys)`` — recursive search that
    handles raw dicts, CLI envelopes (``result`` / ``response`` / ``output`` /
    ``content`` / ``messages[].text`` / ``events[].text``), fenced ``` ```json
    blocks, and free text containing a JSON object. The single helper used by
    both the spec agent and the CLI vision critic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable


FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def try_load(s: str) -> dict | None:
    """Best-effort: parse ``s`` as JSON directly, then unwrap a fenced code
    block. Returns the dict on success, ``None`` otherwise. Non-dict values
    (lists, scalars) are also treated as failure since every critic schema
    is dict-shaped at the top level.
    """
    s = s.strip()
    if not s:
        return None
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    m = FENCE_RE.search(s)
    if m:
        try:
            v = json.loads(m.group(1))
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _extract_balanced_json(s: str) -> str | None:
    """Find the first balanced ``{...}`` substring respecting JSON string escapes."""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


_ENVELOPE_STR_KEYS = ("result", "response", "output", "content")
_ENVELOPE_LIST_KEYS = ("messages", "events")


def extract_first_json_dict(
    text: str, *, required_keys: Iterable[str]
) -> dict | None:
    """Find the first JSON dict in ``text`` that contains all ``required_keys``.

    Strategy, in order:
      1. Direct ``json.loads(text)``. If the result is a dict containing every
         required key, return it. If it's an envelope dict missing them, recurse
         into known string-valued keys (``result`` / ``response`` / ``output`` /
         ``content``) and into known list-valued keys (``messages`` / ``events``,
         each item's ``text`` field).
      2. Strip a fenced ``` ```json ``` block and parse that.
      3. Balanced-brace scan from each ``{`` offset; the first parse that
         satisfies ``required_keys`` wins.

    ``required_keys`` is mandatory: without it, an outer envelope dict would
    be returned in step 1 instead of the inner payload. Pass the most
    distinctive top-level keys of the target schema (e.g. ``("per_criterion",)``
    for critic JSON, ``("slug", "intent_md")`` for spec JSON).
    """
    required = set(required_keys)
    if not required:
        raise ValueError("extract_first_json_dict requires non-empty required_keys")

    text = text.strip()
    if not text:
        return None

    def _matches(v: object) -> bool:
        return isinstance(v, dict) and required.issubset(v)

    # 1. Direct parse + envelope walk
    try:
        direct = json.loads(text)
    except json.JSONDecodeError:
        direct = None
    if _matches(direct):
        return direct  # type: ignore[return-value]
    if isinstance(direct, dict):
        for key in _ENVELOPE_STR_KEYS:
            inner = direct.get(key)
            if isinstance(inner, str):
                hit = extract_first_json_dict(inner, required_keys=required)
                if hit is not None:
                    return hit
        for key in _ENVELOPE_LIST_KEYS:
            arr = direct.get(key)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        hit = extract_first_json_dict(item["text"], required_keys=required)
                        if hit is not None:
                            return hit

    # 2. Fenced JSON
    m = FENCE_RE.search(text)
    if m:
        try:
            inner = json.loads(m.group(1))
        except json.JSONDecodeError:
            inner = None
        if _matches(inner):
            return inner  # type: ignore[return-value]

    # 3. Balanced-brace scan. Walk every '{' offset; for each, try to extract
    # a balanced object and parse it. When a parse succeeds (whether it
    # matches or not), skip past the entire blob to keep the scan O(n).
    cursor = 0
    n = len(text)
    while cursor < n:
        next_open = text.find("{", cursor)
        if next_open < 0:
            break
        blob = _extract_balanced_json(text[next_open:])
        if blob is None:
            break
        try:
            cand = json.loads(blob)
        except json.JSONDecodeError:
            cand = None
        if _matches(cand):
            return cand  # type: ignore[return-value]
        cursor = next_open + (len(blob) if cand is not None else 1)

    return None
