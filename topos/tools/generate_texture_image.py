"""``generate_texture_image`` tool: read ``src/design.json``, look up the
given part's ``texture`` spec, and materialize the PNG via an
``ImageGenBackend`` (default Gemini Nano Banana 2).

design.json is the authoritative source for what each part should look like
— the design agent already wrote ``parts[i].texture.prompt`` and
``parts[i].texture.image_relpath``. This tool just resolves the spec and
runs image-gen; no prompt is passed in from outside.

Dispatch on ``texture.kind``:
  - ``image``       — call backend with ``prompt`` → save PNG at ``image_relpath``
  - ``procedural``  — no-op, ``success=True``, ``cost_usd=0`` (procedural
                       textures live in the part's ``texture_<name>(obj)``
                       Python; no external image needed)
  - missing / other — no-op, same shape, ``kind`` reflects what was found

CLI ``topos generate-texture`` does NOT go through this tool — it talks
to ``ImageGenBackend`` directly because it has the prompt on hand and
isn't tied to a design.json workspace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..agents.image_gen.base import make_backend
from ._paths import resolve_under_workspace
from .registry import tool


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


def _failed(error: str, *, kind: str = "image", duration_s: float = 0.0,
            model: str = "") -> dict[str, Any]:
    """Shape for 'tried to generate, didn't make it' — image-gen API failure,
    backend init error, schema problem in an otherwise-image texture spec.
    cost_usd is 0 because Gemini bills per successfully returned image."""
    return {
        "success": False,
        "kind": kind,
        "image_path": "",
        "byte_size": 0,
        "duration_s": duration_s,
        "model": model,
        "cost_usd": 0.0,
        "usage": {"model": model, "n_images": 0},
        "error": error,
    }


@tool(
    "generate_texture_image",
    description=(
        "Generate the texture image for a part. Reads the texture spec from "
        "``src/design.json[parts.<part_name>.texture]`` — the design agent "
        "authored ``prompt``, ``image_relpath`` and ``kind`` there. For "
        "``kind=='image'`` this calls the configured ImageGenBackend (Gemini "
        "Nano Banana 2 by default) and writes the PNG. For procedural / "
        "missing textures the tool returns success with cost_usd=0 and does "
        "nothing — those textures live entirely in the part's "
        "``texture_<name>(obj)`` Python at build time."
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
                "description": "Echo of the texture.kind that was found: 'image' | 'procedural' | 'missing' | 'unknown'.",
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
    kind = tex.get("kind") or "missing"

    if kind != "image":
        return _no_op(
            kind=kind,
            note=(
                f"part {part_name!r}: texture.kind={kind!r} — no image-gen "
                f"needed (handled by the part's texture_<name>(obj) at build time)"
            ),
        )

    prompt = tex.get("prompt")
    image_relpath = tex.get("image_relpath")
    if not prompt:
        return _failed(
            f"part {part_name!r} has texture.kind='image' but no 'prompt' field "
            f"in design.json — the design agent must set both prompt and "
            f"image_relpath for image-kind textures.",
            kind=kind,
        )
    if not image_relpath:
        return _failed(
            f"part {part_name!r} has texture.kind='image' but no 'image_relpath' "
            f"field in design.json.",
            kind=kind,
        )

    out_path = resolve_under_workspace(ws, image_relpath, label="image_relpath")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        impl = make_backend(backend)
    except (ValueError, RuntimeError) as e:
        return _failed(str(e), kind=kind)

    # Optional size override (some parts may want non-1024 — flag in design.json).
    size = int(tex.get("size") or 1024)

    result = impl.generate(prompt, size=size, timeout_s=timeout_s)
    if not result.success:
        return _failed(
            result.error or "unknown image-gen failure",
            kind=kind,
            duration_s=result.duration_s,
            model=result.model,
        )

    out_path.write_bytes(result.png_bytes)
    return {
        "success": True,
        "kind": kind,
        "image_path": str(out_path.relative_to(ws)),
        "byte_size": len(result.png_bytes),
        "duration_s": result.duration_s,
        "model": result.model,
        "cost_usd": float(result.cost_usd or 0.0),
        "usage": {"model": result.model, "n_images": 1},
    }
