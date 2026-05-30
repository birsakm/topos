"""CLI-based vision critic — drives a coding-agent CLI (codex / gemini) to
critique BOTH the rendered images AND the source code that produced them.

Why this exists alongside the HTTP-API critics
----------------------------------------------
HTTP-API critics (``openai_vision``, ``gemini_vision``) see only the rendered
images. They produce feedback like "the handle looks too plain — add bevels".

CLI critics run inside the project workspace. They have agent-grade tooling
(Read, Glob) which means they can correlate "the handle looks plain" with
"yes, ``src/parts/handle.py:23`` just calls ``primitive_cube_add`` once".
Resulting feedback is grounded in source: "handle.py:23 — replace the
single primitive_cube with a composite cylinder+stubs per the D-handle
pattern in topos_furniture_hardware skill". More actionable, but slower
(multi-turn agent loop) and pricier than a single API call.

Selection: rubric.judge_backend ∈ {``codex_cli``, ``gemini_cli``}.

Image handling: rendered images are staged INTO the workspace at a known
relative path (``_critic_images/``) so the CLI agent reads them via Read.
We don't depend on per-CLI image-attachment flags (which vary by version).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from ...backends.base import AgentBackend
from .._json_extract import extract_first_json_dict
from .base import CriticInputs, CriticResult, Rubric
from .critic_utils import build_critic_prompt, materialise_score


@dataclass
class CliVisionCritic:
    """Generic CLI-driven vision critic. The concrete backend (codex_cli or
    gemini_cli) is passed in by from_config()."""
    backend: AgentBackend
    timeout_s: int = 300
    image_subdir: str = "_critic_images"
    label: str = "cli"          # appears in trajectory + prompts (e.g. "codex" / "gemini")

    def evaluate(self, inputs: CriticInputs, rubric: Rubric) -> CriticResult:
        if not inputs.images:
            raise ValueError(f"{type(self).__name__}.evaluate: no images supplied")

        # Decide a workspace for the CLI agent. The critic's metadata may
        # carry workspace_path — if so, use it (the project workspace, where
        # src/ files live). Otherwise stage everything in a fresh scratch dir.
        meta = inputs.metadata or {}
        ws_path = meta.get("workspace_path")
        if ws_path:
            workspace = Path(ws_path).resolve()
        else:
            import tempfile
            workspace = Path(tempfile.mkdtemp(prefix=f"topos-{self.label}-critic-")).resolve()

        # Per-call paths to avoid collisions when N judges run in parallel.
        # The runner injects ``_task_id`` + ``_trajectory_dir`` into metadata
        # (see Runner._run_tool); when present, we use:
        #   - ``<workspace>/_critic_images_<task_id>/`` for image staging
        #   - the runner's pre-allocated ``trajectories/<task_id>_iter<N>/``
        #     for transcript output
        # so each judge's artifacts land in distinct, postmortem-preservable
        # paths. Falls back to the legacy fixed names when called outside the
        # runner (tests, scripts).
        task_id = meta.get("_task_id")
        trajectory_dir: Path | None = None
        traj_meta = meta.get("_trajectory_dir")
        if traj_meta:
            trajectory_dir = Path(traj_meta).resolve()

        img_subdir = (
            f"{self.image_subdir}_{task_id}" if task_id else self.image_subdir
        )
        img_dir = workspace / img_subdir
        img_dir.mkdir(parents=True, exist_ok=True)
        staged_names: list[str] = []
        try:
            for i, img in enumerate(inputs.images):
                dst = img_dir / f"render_{i:02d}{img.suffix.lower() or '.png'}"
                shutil.copy(img, dst)
                staged_names.append(f"{img_subdir}/{dst.name}")

            prompt = _build_prompt(
                staged_names, rubric, metadata=meta,
                workspace_aware=bool(ws_path),
            )
            result = self.backend.run(
                prompt=prompt,
                workspace=workspace,
                allowed_tools=["Read", "Glob"],
                mcp_servers=[],
                timeout_s=self.timeout_s,
                trajectory_dir=trajectory_dir,  # None → backend defaults to <ws>/.trajectory
            )
            if not result.success:
                raise RuntimeError(
                    f"{self.label}_critic CLI run failed: exit_reason={result.exit_reason}\n"
                    f"stdout (tail): {result.stdout[-500:]}"
                )

            raw = result.transcript_path.read_text(encoding="utf-8") if result.transcript_path.is_file() else result.stdout
            parsed = _extract_json(raw)
            return _materialise(parsed, rubric, raw, cost_usd=result.cost_usd, usage=result.usage)
        finally:
            # Don't blow away the user's real workspace; only clean up the
            # staged images dir (or our temp dir if we created one).
            if not ws_path:
                shutil.rmtree(workspace, ignore_errors=True)
            else:
                shutil.rmtree(img_dir, ignore_errors=True)


def make_codex_cli_critic(config: dict | None = None) -> CliVisionCritic:
    from ...backends.codex_cli import CodexCLIBackend
    backend = CodexCLIBackend.from_config(config)
    timeout_s = int((config or {}).get("timeout_s", 300))
    return CliVisionCritic(backend=backend, timeout_s=timeout_s, label="codex")


def make_gemini_cli_critic(config: dict | None = None) -> CliVisionCritic:
    from ...backends.gemini_cli import GeminiCLIBackend
    backend = GeminiCLIBackend.from_config(config)
    timeout_s = int((config or {}).get("timeout_s", 300))
    return CliVisionCritic(backend=backend, timeout_s=timeout_s, label="gemini")


def _build_prompt(
    image_relpaths: list[str], rubric: Rubric, metadata: dict | None = None,
    *, workspace_aware: bool,
) -> str:
    """Render the CLI-critic prompt. Unlike the API-based critics, we ask the
    model to use its Read tool to inspect images AND optionally the source.
    Adds a workspace-aware paragraph to the role hint when the run is rooted
    in a real project workspace (not a /tmp stage)."""
    md = metadata or {}
    extra_context = md.get("role_hint", "")
    if workspace_aware:
        extra_context = (extra_context + "\n\n" if extra_context else "") + (
            "You are running INSIDE the project workspace. In addition to the "
            "rendered images, you may use Read/Glob to inspect any source file "
            "under `src/` that's relevant to your critique — this is encouraged "
            "for grounding visual feedback in concrete code locations. "
            "If you cite a fix, prefer naming the file+line that needs editing."
        )
    return build_critic_prompt(
        rubric,
        image_names=image_relpaths,
        role_hint=extra_context or None,
    )


def _try_brace_repair(s: str, *, max_appends: int = 6) -> dict | None:
    """LLM-output tolerance: occasionally the model emits structurally-complete
    JSON missing 1-2 trailing braces (observed on optimus_prime_v3 left_hand
    judge — output_tokens=2290 well below the 64K max, stop_reason=end_turn,
    yet final ``}`` for the outer object dropped). The shape is repairable as
    long as the *content* is otherwise intact: tally unclosed ``{`` / ``[``
    (string-aware so curlies inside JSON strings don't get counted) and
    append the missing closes in LIFO order until parse succeeds. Caps at
    ``max_appends`` so we don't paper over genuinely corrupt output.
    """
    if not s.strip():
        return None
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()
    if not stack or len(stack) > max_appends:
        return None
    suffix = "".join(reversed(stack))
    try:
        v = json.loads(s + suffix)
    except json.JSONDecodeError:
        return None
    return v if isinstance(v, dict) else None


def _normalize_critic_shape(parsed: dict) -> dict:
    """Lift mis-nested top-level fields back to the root.

    LLMs occasionally emit ``overall_score`` / ``passed`` / ``suggested_fixes``
    *inside* ``per_criterion`` instead of as siblings (observed on
    optimus_prime_v3 left_hand). Criteria are dicts with a ``score`` field;
    these non-criteria intruders are not. Promote them and strip from
    per_criterion so downstream ``materialise_score`` sees the canonical
    schema.
    """
    pc = parsed.get("per_criterion")
    if not isinstance(pc, dict):
        return parsed
    promoted: list[str] = []
    for key in ("overall_score", "passed", "suggested_fixes"):
        if key in pc and key not in parsed:
            value = pc[key]
            # Only promote if it doesn't look like a criterion (criteria are
            # dicts with a numeric "score" field).
            if not (isinstance(value, dict) and "score" in value):
                parsed[key] = value
                promoted.append(key)
    for key in promoted:
        del pc[key]
    return parsed


def _extract_json(text: str) -> dict:
    """Find the JSON critique inside CLI output (transcript or stdout).

    Delegates to ``extract_first_json_dict`` with ``per_criterion`` as the
    distinguishing key — that's the rubric-shaped field that proves we have
    the actual critique payload, not a wrapping envelope (claude
    ``result``/``messages``, gemini ``response``, codex ``output``/``content``).

    Falls back to ``_try_brace_repair`` on the unwrapped inner payload when
    the strict extractor fails, since LLMs occasionally drop a trailing brace.
    Normalises the shape (lifting mis-nested top-level fields) before return.
    """
    if not text.strip():
        raise ValueError("empty critic transcript")
    parsed = extract_first_json_dict(text, required_keys=("per_criterion",))
    if parsed is not None:
        return _normalize_critic_shape(parsed)
    # Repair path: peel the claude/codex/gemini outer envelope and brace-repair
    # the inner payload. Try the outer envelope first (it's almost always
    # well-formed; the inner LLM-emitted JSON is what gets truncated).
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        outer = None
    if isinstance(outer, dict):
        for key in ("result", "response", "output", "content"):
            inner = outer.get(key)
            if isinstance(inner, str):
                repaired = _try_brace_repair(inner)
                if repaired is not None and "per_criterion" in repaired:
                    return _normalize_critic_shape(repaired)
    # Last resort: brace-repair the whole text (when there's no envelope).
    repaired = _try_brace_repair(text)
    if repaired is not None and "per_criterion" in repaired:
        return _normalize_critic_shape(repaired)
    raise ValueError(
        f"could not extract critic JSON from CLI output. head: {text[:300]!r}"
    )


def _materialise(parsed: dict, rubric: Rubric, raw: str, *,
                  cost_usd: float = 0.0, usage: dict | None = None) -> CriticResult:
    passed, overall, per_criterion, fixes = materialise_score(parsed, rubric)
    return CriticResult(
        passed=passed,
        overall_score=overall,
        per_criterion=per_criterion,
        suggested_fixes=fixes,
        raw_response=raw,
        cost_usd=cost_usd,
        usage=usage or {},
    )
