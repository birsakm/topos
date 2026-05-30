"""``fix_part.md.j2`` should adapt to the judge feedback:

- When any criterion scores below 0.40 OR feedback mentions a visual artifact
  (floating, stray, intersect, misalign, magenta, ...), the fix agent is told
  to Read the actual rendered PNGs + grep build stderr for FLOATING_WARN /
  COLLISION_WARN warnings. Vision-grounded fix.
- When feedback is purely about additive detail (e.g. "bevel too narrow",
  "add inset"), the image-Read step is skipped to save tokens — the text is
  enough.

This test pins both branches of the template. Without these assertions, a
template edit that breaks the conditional silently degrades every fix-loop
iter on the visual-artifact path."""

from __future__ import annotations

from topos.prompts import render as render_prompt


def _render(per_criterion: dict, **kw) -> str:
    return render_prompt(
        "system/fix_part.md.j2",
        iteration=1,
        part_name="Torso",
        overall_score=0.50,
        per_criterion=per_criterion,
        suggested_fixes=kw.get("suggested_fixes", []),
    )


# --- Visual-artifact branch -----------------------------------------------


def test_floating_keyword_in_feedback_triggers_image_read():
    """The torso fix-loop in the Optimus run got judge feedback like
    'four short floating line segments to the right of the main mesh'.
    Such language must trigger the image-Read step so the fix agent can
    locate the bad geometry visually rather than guessing from text."""
    out = _render({
        "geometry_detail": {"score": 0.55, "feedback": "Some good detail visible."},
        "no_obvious_errors": {
            "score": 0.40,
            "feedback": "Render contains four short floating line segments to the right of the main mesh.",
        },
    })
    # The image-Read instruction must be present
    assert "artifacts/parts_render/Torso/view_front_low.png" in out
    # And the build-warning surfacing must come from tool output.json files
    # (not agent stderr.log — agent stderr is the agent's own stderr, contract
    # warnings live in the tool subprocess that ran build.py).
    assert "trajectories/" in out
    # Read from the tool output.jsons — these are where the warnings field lives
    assert "tool_export_glb" in out or "tool_render_multiview" in out
    assert "output.json" in out
    # Filter the warnings by the part name so the agent only sees its own
    assert "Torso" in out


def test_very_low_score_triggers_image_read_even_without_keyword():
    """Score below 0.40 indicates the visual is severely wrong — read the
    image even if the judge didn't volunteer artifact keywords."""
    out = _render({
        "recognizable_as_role": {"score": 0.30, "feedback": "Geometry is too simple."},
        "geometry_detail":      {"score": 0.65, "feedback": "Some detail present."},
    })
    assert "artifacts/parts_render/Torso" in out
    assert ".png" in out


def test_intersect_and_zfight_keywords_trigger():
    """Multiple visual-artifact keywords must all be picked up."""
    for kw in ("z-fight", "intersect", "magenta", "missing texture",
               "inverted normal", "disconnected"):
        out = _render({
            "no_obvious_errors": {"score": 0.50, "feedback": f"render shows {kw} issues."},
        })
        assert "artifacts/parts_render/Torso" in out, (
            f"keyword {kw!r} should have triggered image-Read step but did not"
        )


# --- Additive-only branch (no image Read) ---------------------------------


def test_only_additive_feedback_skips_image_read():
    """Bevel-too-narrow / missing-inset feedback is fixable from text alone.
    Skipping the PNG Read saves ~500-1000 tokens per fix call."""
    out = _render({
        "geometry_detail":  {"score": 0.55, "feedback": "Bevel width is too narrow at 0.002m — bump to 0.008m."},
        "material_quality": {"score": 0.50, "feedback": "Add a procedural noise texture."},
    })
    # The image-Read step should be SKIPPED (no artifacts/parts_render path)
    assert "artifacts/parts_render/Torso/view_" not in out, (
        "additive-only fixes should NOT pull in render PNGs (token waste)"
    )
    # And the template should explicitly say it's skipping
    assert "Skip the PNG Read" in out or "skip the PNG" in out.lower()
