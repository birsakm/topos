"""``verify_geometry`` tool — deterministic checks on design.json.

Vision judges describe images; this tool asserts numeric invariants from
``src/design.json`` directly. Two of the most common gemini failure modes
(observed 2026-05-13 on cab_gemini_*_palace5 runs) are arithmetic in the
design phase that no vision judge can reliably pin to a millimeter:

  - **Handle sunk into drawer face**: design agent forgets to subtract
    ``handle.ey/2`` when computing the handle's ``world_xyz.Y``, so the
    handle ends up 3-5 mm BEHIND the drawer's front face instead of in
    front. The judge sees "handle clipping" in the render but the fix
    requires editing ``design.json`` Y by exact mm, which a vision-LLM
    fix-loop can't deliver.

  - **Drawer Z slots overlap or leave gaps**: design's per-drawer Z
    centers and ez extents don't match the frame's interior height. The
    visible symptom is "top drawer crammed against cornice" or "gap
    between drawer 3 and 4" — but the root cause is design arithmetic.

Both classes are cheap to check from numbers alone. Tool runs in <50ms,
no Blender, no LLM. Output mirrors the ``failed_parts`` schema used by
``verify_parts`` so future fix-loop dispatch could route to a redesign
task without adding a new code path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._paths import resolve_under_workspace
from .registry import tool


_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "workspace": {"type": "string"},
        "design_relpath": {"type": "string", "default": "src/design.json"},
        "handle_min_proud_mm": {
            "type": "number", "default": 0.5,
            "description": (
                "Minimum mm a hardware part (handle/pull/knob) must protrude "
                "from its parent's front face. <0 means sunken — always a fail."
            ),
        },
    },
    "required": ["workspace"],
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "total": {"type": "integer"},
        "passed_assertions": {"type": "array", "items": {"type": "string"}},
        "failed_parts": {
            "type": "array",
            "items": {"type": "object"},
            "description": (
                "Per-failure records with name, stage, error_class, error_msg. "
                "Schema mirrors verify_parts so fix-loop can reuse the same dispatch."
            ),
        },
    },
    "required": ["success", "total", "passed_assertions", "failed_parts"],
}


def _parts_by_name(design: dict) -> dict[str, dict]:
    return {p["name"]: p for p in design.get("parts", [])}


def _bbox_range(part: dict, axis_idx: int) -> tuple[float, float]:
    c = part["world_xyz"][axis_idx]
    e = part["world_extents"][axis_idx]
    return (c - e / 2, c + e / 2)


def _find_parent_drawer(handle_name: str, parts_by_name: dict) -> dict | None:
    """Match HandleN <-> DrawerN by trailing digit(s). Returns None when no
    parent can be identified — caller treats that as "unknown geometry,
    skip" rather than fail.
    """
    import re
    m = re.search(r"(\d+)$", handle_name)
    if not m:
        return None
    suffix = m.group(1)
    candidates = [
        f"Drawer{suffix}",
        f"Drawer_{suffix}",
        f"Door{suffix}",
        f"Door_{suffix}",
    ]
    for cand in candidates:
        if cand in parts_by_name:
            return parts_by_name[cand]
    return None


_HARDWARE_TOKENS = ("handle", "pull", "knob")


def _is_hardware(part: dict) -> bool:
    name = (part.get("name") or "").lower()
    role = (part.get("role") or "").lower()
    return any(t in name or t in role for t in _HARDWARE_TOKENS)


@tool(
    "verify_geometry",
    description=(
        "Read src/design.json and assert numeric invariants the vision judge "
        "can't reliably catch (mm-scale offsets, drawer-stack Z gaps, missing "
        "cavity fields). Pure arithmetic — no Blender, no LLM. Returns "
        "failed_parts in the same schema as verify_parts so fix-loop dispatch "
        "could route to a design redesign task."
    ),
    input_schema=_INPUT_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    side_effects=False,
    deterministic=True,
)
def verify_geometry(
    *,
    workspace: str,
    design_relpath: str = "src/design.json",
    handle_min_proud_mm: float = 0.5,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    design_path = resolve_under_workspace(ws, design_relpath, label="design_relpath")
    if not design_path.is_file():
        return {
            "success": False,
            "total": 0,
            "passed_assertions": [],
            "failed_parts": [{
                "name": "<design.json>",
                "stage": "load",
                "error_class": "FileNotFoundError",
                "error_msg": f"missing {design_path}",
            }],
        }
    try:
        design = json.loads(design_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "total": 0,
            "passed_assertions": [],
            "failed_parts": [{
                "name": "<design.json>",
                "stage": "load",
                "error_class": "JSONDecodeError",
                "error_msg": str(e),
            }],
        }
    if not isinstance(design, dict):
        return {
            "success": False,
            "total": 0,
            "passed_assertions": [],
            "failed_parts": [{
                "name": "<design.json>",
                "stage": "load",
                "error_class": "TypeError",
                "error_msg": f"expected dict, got {type(design).__name__}",
            }],
        }
    parts_by_name = _parts_by_name(design)

    passed: list[str] = []
    failed: list[dict] = []

    # ---- Check 1: every hardware part is proud of its parent drawer ----
    for name, part in parts_by_name.items():
        if not _is_hardware(part):
            continue
        parent = _find_parent_drawer(name, parts_by_name)
        if parent is None:
            continue
        h_y_lo, h_y_hi = _bbox_range(part, 1)
        p_y_lo, _ = _bbox_range(parent, 1)
        # Front face of parent (drawer/door) is the most -Y face (the
        # framework convention: -Y points toward the viewer). The hardware's
        # +Y-most (back) extent must be <= parent's -Y face — i.e. handle
        # sits entirely in front of the drawer. Positive proud = good.
        proud_m = p_y_lo - h_y_hi
        proud_mm = proud_m * 1000
        if proud_mm < handle_min_proud_mm:
            failed.append({
                "name": name,
                "stage": "design_handle_protrusion",
                "error_class": "GeometryAssertionError",
                "error_msg": (
                    f"{name} protrudes only {proud_mm:.2f} mm from {parent['name']}'s "
                    f"front face (threshold {handle_min_proud_mm:.2f} mm). "
                    f"Fix: set {name}.world_xyz.Y <= "
                    f"{parent['world_xyz'][1] - parent['world_extents'][1]/2 - part['world_extents'][1]/2 - handle_min_proud_mm/1000:.4f} "
                    f"(currently {part['world_xyz'][1]:.4f})."
                ),
                "delta_mm": round(handle_min_proud_mm - proud_mm, 2),
            })
        else:
            passed.append(f"{name}_proud_over_{parent['name']}")

    # ---- Check 2: Drawer Z ranges don't overlap each other ----
    drawers = sorted(
        [(n, p) for n, p in parts_by_name.items() if n.lower().startswith("drawer")],
        key=lambda kv: -kv[1]["world_xyz"][2],  # top-to-bottom
    )
    for i in range(len(drawers) - 1):
        n_top, p_top = drawers[i]
        n_bot, p_bot = drawers[i + 1]
        top_z_lo, _ = _bbox_range(p_top, 2)  # drawer bottom face Z
        _, bot_z_hi = _bbox_range(p_bot, 2)  # drawer top face Z
        gap_mm = (top_z_lo - bot_z_hi) * 1000
        if gap_mm < 0:
            failed.append({
                "name": n_top,
                "stage": "design_drawer_z_overlap",
                "error_class": "GeometryAssertionError",
                "error_msg": (
                    f"{n_top} (z_bottom={top_z_lo:.4f}) overlaps {n_bot} "
                    f"(z_top={bot_z_hi:.4f}) by {-gap_mm:.2f} mm. "
                    f"Adjust one drawer's world_xyz.Z to restore a non-negative gap."
                ),
                "delta_mm": round(-gap_mm, 2),
            })
        else:
            passed.append(f"{n_top}_above_{n_bot}_gap_{gap_mm:.1f}mm")

    # ---- Check 3: hollow Frame must declare cavity / cavities ----
    frame = parts_by_name.get("Frame")
    if frame is not None:
        strategy = (frame.get("geometry_strategy") or "").lower()
        if "hollow" in strategy or "boolean" in strategy or "cavity" in strategy:
            if not (frame.get("cavity") or frame.get("cavities")):
                failed.append({
                    "name": "Frame",
                    "stage": "design_missing_cavity",
                    "error_class": "GeometryAssertionError",
                    "error_msg": (
                        f"Frame.geometry_strategy={strategy!r} implies a hollow body, "
                        f"but no 'cavity' or 'cavities' field is declared. Part agents "
                        f"will guess the interior layout independently, leading to "
                        f"mis-aligned drawer slots. Add cavity:{{world_xyz, world_extents, open_axis}} "
                        f"or cavities:[...] to design.json."
                    ),
                })
            else:
                passed.append("Frame_has_cavity_field")

    return {
        "success": len(failed) == 0,
        "total": len(passed) + len(failed),
        "passed_assertions": passed,
        "failed_parts": failed,
    }
