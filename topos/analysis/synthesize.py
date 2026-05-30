"""LLM-based synthesis of trajectory analysis.

Calls ``extract_run_metrics`` for structured data, builds a compact prompt
(< 8K tokens), sends it to a configurable text-LLM provider (``gemini`` /
``openai`` / ``anthropic`` — pick via ``analysis.backend`` config or the
``--backend`` flag), and returns a markdown report. Defaults to Gemini Flash.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .. import config as cfg
from ..agents.visual_critic.critic_utils import post_json_with_retries
from .extract import RunAnalysis, extract_run_metrics


# Per-provider default model; override via analysis.model config or --model.
_DEFAULT_MODELS = {
    "gemini": "gemini-3-flash-preview",
    "openai": "gpt-5",
    "anthropic": "claude-sonnet-4-6",
}

_GEMINI_ENDPOINT_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"

_SYNTHESIS_PROMPT = """\
You are analyzing the trajectory of an AI-driven 3D articulated object generation pipeline.
The system uses coding agents (Claude Sonnet/Opus) to write Blender Python code that builds 3D parts,
a build agent to assemble them, and vision judges to evaluate quality.

## Run Metrics
{metrics_json}

## Judge Feedback
{judge_scores_and_fixes}

## Agent Behavior Summary
{per_agent_tool_call_patterns}

## Geometry Verification Warnings
{verify_warnings}

Provide a structured analysis:

### 1. Quality Assessment
Rate the overall output quality. Which parts succeeded? Which fell short and why?

### 2. Agent Behavior Analysis
For each agent task: Did it follow best practices? (read skills first, check design.json, \
iterate on code, use bpy_docs_search for API verification). Flag agents that wrote one-shot \
code without reading skills.

### 3. Failure Root Causes
For each criterion that scored below 0.6: what's the likely root cause? \
(geometry too simple, parts don't fit, texture issues, etc.)

### 4. Actionable Improvements
Specific changes to make the next run better:
- Prompt modifications (what to add/change in extras_*.md)
- Skill gaps (what knowledge the agents lacked)
- Design spec issues (bad coordinates, missing constraints)
- Pipeline issues (tool failures, config problems)

### 5. Cost Efficiency
Was the budget well-spent? Which tasks consumed disproportionate cost for their contribution?
"""


def _resolve_backend_and_model(backend: str | None, model: str | None) -> tuple[str, str]:
    """Pick the analysis provider + model. Precedence: explicit arg >
    ``analysis.*`` config > built-in default (gemini).

    ``analysis.model`` is paired with ``analysis.backend``: if the caller
    overrides the backend to a different provider, the config's model (meant
    for the config's backend) is ignored in favour of the new backend's
    default — a gpt-5 model id shouldn't leak onto an anthropic call.
    """
    asec = cfg.load_effective_config().get("analysis") or {}
    cfg_backend = (asec.get("backend") or "gemini").lower()
    resolved = (backend or cfg_backend).lower()
    if resolved not in _DEFAULT_MODELS:
        raise ValueError(
            f"unknown analysis backend {resolved!r}; choose from {sorted(_DEFAULT_MODELS)}"
        )
    if model is None:
        model = (asec.get("model") if resolved == cfg_backend else None) or _DEFAULT_MODELS[resolved]
    return resolved, model


def _resolve_api_key(backend: str) -> str:
    """Resolve the API key for the chosen analysis provider from env or config,
    reusing the keys other features already configure (Google/OpenAI keys aren't
    feature-scoped, so a critic key works for analysis too)."""
    effective = cfg.load_effective_config()
    vc = effective.get("visual_critic") or {}
    if backend == "gemini":
        key = (
            os.environ.get("GEMINI_API_KEY")
            or (vc.get("gemini_vision") or {}).get("api_key")
            or ((effective.get("image_gen") or {}).get("gemini") or {}).get("api_key")
        )
        hint = "GEMINI_API_KEY, or visual_critic.gemini_vision.api_key, or image_gen.gemini.api_key"
    elif backend == "openai":
        key = (
            os.environ.get("OPENAI_API_KEY")
            or (vc.get("openai_vision") or {}).get("api_key")
        )
        hint = "OPENAI_API_KEY, or visual_critic.openai_vision.api_key"
    elif backend == "anthropic":
        key = (
            os.environ.get("ANTHROPIC_API_KEY")
            or ((effective.get("analysis") or {}).get("anthropic") or {}).get("api_key")
        )
        hint = "ANTHROPIC_API_KEY, or analysis.anthropic.api_key"
    else:  # pragma: no cover — guarded by _resolve_backend_and_model
        raise ValueError(f"unknown analysis backend {backend!r}")
    if not key:
        raise RuntimeError(f"No API key for analysis backend {backend!r}. Set one of: {hint}.")
    return key


def _build_metrics_json(analysis: RunAnalysis) -> str:
    """Compact JSON summary of the run — kept under ~2K tokens."""
    return json.dumps(
        {
            "slug": analysis.slug,
            "passed": analysis.passed,
            "final_score": analysis.final_score,
            "iterations": analysis.iterations,
            "total_cost_usd": round(analysis.total_cost_usd, 4),
            "total_wall_time_s": round(analysis.total_wall_time_s, 1),
            "n_parts": analysis.n_parts,
            "n_joints": analysis.n_joints,
            "design": {
                "robot_name": analysis.design_summary.get("robot_name", ""),
                "part_names": analysis.design_summary.get("part_names", []),
                "joint_types": analysis.design_summary.get("joint_types", []),
            },
        },
        indent=2,
    )


def _build_judge_section(analysis: RunAnalysis) -> str:
    """Format judge task scores and feedback."""
    lines: list[str] = []
    judge_tasks = [
        t for t in analysis.tasks
        if t.tool_name and "judge" in t.tool_name and t.score is not None
    ]
    if not judge_tasks:
        return "(no judge scores found)"

    for jt in judge_tasks:
        lines.append(f"**{jt.task_id}** — score={jt.score:.3f} success={jt.success}")
        if jt.per_criterion:
            for crit, score_val in jt.per_criterion.items():
                lines.append(f"  - {crit}: {score_val:.2f}")
        if jt.suggested_fixes:
            lines.append("  Suggested fixes:")
            # Truncate each fix to keep prompt compact
            for fix in jt.suggested_fixes[:5]:
                lines.append(f"    - {fix[:200]}")
        lines.append("")

    return "\n".join(lines)


def _build_agent_section(analysis: RunAnalysis) -> str:
    """Summarise agent behavior: tool calls, skills read, edit cycles."""
    lines: list[str] = []
    agent_tasks = [t for t in analysis.tasks if t.kind == "agent"]
    if not agent_tasks:
        return "(no agent tasks)"

    for at in agent_tasks:
        lines.append(
            f"**{at.task_id}** (iter={at.iter}) — "
            f"cost=${at.cost_usd:.4f}, duration={at.duration_s:.0f}s, "
            f"tool_calls={at.n_tool_calls}"
        )
        if at.tool_call_summary:
            summary = ", ".join(
                f"{tc['tool']}:{tc['count']}" for tc in at.tool_call_summary[:8]
            )
            lines.append(f"  Tools: {summary}")
        if at.skills_read:
            lines.append(f"  Skills read: {', '.join(at.skills_read)}")
        else:
            lines.append("  Skills read: NONE")
        if at.files_written:
            fnames = [Path(f).name for f in at.files_written]
            lines.append(f"  Files written: {', '.join(fnames)}")
        if at.n_edit_cycles > 0:
            lines.append(f"  Files with multiple edit cycles: {at.n_edit_cycles}")
        lines.append("")

    return "\n".join(lines)


def _build_warnings_section(analysis: RunAnalysis) -> str:
    """Format geometry warnings."""
    if not analysis.warnings:
        return "(no warnings)"
    # Cap at 20 warnings to keep prompt compact
    lines = analysis.warnings[:20]
    if len(analysis.warnings) > 20:
        lines.append(f"... and {len(analysis.warnings) - 20} more")
    return "\n".join(lines)


def _call_gemini(prompt: str, *, model: str, api_key: str, timeout_s: int = 120) -> str:
    """Call Gemini generateContent and return the text response."""
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096},
    }
    url = f"{_GEMINI_ENDPOINT_BASE}/{model}:generateContent?key={api_key}"
    response_bytes = post_json_with_retries(
        url=url,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout_s=timeout_s,
        max_retries=2,
        retry_base_wait_s=15.0,
        label="analyze",
    )
    envelope = json.loads(response_bytes)
    candidates = envelope.get("candidates") or []
    if not candidates:
        raise RuntimeError(
            f"Gemini returned no candidates. "
            f"promptFeedback={envelope.get('promptFeedback')}"
        )
    for part in (candidates[0].get("content") or {}).get("parts") or []:
        if isinstance(part.get("text"), str):
            return part["text"]
    raise RuntimeError("Gemini response had no text part")


def _call_openai(prompt: str, *, model: str, api_key: str, timeout_s: int = 120) -> str:
    """Call OpenAI Chat Completions and return the text response. (No temperature
    or token cap set — reasoning models like gpt-5 reject non-default values.)"""
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    response_bytes = post_json_with_retries(
        url=_OPENAI_ENDPOINT,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        timeout_s=timeout_s,
        max_retries=2,
        retry_base_wait_s=15.0,
        label="analyze",
    )
    envelope = json.loads(response_bytes)
    choices = envelope.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices. keys={list(envelope)}")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str):
        raise RuntimeError("OpenAI response had no message content")
    return content


def _call_anthropic(prompt: str, *, model: str, api_key: str, timeout_s: int = 120) -> str:
    """Call the Anthropic Messages API and return the text response."""
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    response_bytes = post_json_with_retries(
        url=_ANTHROPIC_ENDPOINT,
        body=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout_s=timeout_s,
        max_retries=2,
        retry_base_wait_s=15.0,
        label="analyze",
    )
    envelope = json.loads(response_bytes)
    for block in envelope.get("content") or []:
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            return block["text"]
    raise RuntimeError(f"Anthropic response had no text block. keys={list(envelope)}")


_DISPATCH = {"gemini": _call_gemini, "openai": _call_openai, "anthropic": _call_anthropic}


def _synthesize(prompt: str, *, backend: str, model: str, api_key: str, timeout_s: int = 120) -> str:
    return _DISPATCH[backend](prompt, model=model, api_key=api_key, timeout_s=timeout_s)


def analyze_run(
    workspace: Path,
    *,
    backend: str | None = None,
    model: str | None = None,
) -> str:
    """Full analysis: extract metrics, call the configured text-LLM provider,
    return a markdown report.

    ``backend`` is one of ``gemini`` / ``openai`` / ``anthropic`` (default:
    ``analysis.backend`` config, else gemini); ``model`` overrides the
    per-provider default. Returns the markdown report as a string. Cost is
    ~$0.01-0.05 per call.
    """
    backend, model = _resolve_backend_and_model(backend, model)
    api_key = _resolve_api_key(backend)
    analysis = extract_run_metrics(workspace)

    prompt = _SYNTHESIS_PROMPT.format(
        metrics_json=_build_metrics_json(analysis),
        judge_scores_and_fixes=_build_judge_section(analysis),
        per_agent_tool_call_patterns=_build_agent_section(analysis),
        verify_warnings=_build_warnings_section(analysis),
    )

    start = time.monotonic()
    report_text = _synthesize(prompt, backend=backend, model=model, api_key=api_key)
    duration = time.monotonic() - start

    # Prepend a header with run summary
    header = (
        f"# Trajectory Analysis: {analysis.slug}\n\n"
        f"**Score:** {analysis.final_score:.3f} "
        f"({'PASS' if analysis.passed else 'FAIL'})  \n"
        f"**Cost:** ${analysis.total_cost_usd:.4f}  \n"
        f"**Wall time:** {analysis.total_wall_time_s:.0f}s  \n"
        f"**Iterations:** {analysis.iterations}  \n"
        f"**Parts:** {analysis.n_parts}  |  **Joints:** {analysis.n_joints}  \n"
        f"**Analysis model:** {backend}/{model}  |  **Analysis time:** {duration:.1f}s\n\n"
        f"---\n\n"
    )

    return header + report_text
