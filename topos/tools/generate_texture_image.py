"""``generate_texture_image`` tool: read ``src/design.json``, look up the
given part's ``texture`` spec, and materialize the PNG via an
``ImageGenBackend`` (default Gemini Nano Banana 2).

Image generation is the DEFAULT and only texture path — geometry and texture
are fully decoupled. The design agent authors ``parts[i].texture.prompt`` in
design.json (the authoritative source for the look); the part's geometry code
never touches texture and there is no part-authored ``texture_<name>()``.

Dispatch on ``texture.prompt``:
  - prompt present — call backend with ``prompt`` → save PNG at the DERIVED
                     path ``src/textures/<snake(part_name)>.png`` (the same
                     stem ``build.py``'s ``_apply_texture`` UV-binds at build
                     time). Any design-supplied ``image_relpath`` is ignored.
  - no prompt      — no-op, ``success=True``, ``cost_usd=0`` (the part is left
                     flat; ``build.py`` renders it in ``color_rgba``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..agents.image_gen.base import make_backend
from ._paths import resolve_under_workspace
from .registry import tool


def _snake(name: str) -> str:
    """``SeatPost`` → ``seat_post``. Identical to
    ``orchestrator.expand._camel_to_snake`` (kept inline to avoid a tools→
    orchestrator import). This is the canonical part-name→file-stem transform,
    so the derived ``src/textures/<snake>.png`` matches what ``build.py``'s
    ``_apply_texture`` globs for (it derives the same stem from the part's
    ``build_<snake>`` function name)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _no_op(kind: str, note: str) -> dict[str, Any]:
    """Shape for the 'nothing to bill, nothing to do' return — procedural
    textures, parts with no texture spec, etc. Kept structurally identical
    to the success/failure shapes so run_report aggregation doesn't have
    to special-case missing fields."""
    return {
        "success": True,
        "kind": kind,
        "image_path": "",
        "byte_size": 0,
        "duration_s": 0.0,
        "model": "",
        "cost_usd": 0.0,
        "usage": {"model": "", "n_images": 0},
        "note": note,
    }


def _degraded(error: str, *, duration_s: float = 0.0,
              model: str = "") -> dict[str, Any]:
    """Shape for 'tried to generate, didn't make it' — image-gen API failure,
    backend init error, missing API key.

    Returns ``success=True`` ON PURPOSE: image texture is BEST-EFFORT, flat
    ``color_rgba`` is the floor. ``build.py``'s ``_apply_texture`` already falls
    back to flat when the PNG is absent, so a degraded texture must NOT fail the
    DAG — otherwise (since image-gen is now the default for every part) a single
    transient 429 would abort the whole build via the subgraph's
    ``all(child.success)`` rule. The failure is surfaced loudly via the
    ``error``/``note`` fields + a stderr line (CLAUDE.md rule #12), not by
    crashing the run. cost_usd is 0 (Gemini bills per returned image)."""
    import sys
    print(f"[TEXTURE_DEGRADED] image-gen failed; part left flat: {error}", file=sys.stderr)
    return {
        "success": True,
        "kind": "degraded",
        "image_path": "",
        "byte_size": 0,
        "duration_s": duration_s,
        "model": model,
        "cost_usd": 0.0,
        "usage": {"model": model, "n_images": 0},
        "error": error,
        "note": "image-gen failed; part rendered flat (color_rgba). See error.",
    }


@tool(
    "generate_texture_image",
    description=(
        "Generate the texture image for a part. Reads ``texture.prompt`` from "
        "``src/design.json[parts.<part_name>.texture]`` (authored by the design "
        "agent). If a prompt is present it calls the configured ImageGenBackend "
        "(Gemini Nano Banana 2 by default) and writes the PNG to the derived "
        "path ``src/textures/<snake(part_name)>.png`` (which build.py UV-binds "
        "at build time). If there is no prompt the tool returns success with "
        "cost_usd=0 and does nothing — the part is left flat (color_rgba)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "part_name": {
                "type": "string",
                "description": (
                    "PascalCase part name; must match a ``parts[].name`` "
                    "entry in design.json. The design agent's per-part "
                    "texture spec is looked up by this name."
                ),
            },
            "design_relpath": {
                "type": "string",
                "default": "src/design.json",
                "description": "Where to read the design contract from.",
            },
            "backend": {
                "type": "string",
                "description": (
                    "Optional ImageGenBackend override (default = "
                    "config.image_gen.default = 'gemini'). 'stub' is gated "
                    "to prevent agents from accidentally producing noise."
                ),
            },
            "timeout_s": {"type": "integer", "default": 180, "minimum": 5, "maximum": 600},
        },
        "required": ["workspace", "part_name"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "kind": {
                "type": "string",
                "description": "'image' when a PNG was generated, 'flat' when the part had no texture.prompt (left to color_rgba).",
            },
            "image_path": {"type": "string", "description": "Where the PNG landed (workspace-relative). Empty for non-image kinds and failures."},
            "byte_size": {"type": "integer"},
            "duration_s": {"type": "number"},
            "model": {"type": "string"},
            "cost_usd": {
                "type": "number",
                "description": "USD cost of this image-gen call. Picked up by the runner and surfaced in TaskResult.cost_usd / run_report.json. 0.0 for non-image kinds and failures (Gemini bills per successfully returned image).",
            },
            "usage": {
                "type": "object",
                "description": "Per-call usage record: model + image count. 0 images for procedural / failed.",
            },
            "note": {"type": "string", "description": "Set when no image-gen happened (procedural / missing); explains why."},
            "error": {"type": "string", "description": "Set when image-gen failed; never raises for runtime failures so the run can continue with build's flat-color fallback."},
        },
    },
    side_effects=True,
)
def generate_texture_image(
    *,
    workspace: str,
    part_name: str,
    design_relpath: str = "src/design.json",
    backend: str | None = None,
    timeout_s: int = 180,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    design_path = resolve_under_workspace(ws, design_relpath, label="design_relpath")
    # design.json missing or malformed is a plan-level bug (texture task
    # scheduled before design agent ran, or design agent failed silently);
    # raise so the runner surfaces it loudly rather than masking as "no
    # texture needed". Runtime image-gen failures still return success=False.
    if not design_path.is_file():
        raise FileNotFoundError(
            f"{design_relpath} not found at {design_path}. The texture task "
            f"depends on design.json being written first."
        )
    try:
        design = json.loads(design_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FileNotFoundError(
            f"{design_relpath} is not valid JSON: {e}"
        ) from None
    if not isinstance(design, dict):
        raise ValueError(
            f"{design_relpath} must be a JSON object, got {type(design).__name__}"
        )
    parts = design.get("parts") or []
    part = next((p for p in parts if p.get("name") == part_name), None)
    if part is None:
        raise ValueError(
            f"part {part_name!r} not in {design_relpath}; "
            f"available parts: {[p.get('name') for p in parts]}"
        )

    tex = part.get("texture") or {}
    prompt = tex.get("prompt")

    # Image generation is the default and the ONLY image path: a part with a
    # texture.prompt gets a generated PNG; a part with no prompt is left flat
    # (build.py's _apply_texture falls back to color_rgba). There is no longer a
    # `kind` field or a part-authored texture_<name>() — geometry and texture are
    # fully decoupled (the design agent owns the look via texture.prompt here).
    if not prompt:
        return _no_op(
            kind="flat",
            note=(
                f"part {part_name!r}: no texture.prompt — left flat (build.py "
                f"renders it in color_rgba)"
            ),
        )

    # Path is DERIVED, not read from design.json — src/textures/<snake>.png —
    # the same stem build.py's _apply_texture globs for. Any design-supplied
    # image_relpath is ignored on purpose so the two sides can't drift.
    image_relpath = f"src/textures/{_snake(part_name)}.png"
    out_path = resolve_under_workspace(ws, image_relpath, label="image_relpath")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        impl = make_backend(backend)
    except (ValueError, RuntimeError) as e:
        return _degraded(str(e))

    # Optional size override (some parts may want non-1024 — flag in design.json).
    size = int(tex.get("size") or 1024)

    result = impl.generate(prompt, size=size, timeout_s=timeout_s)
    if not result.success:
        return _degraded(
            result.error or "unknown image-gen failure",
            duration_s=result.duration_s,
            model=result.model,
        )

    out_path.write_bytes(result.png_bytes)
    return {
        "success": True,
        "kind": "image",
        "image_path": str(out_path.relative_to(ws)),
        "byte_size": len(result.png_bytes),
        "duration_s": result.duration_s,
        "model": result.model,
        "cost_usd": float(result.cost_usd or 0.0),
        "usage": {"model": result.model, "n_images": 1},
    }
