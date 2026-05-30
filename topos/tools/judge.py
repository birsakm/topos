"""``judge`` tool: load a rubric, materialise the right ``Critic``, and
evaluate against rendered images. Exposed to agents and to ToolTasks.

(Tool name stays ``judge`` for plan.json / config stability; internal types
are the renamed ``Critic`` / ``CriticInputs`` from ``topos.agents.visual_critic``.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agents.visual_critic.base import CriticInputs, load_rubric, make_critic
from ._paths import resolve_under_workspace
from .registry import tool


INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "workspace": {"type": "string"},
        "rubric": {"type": "string", "description": "Rubric short name (e.g. 'rigid_object_v1') or absolute path to .yaml"},
        "image_pattern": {"type": "string", "description": "Glob pattern relative to workspace (e.g. 'artifacts/*.png')"},
        "images": {"type": "array", "items": {"type": "string"}, "description": "Explicit list, takes precedence over image_pattern"},
        "metadata": {"type": "object"},
    },
    "required": ["workspace", "rubric"],
}

OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "overall_score": {"type": "number"},
        "per_criterion": {"type": "object"},
        "suggested_fixes": {"type": "array", "items": {"type": "string"}},
        "images_evaluated": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["passed", "overall_score", "per_criterion", "suggested_fixes"],
}


@tool(
    "judge",
    description=(
        "Evaluate rendered images against a Topos rubric and return scores plus "
        "concrete suggested fixes."
    ),
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    side_effects=False,
)
def judge(
    *,
    workspace: str,
    rubric: str,
    image_pattern: str | None = None,
    images: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        raise NotADirectoryError(f"workspace must be a directory: {ws}")

    img_paths: list[Path] = []
    if images:
        img_paths = [resolve_under_workspace(ws, p, label=f"images[{i}]") for i, p in enumerate(images)]
    elif image_pattern:
        img_paths = sorted(ws.glob(image_pattern))

    if not img_paths:
        # Fail loud (CLAUDE.md rule #12): zero matches is a misconfiguration,
        # not a low judge score. Letting it return passed=False would burn
        # fix-loop iters on a non-existent visual.
        raise FileNotFoundError(
            f"judge: no images matched under {ws} "
            f"(pattern={image_pattern!r}, images={images!r}). "
            "Check the workspace and the image_pattern in plan.json."
        )

    rubric_obj = load_rubric(rubric)
    critic = make_critic(rubric_obj)
    # Inject workspace_path so CLI critics (claude_vision, codex_cli, gemini_cli)
    # can run INSIDE the project workspace and Read src/ files for grounding.
    # API-only critics (openai_vision, gemini_vision) ignore this field.
    md = dict(metadata or {})
    md.setdefault("workspace_path", str(ws))

    # Auto-inject prompts/intent.md (frozen by the spec agent at `topos make`
    # time) as role_hint when the caller hasn't set one. Without this the
    # critic sees rendered images plus a generic rubric and has no way to
    # tell whether the produced thing matches the request — leading to
    # absurdities like grading a turbofan engine as a "grinder or shaker"
    # because the silhouette is plausible and the rubric is identity-agnostic.
    # Per-part judges already set a part-specific role_hint upstream, so this
    # only kicks in for assembly-level judges that lack one.
    if "role_hint" not in md:
        intent_path = ws / "prompts" / "intent.md"
        if intent_path.is_file():
            md["role_hint"] = (
                "The output renders should match this user-stated intent "
                "(frozen at spec time):\n\n"
                f"{intent_path.read_text(encoding='utf-8').strip()}\n\n"
                "Grade the renders by how well the produced object matches "
                "this intent. Be critical — do NOT assume the produced object "
                "already matches because the prompt describes the target."
            )

    result = critic.evaluate(
        CriticInputs(images=img_paths, metadata=md), rubric_obj
    )
    return {
        **result.to_dict(),
        "images_evaluated": [str(p.relative_to(ws)) if p.is_relative_to(ws) else str(p) for p in img_paths],
    }
