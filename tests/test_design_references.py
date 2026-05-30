"""Discovery + injection of the design-reference plugin path.

The spec agent reads worked real-world anchor examples from
``topos/prompts/in_context_examples/*.md`` and injects them into the prompt.
Adding a new category should be a file drop, no code change."""

from __future__ import annotations

from topos.agents.spec import _load_design_references
from topos.prompts import render


def test_loads_at_least_furniture_and_engines():
    refs = _load_design_references()
    names = {r["name"] for r in refs}
    assert "furniture" in names, f"furniture reference missing; found {names}"
    assert "engines" in names, f"engines reference missing; found {names}"


def test_skips_underscore_prefixed_meta_files():
    refs = _load_design_references()
    names = [r["name"] for r in refs]
    # _README.md exists in the references dir but must NOT be loaded as a
    # reference (it's instructions for maintainers, not vocabulary).
    assert "_README" not in names
    assert "README" not in names
    # Verify nothing starts with underscore
    assert all(not n.startswith("_") for n in names), names


def test_each_reference_has_non_empty_body():
    refs = _load_design_references()
    for r in refs:
        body = r["body"]
        assert body.strip(), f"reference {r['name']!r} has empty body"
        # The convention is a markdown bad/good anchor table — verify both
        # the table marker AND the bad/good axis. Earlier this asserted
        # only `'"' in body` which trivially passed on any text containing
        # a quote; that didn't actually test that the file followed the
        # anchor-table convention.
        assert "|" in body, (
            f"reference {r['name']!r} has no markdown table — convention "
            "is bad/good anchor pairs in a table"
        )
        body_lc = body.lower()
        has_axis = ("vague" in body_lc and "concrete" in body_lc) or (
            "bad" in body_lc and "good" in body_lc
        )
        assert has_axis, (
            f"reference {r['name']!r} table lacks a bad/good (or vague/concrete) "
            "contrast axis"
        )
        assert len(body) > 300, (
            f"reference {r['name']!r} body is only {len(body)} chars; "
            "should contain at least a few real anchor pairs"
        )


def test_spec_agent_prompt_includes_all_references():
    """The rendered spec_agent prompt should contain each reference's body verbatim."""
    refs = _load_design_references()
    prompt = render(
        "system/spec_agent.md.j2",
        user_prompt="dummy",
        design_references=refs,
    )
    for r in refs:
        # Pick a distinctive substring from each reference body
        # (the H1 line which starts with "# ")
        first_line = r["body"].splitlines()[0]
        assert first_line in prompt, (
            f"reference {r['name']!r} not present in rendered prompt; "
            f"missing first line: {first_line!r}"
        )


def test_spec_agent_prompt_renders_with_empty_references():
    """The Jinja template must not StrictUndefined-crash if no references
    are installed (e.g. someone deletes the whole directory)."""
    prompt = render(
        "system/spec_agent.md.j2",
        user_prompt="dummy",
        design_references=[],
    )
    # The graceful-empty message should appear
    assert "No reference files installed" in prompt
