"""Tool registry. Tools registered here are dual-purpose: callable directly
from a ToolTask in the orchestrator, and exposed over MCP to agent backends.

A single source of truth: the same Python function implements both paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolSpec:
    name: str
    func: Callable[..., Any]
    description: str
    input_schema: dict
    output_schema: dict | None = None
    side_effects: bool = True   # informational; not enforced
    deterministic: bool = False
    # ^ If True, the runner skips re-execution at iter > 0 when none of the
    #   tool's dependencies actually re-ran this iter (their inputs are
    #   byte-identical to the prior iter, so the output would be too).
    #   Set True for pure transforms (export_*, verify_parts) and
    #   deterministic renderers (workbench/eevee). Leave False for stochastic
    #   tools (judge, generate_texture_image) or arbitrary-script tools
    #   (blender_run).


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    *,
    description: str,
    input_schema: dict,
    output_schema: dict | None = None,
    side_effects: bool = True,
    deterministic: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: register a function as a Topos tool under ``name``."""
    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise RuntimeError(f"tool already registered: {name!r}")
        _REGISTRY[name] = ToolSpec(
            name=name,
            func=func,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            side_effects=side_effects,
            deterministic=deterministic,
        )
        return func
    return deco


def get(name: str) -> ToolSpec:
    if name not in _REGISTRY:
        raise KeyError(f"tool not registered: {name!r}")
    return _REGISTRY[name]


def all_tools() -> dict[str, ToolSpec]:
    return dict(_REGISTRY)


def clear() -> None:
    """Test-only: drop every registration."""
    _REGISTRY.clear()


def _ensure_default_tools_imported() -> None:
    """Import sibling tool modules so their @tool decorators run.

    Called lazily so a partial install doesn't blow up at import-time.
    """
    # Top-level single-tool modules (flat .py files)
    from . import blender_run  # noqa: F401
    from . import judge  # noqa: F401
    from . import bpy_docs_search  # noqa: F401
    from . import generate_texture_image  # noqa: F401
    from . import verify_geometry  # noqa: F401
    # Tool subpackages — each subdir's __init__.py imports its tool.py so
    # the @tool decorators fire on subpackage load. Subdirs are named by
    # the (backend × capability) pair so future expansion can drop in
    # threejs_render/ or unity_render/ etc. without colliding. ``export``
    # stays unprefixed because it already handles multiple targets (GLB,
    # URDF, ...) and isn't tied to one renderer.
    from . import blender_render  # noqa: F401  (render / render_multiview / render_part / render_turntable / render_wireframe / render_cross_section)
    from . import export          # noqa: F401  (export_glb / export_urdf)
    from . import blender_verifier  # noqa: F401  (verify_parts)
    from . import texture_uv_atlas  # noqa: F401  (texture_uv_atlas)
