# UV-aware texture experiment harness

Standalone (no edits to `topos/` orchestrator or `generate_texture_image` tool) experiment harness for testing visual-prompt → texture pipelines. Approach: render a part orthographically against a white background, feed that as a `condition_image` to Gemini Nano Banana 2, project the returned PNG back onto the mesh via view-projection UVs.

See the design doc: `/lab/yipeng/.claude/plans/claude-code-dag-texture-parts-blender-c-synchronous-clover.md`.

## Quick start

```bash
# 0. Sanity wiring (no Gemini cost)
python scripts/experiment_uv_texture/run.py \
    --slug cab_a7_full --part Handle --prompt "dry run" \
    --condition silhouette --projection analytical_view \
    --skip-gemini --out-tag dryrun

# 1. One real Gemini cell
python scripts/experiment_uv_texture/run.py \
    --slug cab_a7_full --part Handle \
    --prompt "golden dragon relief carved into the front face, dark bronze background" \
    --condition ao --projection analytical_view \
    --out-tag dragon_one

# 2. Sweep the matrix
python scripts/experiment_uv_texture/run_matrix.py \
    --slug cab_a7_full --part Handle \
    --prompts-file scripts/experiment_uv_texture/prompts.example.txt \
    --conditions silhouette,ao,depth \
    --projections analytical_view,project_from_view \
    --out-tag matrix_v1

# 3. Build the contact sheet
python scripts/experiment_uv_texture/summarize.py \
    outputs/cab_a7_full/artifacts/uv_tex_exp/matrix_v1
```

Outputs land under `outputs/<slug>/artifacts/uv_tex_exp/<out-tag>/`.

## Layout

```
run.py                 # single-cell orchestrator (outer Python)
run_matrix.py          # cross-product sweep
summarize.py           # contact-sheet.png + summary.md from a matrix dir
_common.py             # paths, slugify, view directions, CamSidecar, prompt prefix
_blender_common.py     # bpy helpers shared across phases
_blender_render.py     # phase 1 — runs inside Blender, calls conditions/<name>
_blender_apply.py      # phase 3 — runs inside Blender, calls projections/<name>
conditions/
  silhouette.py        # black-on-white Workbench silhouette
  ao.py                # cavity-shaded Workbench
  depth.py             # camera-distance grayscale (EEVEE)
projections/
  analytical_view.py   # UVs computed directly from camera matrix
  project_from_view.py # bpy.ops.uv.project_from_view + analytical fallback
```

## Adding a new condition

`conditions/myconditon.py`:

```python
from _blender_common import place_ortho_camera, render_to_png

def render_condition(obj, *, view, size, out_path, cam_path):
    cam, sidecar = place_ortho_camera(obj, view=view, size=size)
    # ... configure scene.render.engine / shading / lighting ...
    render_to_png(out_path)
    sidecar.dump(cam_path)
```

Then register in `conditions/__init__.py`:

```python
from . import myconditon
REGISTRY["mycondition"] = myconditon.render_condition
```

## Adding a new projection

`projections/myproj.py`:

```python
from _common import CamSidecar
from projections.analytical_view import _bind_image_material

def apply_projection(obj, *, image_path, cam_path, view):
    sidecar = CamSidecar.load(cam_path)
    # ... write UVs into obj.data.uv_layers.active.data ...
    _bind_image_material(obj, image_path)
```

Then register in `projections/__init__.py`.

## Notes

- Both Blender subprocesses run `blender --background --python <script> -- <json-args>`. The slug's `src/build.py` is exec'd verbatim each time so its bbox/opening/fit validators print to the log as a free sanity pass.
- `bpy.ops.uv.project_from_view` is a UI op; it does not work in `--background` (no `VIEW_3D` area). `project_from_view.py` detects this and falls back to `analytical_view`'s math. So in practice the two projections converge on the same UVs in v0 — they'll diverge once we add a `project_from_view` variant that creates a synthetic viewport context.
- `_CONDITION_PROMPT_PREFIX` in `_common.py` is the harness-wide instruction prepended to the user prompt before the Gemini call. Tune there to control how Gemini handles the white background.
- Default `--view front` corresponds to camera looking +Y at the part (matching the `-Y` "front" convention in `cab_a7_full/design.json`).
- Adding more views: add an entry to `_common.VIEW_DIRECTIONS` (3-vector world-space camera offset direction).
