"""Shared helper for surfacing build-time geometry-contract warnings.

Background: ``topos_geometry_contracts`` and similar SKILLs instruct the
build agent to embed validators in ``src/build.py`` that print tagged
warnings like ``[ATTACHMENT_WARN]``, ``[COLLISION_WARN]``, ``[HOLLOW_WARN]``,
``[FIT_WARN]`` when something is geometrically off. These print into
Blender's stdout when ``build.py`` executes.

Each tool that runs ``build.py`` (export_glb, render_multiview, render
single, render_part) captures stdout as a tail of the last 2000-4000
chars — but Blender's gltf exporter and material loader spam INFO lines
*after* the contracts run, which routinely pushes the warnings out of
the tail window. Result: contracts fire correctly but the warnings
never reach the structured tool output that fix-loop agents inspect.

The fix is small: regardless of tail truncation, scan the full stdout
buffer for lines tagged ``*_WARN]`` and lift them into a dedicated
``warnings: list[str]`` field on the tool result. The line content is
preserved verbatim so the fix agent can read joint names + gap measures
straight from the prose."""

from __future__ import annotations

import re

# Tag tokens that contract code emits. Match the literal ``[...]_WARN]`` shape
# at the start of a line (after optional whitespace from Blender prefixing) so
# regular ``WARN`` words inside log prose don't false-positive.
_WARN_RE = re.compile(
    r"^\s*\[(?:[A-Z][A-Z0-9_]*_WARN)\][^\n]*",
    re.MULTILINE,
)


def extract_contract_warnings(stdout: str) -> list[str]:
    """Pull every ``[*_WARN] ...`` line out of the captured stdout.

    Returned in source order. Whitespace is stripped per-line to canonicalize
    output. Returns an empty list when stdout has no tagged warnings.
    """
    if not stdout:
        return []
    return [m.group(0).strip() for m in _WARN_RE.finditer(stdout)]
