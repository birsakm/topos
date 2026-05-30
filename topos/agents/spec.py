"""Spec agent: converts a natural-language prompt into a structured project
spec that the framework can use to bootstrap an end-to-end pipeline.

Wraps a single ``ClaudeCLIBackend.run`` call with the spec-agent prompt
template and JSON-extraction logic. Output schema documented in
``topos/prompts/system/spec_agent.md.j2``.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from ..backends.claude_cli import ClaudeCLIBackend
from ..prompts import render
from ._json_extract import extract_first_json_dict


def _load_design_references() -> list[dict[str, str]]:
    """Discover all design-reference markdown files under
    ``topos/prompts/in_context_examples/`` (the plugin path for real-world
    extras_md vocabulary).

    Returns ``[{"name": <stem>, "body": <file content>}, ...]``, sorted
    by filename. Files whose name starts with ``_`` are skipped (reserved
    for ``_README.md`` and future ``_index.md``-style meta files).

    Phase 1: returns every file. Phase 2 will optionally filter by
    YAML-frontmatter ``applies_to`` keywords against the user prompt;
    that change is non-breaking — the returned shape stays the same.
    """
    refs_dir = resources.files("topos").joinpath("prompts").joinpath("in_context_examples")
    if not refs_dir.is_dir():
        return []
    out: list[dict[str, str]] = []
    for entry in sorted(refs_dir.iterdir(), key=lambda p: p.name):
        if entry.suffix != ".md" or entry.name.startswith("_"):
            continue
        out.append({"name": entry.stem, "body": entry.read_text(encoding="utf-8")})
    return out


@dataclass
class PartSpec:
    name: str
    lower_name: str
    extras_md: str


@dataclass
class ProjectSpec:
    slug: str
    domain: str               # "articulated"
    robot_name: str
    intent_md: str
    parts: list[PartSpec]

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectSpec":
        parts = [PartSpec(**p) for p in d.get("parts", [])]
        return cls(
            slug=d["slug"],
            domain=d.get("domain", "articulated"),
            robot_name=d.get("robot_name", d["slug"]),
            intent_md=d["intent_md"],
            parts=parts,
        )


def _extract_spec_json(envelope_text: str) -> dict:
    """Pull the spec JSON out of a claude --output-format=json envelope.

    Delegates to ``extract_first_json_dict``, which already knows how to walk
    ``result`` / ``messages[].text`` / ``events[].text`` envelope shapes,
    strip markdown fences, and balanced-brace-scan free text. The required
    keys ``slug`` and ``intent_md`` are the most distinctive top-level fields
    of the spec schema (see ``topos/prompts/system/spec_agent.md.j2``).
    """
    if not envelope_text.strip():
        raise ValueError("empty spec agent transcript")
    spec = extract_first_json_dict(envelope_text, required_keys=("slug", "intent_md"))
    if spec is not None:
        return spec
    try:
        envelope = json.loads(envelope_text)
        keys: object = list(envelope) if isinstance(envelope, dict) else type(envelope).__name__
    except json.JSONDecodeError:
        keys = "(transcript not JSON)"
    raise ValueError(
        f"could not extract spec JSON. envelope keys: {keys}; "
        f"head: {envelope_text[:300]!r}"
    )


def run_spec_agent(
    user_prompt: str,
    *,
    backend: ClaudeCLIBackend | None = None,
    timeout_s: int = 600,
) -> ProjectSpec:
    """Call the spec agent and return a typed ``ProjectSpec``.

    The agent runs in a throwaway temp workspace with no tools; the only job is
    to think and output structured JSON.
    """
    if backend is None:
        backend = ClaudeCLIBackend.from_config()

    prompt = render(
        "system/spec_agent.md.j2",
        user_prompt=user_prompt,
        design_references=_load_design_references(),
    )

    with tempfile.TemporaryDirectory(prefix="topos-spec-") as td:
        scratch = Path(td)
        result = backend.run(
            prompt=prompt,
            workspace=scratch,
            # WebSearch + WebFetch grant the spec agent the ability to do
            # ONE-TIME research up front: when the user prompt references a
            # recognizable real-world object / franchise / brand (e.g.
            # "Optimus Prime", "Eames lounge chair", "PW1100G turbofan"), the
            # spec agent's prompt template instructs it to look up real
            # references and DISTILL them into intent.md / extras_md as
            # concrete facts (proportions, kibble, colors) — so the
            # downstream design + part agents READ those facts instead of
            # each independently searching for the same thing. The single
            # search at spec time replaces N≥21 redundant searches at
            # part-agent time (observed cost factor ~5x).
            allowed_tools=["WebSearch", "WebFetch"],
            mcp_servers=[],
            timeout_s=timeout_s,
            trajectory_dir=scratch / ".trajectory",
        )
        if not result.success:
            raise RuntimeError(
                f"spec agent failed: exit_reason={result.exit_reason}\n"
                f"stdout (tail): {result.stdout[-500:]}"
            )
        raw = result.transcript_path.read_text(encoding="utf-8") if result.transcript_path.is_file() else ""
        spec_dict = _extract_spec_json(raw)

    return ProjectSpec.from_dict(spec_dict)
