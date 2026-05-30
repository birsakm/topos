"""Topos skills package.

A skill is a folder under ``topos/skills/<name>/`` containing at least a
``SKILL.md`` entry doc — a self-contained "how to do X correctly" capability
bundle. Skills are materialized into ``workspace/.topos_skills/`` by the
runner; the agent autonomously reads only the SKILL bodies whose
``when_to_use`` matches its task. Plan.json declares ``skills: [<name>]``
per AgentTask.

The ``load_skill_md(name)`` helper resolves a skill via
``importlib.resources`` so plans survive ``topos init`` copying them into
arbitrary workspaces.

Skill naming convention: ``topos_<short_capability>`` (snake_case, prefix
so future user-installed skills from other sources don't collide).
"""

from __future__ import annotations

from importlib import resources


def load_skill_md(name: str) -> str:
    """Return the SKILL.md content for the skill at ``topos/skills/<name>/``.

    Raises FileNotFoundError if no such skill exists.
    """
    ref = resources.files("topos").joinpath("skills").joinpath(name).joinpath("SKILL.md")
    if not ref.is_file():
        raise FileNotFoundError(
            f"skill {name!r}: no SKILL.md at topos/skills/{name}/SKILL.md"
        )
    return ref.read_text(encoding="utf-8")


def list_skills() -> list[str]:
    """List all installed skill names (folders under topos/skills/ that contain a SKILL.md)."""
    skills_root = resources.files("topos").joinpath("skills")
    names: list[str] = []
    for entry in skills_root.iterdir():
        try:
            if entry.is_dir() and entry.joinpath("SKILL.md").is_file():
                names.append(entry.name)
        except (OSError, FileNotFoundError):
            continue
    return sorted(names)
