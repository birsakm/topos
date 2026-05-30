"""Phase 1 — condition-image render. Runs inside Blender subprocess.

Args (single JSON blob after `--`):
    slug_src_dir : absolute path to outputs/<slug>/src/
    part_name    : object name to texture (case-sensitive)
    view         : named view from _common.VIEW_DIRECTIONS
    size         : square render resolution in pixels
    condition    : key in conditions.REGISTRY
    cond_png_out : absolute path for the condition PNG
    cam_json_out : absolute path for the camera sidecar JSON
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow imports of sibling modules in this experiment directory.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _blender_common import isolate_part, load_scene_from_slug  # noqa: E402
from _common import parse_blender_args                           # noqa: E402
import conditions                                                # noqa: E402


def main() -> None:
    args = parse_blender_args(sys.argv)

    slug_src_dir = Path(args["slug_src_dir"])
    part_name    = str(args["part_name"])
    view         = str(args["view"])
    size         = int(args["size"])
    condition    = str(args["condition"])
    cond_png_out = Path(args["cond_png_out"])
    cam_json_out = Path(args["cam_json_out"])

    print(f"[phase1] slug_src={slug_src_dir} part={part_name} view={view} "
          f"size={size} condition={condition}")

    load_scene_from_slug(slug_src_dir)
    obj = isolate_part(part_name)

    render_fn = conditions.get(condition)
    render_fn(
        obj,
        view=view,
        size=size,
        out_path=cond_png_out,
        cam_path=cam_json_out,
    )
    print(f"[phase1] wrote {cond_png_out}")
    print(f"[phase1] wrote {cam_json_out}")


try:
    main()
except Exception as e:
    import traceback
    print(f"[phase1] FAILED: {e}", file=sys.stderr)
    traceback.print_exc()
    # Blender --background returns 0 even on uncaught Python exceptions;
    # force a non-zero exit so run.py sees the failure.
    sys.exit(1)
