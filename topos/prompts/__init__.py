"""Topos prompts package.

Prompt files (.md, .md.j2) ship as package data. The ``load_text`` and
``render`` helpers below resolve a relative path inside this package and
return its content — useful for the framework's own runtime prompts that
aren't referenced via ``plan.json`` (system prompts, judge prompts,
fix-loop prompts).

Layout convention:
    topos/prompts/
        system/<...>.md           — framework-level standing prompts
        system/<...>.md.j2        — framework-level templates
        articulated/<...>          — domain-level templates referenced from plan.json
"""

from __future__ import annotations

from importlib import resources


def load_text(rel_path: str) -> str:
    """Read a prompt file (markdown or template source) shipped as
    ``topos.prompts.<rel_path>`` package data. Returns the raw text."""
    ref = resources.files("topos").joinpath("prompts").joinpath(rel_path)
    if not ref.is_file():
        raise FileNotFoundError(f"prompt resource not found: topos/prompts/{rel_path}")
    return ref.read_text(encoding="utf-8")


def render(rel_path: str, /, **params) -> str:
    """Load a Jinja2 template at ``topos/prompts/<rel_path>`` and render it
    with ``params``. ``StrictUndefined`` so missing params fail loudly."""
    from jinja2 import Environment, StrictUndefined
    template_text = load_text(rel_path)
    env = Environment(
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    return env.from_string(template_text).render(**params)
