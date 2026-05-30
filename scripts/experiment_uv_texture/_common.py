"""Shared utilities for the UV-texture experiment harness.

Pure stdlib so it can be imported both from outer Python (which has topos
installed) and from Blender's bundled Python (which does not).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


# View definitions: (camera_offset_direction, up_axis_hint, label).
# The camera is placed at part_center + offset_direction * distance, looking
# back at the center. Offset direction is a unit vector in world space.
#
# "front" follows the convention in outputs/cab_a7_full/design.json — the
# drawer opens along -Y, so the user's "front" of the part means a viewer
# standing at -Y looking +Y. Camera offset direction is therefore -Y.
# Cube-atlas layout for the UV-atlas pipeline. 3 columns × 2 rows of tiles
# arranged on a square canvas in [0,1]² UV space. Each (axis, sign) maps to
# a single tile: row 0 is the "negative" face (front / left / bottom), row 1
# is the "positive" face (back / right / top). The label string is what we
# overlay onto each tile in the condition image so Gemini knows which
# physical surface that island represents.
#
# Tile geometry: each tile occupies a TILE_W × TILE_H rectangle inside the
# unit square, with TILE_MARGIN of empty space around it for the label band.
TILE_W = 1.0 / 3.0
TILE_H_6 = 1.0 / 2.0    # tile height for 3×2 (6-tile, solid parts)
TILE_H_12 = 1.0 / 4.0   # tile height for 3×4 (12-tile, dual inner/outer)
TILE_MARGIN = 0.03       # inset, normalized to UV [0,1]

# (axis_index, sign) → (col, row, short label, full label)
# axis_index: 0=X, 1=Y, 2=Z. sign: -1 or +1.
# 6-tile layout (no inner/outer split). Solid parts only.
CUBE_ATLAS_LAYOUT: dict[tuple[int, int], tuple[int, int, str, str]] = {
    (0, -1): (0, 0, "LEFT",   "outer face, -X (left)"),
    (1, -1): (1, 0, "FRONT",  "outer face, -Y (front)"),
    (2, -1): (2, 0, "BOTTOM", "outer face, -Z (bottom)"),
    (0, +1): (0, 1, "RIGHT",  "outer face, +X (right)"),
    (1, +1): (1, 1, "BACK",   "outer face, +Y (back)"),
    (2, +1): (2, 1, "TOP",    "outer face, +Z (top)"),
}

# 12-tile layout: 3 cols × 4 rows. Rows 0-1 (bottom half of canvas) hold
# the outer shell; rows 2-3 (top half) hold the inner cavity walls. Used
# when a part is hollow (drawer, cabinet frame). Per-face side determined
# by sign of dot(face_normal, face_position_relative_to_bbox_center).
CUBE_ATLAS_LAYOUT_DUAL: dict[tuple[int, int, str], tuple[int, int, str, str]] = {
    # OUTER shell — bottom half of canvas (V=[0, 0.5])
    (0, -1, "outer"): (0, 0, "OUT-L",   "outer face, -X (left)"),
    (1, -1, "outer"): (1, 0, "OUT-F",   "outer face, -Y (front)"),
    (2, -1, "outer"): (2, 0, "OUT-Bot", "outer face, -Z (bottom)"),
    (0, +1, "outer"): (0, 1, "OUT-R",   "outer face, +X (right)"),
    (1, +1, "outer"): (1, 1, "OUT-B",   "outer face, +Y (back)"),
    (2, +1, "outer"): (2, 1, "OUT-T",   "outer face, +Z (top)"),
    # INNER cavity — top half of canvas (V=[0.5, 1.0])
    (0, -1, "inner"): (0, 2, "IN-L",   "inner face, -X side (left cavity wall)"),
    (1, -1, "inner"): (1, 2, "IN-F",   "inner face, -Y side (front cavity wall)"),
    (2, -1, "inner"): (2, 2, "IN-Bot", "inner face, -Z side (cavity floor)"),
    (0, +1, "inner"): (0, 3, "IN-R",   "inner face, +X side (right cavity wall)"),
    (1, +1, "inner"): (1, 3, "IN-B",   "inner face, +Y side (back cavity wall)"),
    (2, +1, "inner"): (2, 3, "IN-T",   "inner face, +Z side (cavity ceiling)"),
}


# Cylindrical-atlas layout — used for parts that are dominantly a cylinder
# along some axis (handles, knobs, columns). Three zones on the canvas:
#   - Top half (V=[0.5, 1.0], full width): LATERAL — the unwrapped band
#     where U=theta around axis, V=position along axis
#   - Bottom-left (V=[0,0.5], U=[0,0.5]): CAP-NEG — the negative-axis endcap
#   - Bottom-right (V=[0,0.5], U=[0.5,1]): CAP-POS — the positive-axis endcap
# Unlike the cube layouts (int col/row grid indices), this dict stores the
# UV-space origin (u0, v0) of each zone directly as floats, because the
# three zones are not uniform grid cells.
CYLINDER_ATLAS_LABELS: dict[str, tuple[float, float, str, str]] = {
    "lateral":  (0.0, 0.5, "LATERAL",  "unwrapped lateral cylinder surface, U=angle around axis"),
    "cap_neg":  (0.0, 0.0, "CAP-NEG",  "endcap on the negative end of the cylinder axis"),
    "cap_pos":  (0.5, 0.0, "CAP-POS",  "endcap on the positive end of the cylinder axis"),
}


VIEW_DIRECTIONS: dict[str, tuple[float, float, float]] = {
    "front":  (0.0, -1.0, 0.0),
    "back":   (0.0, +1.0, 0.0),
    "left":   (-1.0, 0.0, 0.0),
    "right":  (+1.0, 0.0, 0.0),
    "top":    (0.0, 0.0, +1.0),
    "bottom": (0.0, 0.0, -1.0),
    # Three-quarter views — useful for diagnostic re-renders in phase 3.
    "front_3q":  (+0.71, -0.71, +0.30),
    "back_3q":   (+0.71, +0.71, +0.30),
}


# Prompt prefix prepended to the user's prompt before the Gemini call. The
# critical bits: (1) tell Nano Banana to paint *within* the silhouetted
# region, (2) keep the white background, (3) output the same dimensions as
# the conditioning image. Tuned in v0; expect to iterate on this.
CONDITION_PROMPT_PREFIX = (
    "You are painting a texture onto a 3D object that is shown to you in the "
    "attached image. The object is rendered against a pure white background "
    "from an orthographic camera. Treat the silhouette / form shown in the "
    "image as a 2D canvas. Paint your content ONLY within the object's "
    "silhouette — keep the surrounding white background completely untouched. "
    "Preserve the object's overall shape and position so the result aligns "
    "exactly with the input image. Output a square PNG of the same dimensions. "
    "Subject of the painting:"
)


@dataclass
class CamSidecar:
    """Camera state serialized between phase-1 and phase-3 Blender processes.

    Phase 1 writes this after framing the part; phase 3 reads it back so the
    apply step's project-from-view UV unwrap uses the exact same ortho
    transform that produced the condition image.
    """
    matrix_world_rows: list[list[float]]  # 4x4 camera world matrix
    ortho_scale: float
    clip_start: float
    clip_end: float
    resolution: int                        # render was resolution × resolution
    view: str                              # which named view ("front", ...)

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps({
            "matrix_world_rows": self.matrix_world_rows,
            "ortho_scale": self.ortho_scale,
            "clip_start": self.clip_start,
            "clip_end": self.clip_end,
            "resolution": self.resolution,
            "view": self.view,
        }, indent=2))

    @classmethod
    def load(cls, path: Path) -> "CamSidecar":
        raw = json.loads(path.read_text())
        return cls(
            matrix_world_rows=[list(r) for r in raw["matrix_world_rows"]],
            ortho_scale=float(raw["ortho_scale"]),
            clip_start=float(raw["clip_start"]),
            clip_end=float(raw["clip_end"]),
            resolution=int(raw["resolution"]),
            view=str(raw["view"]),
        )


def slugify(text: str, *, max_len: int = 32) -> str:
    """Filesystem-safe slug — lowercase, alnum + underscores, truncated."""
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s or "x"


def parse_blender_args(argv: list[str]) -> dict:
    """Pull the JSON payload after `--` from a Blender subprocess argv."""
    try:
        sep = argv.index("--")
    except ValueError as e:
        raise SystemExit(
            f"_common.parse_blender_args: '--' separator not found in argv={argv!r}"
        ) from e
    if sep + 1 >= len(argv):
        raise SystemExit("_common.parse_blender_args: no JSON arg after '--'")
    return json.loads(argv[sep + 1])
