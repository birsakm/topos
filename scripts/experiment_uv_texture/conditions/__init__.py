"""Condition-image renderer registry.

Each condition module defines:

    def render_condition(
        obj,                  # bpy.types.Object (already isolated in scene)
        *,
        view: str,            # named view (see _common.VIEW_DIRECTIONS)
        size: int,            # square render resolution
        out_path,             # pathlib.Path — where the PNG goes
        cam_path,             # pathlib.Path — where the camera sidecar JSON goes
    ) -> None

Adding a new condition = drop a new module here + register below.
"""

from . import ao, cycles_diffuse, depth, normal, silhouette

REGISTRY = {
    "silhouette":     silhouette.render_condition,
    "ao":             ao.render_condition,
    "depth":          depth.render_condition,
    "normal":         normal.render_condition,
    "cycles_diffuse": cycles_diffuse.render_condition,
}


def get(name: str):
    if name not in REGISTRY:
        raise KeyError(f"unknown condition {name!r}; choices: {sorted(REGISTRY)}")
    return REGISTRY[name]
