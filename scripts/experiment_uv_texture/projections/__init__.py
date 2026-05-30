"""Projection-strategy registry.

Each projection module defines:

    def apply_projection(
        obj,                  # bpy.types.Object
        *,
        image_path,           # pathlib.Path — generated texture PNG
        cam_path,             # pathlib.Path — camera sidecar JSON from phase 1
        view: str,
    ) -> None

Adding a new strategy = drop a new module here + register below.
"""

from . import analytical_view, project_from_view

REGISTRY = {
    "project_from_view": project_from_view.apply_projection,
    "analytical_view":   analytical_view.apply_projection,
}


def get(name: str):
    if name not in REGISTRY:
        raise KeyError(f"unknown projection {name!r}; choices: {sorted(REGISTRY)}")
    return REGISTRY[name]
