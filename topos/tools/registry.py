"""Tool registry. A registered tool is invoked by the orchestrator as a
ToolTask in the DAG (see ``runner._run_tool``); it is the deterministic,
code-owned half of the pipeline (render / export / judge / verify), distinct
from the LLM AgentTasks.

These tools are NOT exposed to the coding-agent backends: the claude CLI is
launched with ``mcp_servers=[]`` (``runner.py``), so an agent's only tools are
its ``allowed_tools`` (Read/Edit/Write/Glob/Bash/...). Anything an agent needs
from this layer is reached via the CLI instead (e.g. ``topos bpy-docs search``).
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
    #   tools (judge, generate_texture_image).


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
    from . import judge  # noqa: F401
    from . import generate_texture_image  # noqa: F401
    from . import verify_geometry  # noqa: F401
    # Tool subpackages — each subdir's __init__.py imports its tool.py so
    # the @tool decorators fire on subpackage load. Subdirs are named by
    # the (backend × capability) pair so future expansion can drop in
    # threejs_render/ or unity_render/ etc. without colliding. ``export``
    # stays unprefixed because it already handles multiple targets (GLB,
    # URDF, ...) and isn't tied to one renderer.
    from . import blender_render  # noqa: F401  (render_multiview / render_part)
    from . import export          # noqa: F401  (export_glb / export_urdf)
    from . import blender_verifier  # noqa: F401  (verify_parts)
    from . import texture_uv_atlas  # noqa: F401  (texture_uv_atlas)
