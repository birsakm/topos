"""Tests for the image_gen backend layer + generate_texture_image tool.

We don't make real Gemini API calls here — instead:
- StubBackend exercises the full Protocol + PNG round-trip
- GeminiBackend is exercised via mocked urlopen
- The tool is exercised against StubBackend (force backend=stub via env)
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from topos.agents.image_gen.base import ImageGenBackend, ImageGenResult, make_backend
from topos.agents.image_gen.stub import StubBackend
from topos.agents.image_gen.gemini import GeminiBackend
from topos.tools.registry import _ensure_default_tools_imported, get


# ---------------- Protocol + stub ----------------

def test_stub_backend_implements_protocol():
    backend = StubBackend()
    assert isinstance(backend, ImageGenBackend)


def test_stub_backend_generates_valid_png(tmp_path):
    backend = StubBackend()
    result = backend.generate("prompt unused by stub", size=128)
    assert result.success is True
    assert result.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    out = tmp_path / "out.png"
    out.write_bytes(result.png_bytes)
    assert out.stat().st_size > 100  # non-trivial size


def test_make_backend_factory_stub(monkeypatch):
    # Tests must opt in: stub is gated to prevent agents (gemini-3-flash
    # observed self-rationalizing --backend stub for real generation) from
    # silently producing noise when a real API key is available.
    monkeypatch.setenv("TOPOS_ALLOW_STUB_IMAGE_GEN", "1")
    backend = make_backend("stub")
    assert backend.name == "stub"


def test_make_backend_stub_blocked_when_real_key_available(monkeypatch):
    """When a real backend's API key is configured, requesting stub fails
    fast. This stops gemini-3-flash and similar agents from accidentally
    writing RGB noise PNGs as 'textures'."""
    monkeypatch.delenv("TOPOS_ALLOW_STUB_IMAGE_GEN", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    with pytest.raises(ValueError, match="image_gen backend 'stub' is blocked"):
        make_backend("stub")


def test_make_backend_stub_allowed_when_no_real_key_and_no_optin(monkeypatch, tmp_path):
    """When NEITHER a real key is configured NOR the opt-in is set, stub is
    still picked — this is the "developer running tests on a clean machine"
    case, and forcing them to set the opt-in would be friction. The guard's
    purpose is to block accidental stub-on-prod, not stub-on-clean-dev."""
    monkeypatch.delenv("TOPOS_ALLOW_STUB_IMAGE_GEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    # Point XDG_CONFIG_HOME at an empty dir so the user's config.yaml
    # (which may legitimately carry image_gen.gemini.api_key) doesn't leak
    # into this test. config.user_config_path() reads XDG_CONFIG_HOME.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    backend = make_backend("stub")
    assert backend.name == "stub"


def test_make_backend_factory_unknown_name():
    with pytest.raises(ValueError, match="unknown image_gen backend"):
        make_backend("nope_not_a_backend")


def test_stub_clamps_image_size():
    """The stub backend clamps any requested size to <=256px so tests/CI never
    pay to noise-generate a huge image in pure Python. A 1024 request yields a
    256x256 PNG. (Tests the real clamp in StubBackend.generate, not the raw
    _make_noise_png, which is unbounded by design.)"""
    from topos.agents.image_gen.stub import StubBackend
    result = StubBackend().generate("x", size=1024)
    assert result.success
    png = result.png_bytes
    assert png.startswith(b"\x89PNG")
    # IHDR width/height: two big-endian uint32 after the 8-byte signature +
    # 4-byte length + 4-byte "IHDR" type → bytes [16:24].
    import struct
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (256, 256)


# ---------------- Gemini auth + request building ----------------

def test_gemini_no_api_key_returns_failed_result():
    """generate() must NOT raise when key missing — return failed result so
    a missing texture doesn't crash the build."""
    backend = GeminiBackend(api_key=None)
    result = backend.generate("test prompt")
    assert isinstance(result, ImageGenResult)
    assert result.success is False
    assert "api_key not configured" in (result.error or "")
    assert result.png_bytes == b""


def test_gemini_build_request_text_only():
    backend = GeminiBackend(api_key="fake")
    payload = backend._build_request("hello world", condition_image=None)
    assert payload["contents"][0]["parts"][0]["text"] == "hello world"
    assert len(payload["contents"][0]["parts"]) == 1  # no condition image


def test_gemini_build_request_with_condition_bytes():
    backend = GeminiBackend(api_key="fake")
    payload = backend._build_request("sketch-conditioned", condition_image=b"\x89PNGfakebytes")
    parts = payload["contents"][0]["parts"]
    assert len(parts) == 2
    assert parts[0]["text"] == "sketch-conditioned"
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == b"\x89PNGfakebytes"


def test_gemini_extract_png_finds_inline_data():
    backend = GeminiBackend(api_key="fake")
    fake_png = b"\x89PNG" + b"\x00" * 100
    response = {
        "candidates": [{
            "content": {
                "parts": [
                    {"text": "Here is your image."},
                    {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(fake_png).decode()}},
                ]
            }
        }]
    }
    extracted = backend._extract_png(response)
    assert extracted == fake_png


def test_gemini_extract_png_raises_on_no_image():
    backend = GeminiBackend(api_key="fake")
    response_text_only = {
        "candidates": [{
            "content": {"parts": [{"text": "Sorry I refuse."}]}
        }]
    }
    with pytest.raises(RuntimeError, match="no image data"):
        backend._extract_png(response_text_only)


# ---------------- Gemini end-to-end via mocked urlopen ----------------

def test_gemini_generate_happy_path_with_mock():
    backend = GeminiBackend(api_key="fake")
    fake_png = b"\x89PNG" + b"\x00" * 50
    fake_response = json.dumps({
        "candidates": [{
            "content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(fake_png).decode()}}
            ]}
        }]
    }).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_response
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = lambda self, *args: None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = backend.generate("a red ball")
    assert result.success is True
    assert result.png_bytes == fake_png
    assert result.model == "gemini-3.1-flash-image-preview"
    # Cost is computed inline from IMAGE_PRICING — Nano Banana 2 = $0.039/img.
    # Without this, image-gen would silently under-report by 100%.
    from topos.backends._pricing import gemini_image_cost_usd
    assert result.cost_usd == pytest.approx(
        gemini_image_cost_usd("gemini-3.1-flash-image-preview")
    )
    assert result.cost_usd > 0


def test_gemini_generate_failed_call_has_zero_cost():
    """Per-image billing kicks in on success only — failed HTTP / parse paths
    must not phantom-charge."""
    backend = GeminiBackend(api_key="fake", retry_base_wait_s=0.01)
    import urllib.error
    err = urllib.error.HTTPError(
        url="https://x", code=429, msg="Too Many Requests",
        hdrs=None, fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=err):
        result = backend.generate("test")
    assert result.success is False
    assert result.cost_usd == 0.0


def test_gemini_generate_handles_http_error():
    # 10ms retry_base_wait_s vs production 5s — keeps the retry contract
    # the same (still exhausts max_retries) but runs in ~30ms instead of
    # the 15s the production defaults would impose on this test.
    backend = GeminiBackend(api_key="fake", retry_base_wait_s=0.01)
    import urllib.error
    err = urllib.error.HTTPError(
        url="https://x", code=429, msg="Too Many Requests",
        hdrs=None, fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=err):
        result = backend.generate("test")
    assert result.success is False
    assert "HTTP 429" in result.error


def _mock_resp(body_bytes):
    m = MagicMock()
    m.read.return_value = body_bytes
    m.__enter__ = lambda self: self
    m.__exit__ = lambda self, *a: None
    return m


def _png_response(fake_png: bytes) -> bytes:
    return json.dumps({"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(fake_png).decode()}}
    ]}}]}).encode()


_EMPTY_RESPONSE = json.dumps({"candidates": [{"content": {"parts": [{"text": ""}]}}]}).encode()


def test_gemini_retries_on_empty_response_then_succeeds():
    """A 200 with NO image part (the observed Nano-Banana flake) is transient —
    it must be RETRIED, not failed. First attempt empty, second has an image →
    success. Without the retry, image-default would leave the part flat for a
    transient blip (which is exactly what happened to leg2 in the stool run)."""
    backend = GeminiBackend(api_key="fake", retry_base_wait_s=0.01)
    fake_png = b"\x89PNG" + b"\x00" * 50
    with patch("urllib.request.urlopen",
               side_effect=[_mock_resp(_EMPTY_RESPONSE), _mock_resp(_png_response(fake_png))]):
        result = backend.generate("a red ball")
    assert result.success is True
    assert result.png_bytes == fake_png


def test_gemini_empty_response_exhausts_retries_then_fails_cleanly():
    """If EVERY attempt is empty, fail after max_retries with the parse error
    (degrade-to-flat then happens one layer up in generate_texture_image)."""
    backend = GeminiBackend(api_key="fake", retry_base_wait_s=0.01, max_retries=2)
    with patch("urllib.request.urlopen", side_effect=[_mock_resp(_EMPTY_RESPONSE)] * 3):
        result = backend.generate("x")
    assert result.success is False
    assert "no image data" in result.error
    assert result.cost_usd == 0.0


_RECITATION_RESPONSE = json.dumps({
    "candidates": [{"finishReason": "IMAGE_RECITATION", "content": {"parts": []}}]
}).encode()


def test_extract_png_raises_terminal_on_recitation():
    """A no-image 200 whose finishReason is a deterministic refusal must raise
    the terminal variant (not plain RuntimeError) so generate() fails fast."""
    from topos.agents.image_gen.gemini import _TerminalImageGenError
    backend = GeminiBackend(api_key="fake")
    with pytest.raises(_TerminalImageGenError, match="IMAGE_RECITATION"):
        backend._extract_png(json.loads(_RECITATION_RESPONSE))


def test_gemini_recitation_fails_fast_without_retry():
    """IMAGE_RECITATION is deterministic — retrying the identical prompt always
    re-fails. The backend must NOT burn its retry budget on it: urlopen is
    called exactly ONCE and the result is a clean failure (degrade-to-flat
    happens one layer up). This is the wasted-retry bug the fix closes."""
    backend = GeminiBackend(api_key="fake", retry_base_wait_s=0.01, max_retries=2)
    with patch("urllib.request.urlopen", return_value=_mock_resp(_RECITATION_RESPONSE)) as m:
        result = backend.generate("seamless tileable black leather, fine pebbled grain, 4k")
    assert result.success is False
    assert m.call_count == 1, "recitation must not be retried"
    assert "refused" in (result.error or "").lower()


def test_gemini_empty_still_retries_not_misclassified_as_terminal():
    """Guard against over-broad classification: a genuinely empty 200 (no
    finishReason) stays transient and is retried."""
    backend = GeminiBackend(api_key="fake", retry_base_wait_s=0.01)
    fake_png = b"\x89PNG" + b"\x00" * 50
    with patch("urllib.request.urlopen",
               side_effect=[_mock_resp(_EMPTY_RESPONSE), _mock_resp(_png_response(fake_png))]):
        result = backend.generate("a red ball")
    assert result.success is True


# ---------------- generate_texture_image tool ----------------
#
# Tool was refactored 2026-05-14: input is now (workspace, part_name) — the
# tool reads design.json[parts.<part_name>.texture] itself. The old
# (prompt, output_relpath) signature is gone.


def _write_design(ws: Path, parts: list[dict]) -> Path:
    """Helper: drop a minimal design.json at workspace/src/design.json."""
    design = {"robot_name": "t", "description": "", "joints": [], "parts": parts}
    p = ws / "src" / "design.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(design))
    return p


def test_generate_texture_image_tool_registered():
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")
    assert spec.name == "generate_texture_image"
    assert "workspace" in spec.input_schema["properties"]
    assert "part_name" in spec.input_schema["properties"]
    assert "design_relpath" in spec.input_schema["properties"]
    assert set(spec.input_schema["required"]) == {"workspace", "part_name"}
    # Old single-call inputs MUST be gone — otherwise plan.json authors
    # could still pass prompt+path and bypass the design.json contract.
    assert "prompt" not in spec.input_schema["properties"]
    assert "output_relpath" not in spec.input_schema["properties"]


def test_generate_texture_image_tool_image_kind_stub_writes_png(tmp_path, monkeypatch):
    """kind='image' + stub backend: PNG materializes at the design.json's
    image_relpath; usage shape is consistent."""
    monkeypatch.setenv("TOPOS_ALLOW_STUB_IMAGE_GEN", "1")
    _write_design(tmp_path, [{
        "name": "Frame",
        "texture": {
            "kind": "image",
            "prompt": "seamless walnut",
            "image_relpath": "src/textures/frame.png",
        },
    }])
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")
    out = spec.func(workspace=str(tmp_path), part_name="Frame", backend="stub")

    assert out["success"] is True
    assert out["kind"] == "image"
    assert out["image_path"] == "src/textures/frame.png"
    assert out["model"] == "stub"
    full_path = tmp_path / "src" / "textures" / "frame.png"
    assert full_path.is_file()
    assert full_path.read_bytes().startswith(b"\x89PNG")
    assert out["cost_usd"] == 0.0   # stub doesn't bill
    assert out["usage"] == {"model": "stub", "n_images": 1}


def test_generate_texture_image_tool_image_kind_surfaces_gemini_cost(tmp_path, monkeypatch):
    """End-to-end: design.json → tool → Gemini backend → cost in return dict.

    This is THE invariant chunk D is supposed to lock in — a single
    image-gen ToolTask must surface its USD cost so the runner folds it
    into TaskResult.cost_usd.
    """
    _write_design(tmp_path, [{
        "name": "Handle1",
        "texture": {
            "kind": "image",
            "prompt": "polished gilded brass with floral relief, 4k",
            "image_relpath": "src/textures/handle.png",
        },
    }])

    fake_png = b"\x89PNG" + b"\x00" * 50
    fake_response = json.dumps({
        "candidates": [{
            "content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(fake_png).decode()}}
            ]}
        }]
    }).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_response
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = lambda self, *args: None

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")

    from topos import config as cfg
    from topos.backends._pricing import gemini_image_cost_usd
    with patch.object(cfg, "load_effective_config", return_value={
        "image_gen": {"default": "gemini",
                       "gemini": {"api_key": "fake-key-for-test",
                                  "model": "gemini-3.1-flash-image-preview"}}
    }), patch("urllib.request.urlopen", return_value=mock_resp):
        out = spec.func(workspace=str(tmp_path), part_name="Handle1")

    assert out["success"] is True
    assert out["kind"] == "image"
    assert out["cost_usd"] == pytest.approx(
        gemini_image_cost_usd("gemini-3.1-flash-image-preview")
    )
    assert out["cost_usd"] > 0
    assert out["usage"] == {"model": "gemini-3.1-flash-image-preview", "n_images": 1}


def test_generate_texture_image_tool_no_prompt_is_no_op(tmp_path, monkeypatch):
    """A texture block with no ``prompt`` → no image-gen, returns success
    kind='flat' cost=0 and DOES NOT call the backend. The part is left flat
    (build.py renders it in color_rgba). Image-gen is keyed solely on a prompt
    being present — there is no longer a ``kind`` field."""
    monkeypatch.setenv("TOPOS_ALLOW_STUB_IMAGE_GEN", "1")
    _write_design(tmp_path, [{
        "name": "Frame",
        "texture": {"material_hint": "dark walnut"},   # no prompt
    }])
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")
    out = spec.func(workspace=str(tmp_path), part_name="Frame", backend="stub")

    assert out["success"] is True
    assert out["kind"] == "flat"
    assert out["cost_usd"] == 0.0
    assert out["usage"]["n_images"] == 0
    assert out["image_path"] == ""
    assert "no texture.prompt" in out["note"]


def test_generate_texture_image_tool_missing_texture_field_is_no_op(tmp_path):
    """A part with no ``texture`` field at all is legal — left flat
    (color_rgba). Tool returns kind='flat', cost=0."""
    _write_design(tmp_path, [{"name": "Frame"}])
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")
    out = spec.func(workspace=str(tmp_path), part_name="Frame")

    assert out["success"] is True
    assert out["kind"] == "flat"
    assert out["cost_usd"] == 0.0


def test_generate_texture_image_tool_part_not_in_design_raises(tmp_path):
    """Scheduling a texture task for a non-existent part is a plan-level bug —
    raise rather than silently no-op so the runner surfaces the typo loudly."""
    _write_design(tmp_path, [{"name": "Frame"}])
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")
    with pytest.raises(ValueError, match="not in"):
        spec.func(workspace=str(tmp_path), part_name="Phantom")


def test_generate_texture_image_tool_design_json_missing_raises(tmp_path):
    """Scheduling texture before design agent ran (or design agent failed)
    is a plan-level bug — surface the missing file loudly."""
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")
    with pytest.raises(FileNotFoundError, match="design.json"):
        spec.func(workspace=str(tmp_path), part_name="Frame")


def test_generate_texture_image_tool_path_is_derived_not_from_design(tmp_path, monkeypatch):
    """The PNG path is DERIVED as src/textures/<snake(part_name)>.png — any
    image_relpath the design agent wrote is ignored, so the two sides
    (image-gen + build.py's _apply_texture) can't drift."""
    monkeypatch.setenv("TOPOS_ALLOW_STUB_IMAGE_GEN", "1")
    _write_design(tmp_path, [{
        "name": "SeatPost",
        # a stale/wrong image_relpath here must NOT be used
        "texture": {"prompt": "seamless tileable aluminium", "image_relpath": "src/textures/WRONG.png"},
    }])
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")
    out = spec.func(workspace=str(tmp_path), part_name="SeatPost", backend="stub")
    assert out["success"] is True
    assert out["kind"] == "image"
    assert out["image_path"] == "src/textures/seat_post.png"     # derived from PascalCase → snake
    assert (tmp_path / "src/textures/seat_post.png").is_file()
    assert not (tmp_path / "src/textures/WRONG.png").exists()


def test_generate_texture_image_tool_malicious_image_relpath_is_neutralized(tmp_path, monkeypatch):
    """A path-traversal image_relpath in design.json is now harmless: the path
    is derived from the (validated) part name, so the malicious field is simply
    ignored and the PNG lands safely under src/textures/."""
    monkeypatch.setenv("TOPOS_ALLOW_STUB_IMAGE_GEN", "1")
    _write_design(tmp_path, [{
        "name": "Frame",
        "texture": {"prompt": "x", "image_relpath": "../../etc/passwd"},
    }])
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")
    out = spec.func(workspace=str(tmp_path), part_name="Frame", backend="stub")
    assert out["success"] is True
    assert out["image_path"] == "src/textures/frame.png"
    assert (tmp_path / "src/textures/frame.png").is_file()


def test_generate_texture_image_tool_no_api_key_degrades_to_flat(tmp_path, monkeypatch):
    """No Gemini key configured + a texture.prompt → DEGRADE, not fail:
    success=True, kind='degraded', cost=0, error surfaced. Image texture is
    best-effort; flat color_rgba is the floor (build.py's _apply_texture falls
    back when the PNG is absent). This must NOT fail the DAG — otherwise, with
    image-gen the default for every part, one transient failure would abort the
    whole build via the subgraph's all(child.success) rule."""
    _write_design(tmp_path, [{
        "name": "Frame",
        "texture": {"prompt": "x"},
    }])
    _ensure_default_tools_imported()
    spec = get("generate_texture_image")
    monkeypatch.delenv("TOPOS__IMAGE_GEN__GEMINI__API_KEY", raising=False)
    from topos import config as cfg
    with patch.object(cfg, "load_effective_config", return_value={
        "image_gen": {"default": "gemini", "gemini": {"api_key": None, "model": "gemini-3.1-flash-image-preview"}}
    }):
        out = spec.func(workspace=str(tmp_path), part_name="Frame")
    assert out["success"] is True
    assert out["kind"] == "degraded"
    assert out["cost_usd"] == 0.0
    assert "api_key not configured" in out["error"]


# ---------------- doctor integration ----------------

def test_doctor_image_gen_check_warns_when_missing_key():
    from topos.doctor import check_image_gen_key
    result = check_image_gen_key({
        "image_gen": {"default": "gemini", "gemini": {"api_key": None}}
    })
    assert result.status == "warn"
    assert "api_key" in result.summary


def test_doctor_image_gen_check_ok_when_key_present():
    from topos.doctor import check_image_gen_key
    result = check_image_gen_key({
        "image_gen": {"default": "gemini", "gemini": {"api_key": "fake-key"}}
    })
    assert result.status == "ok"


def test_doctor_image_gen_check_ok_for_stub_backend():
    from topos.doctor import check_image_gen_key
    result = check_image_gen_key({"image_gen": {"default": "stub"}})
    assert result.status == "ok"


# ---------------- plan generator wires texture skill ----------------

def test_plan_generator_design_agent_has_texture_skill():
    """Post ADR-0008: per-part skills live in expand.py
    (see test_expand_articulated_parts.py). plan_generator only asserts the
    design agent's skill set + that the parts subgraph is wired."""
    from topos.orchestrator.plan_generator import generate_plan_articulated

    plan = generate_plan_articulated("t", "a test articulated object")
    by_id = {t["id"]: t for t in plan["tasks"]}

    design = by_id["01_agent_design"]
    assert "topos_texture_creator" in design["skills"]


def test_plan_generator_emits_subgraph_for_parts():
    """Post ADR-0008: per-part fan-out (agents/textures/judges) is no longer
    in plan.json. Instead, plan_generator emits a single SubgraphTask that
    the runner expands from design.json at runtime."""
    from topos.orchestrator.plan_generator import generate_plan_articulated

    plan = generate_plan_articulated("t", "a test articulated object")
    by_id = {t["id"]: t for t in plan["tasks"]}

    # No per-part tasks at plan time
    assert not any("_agent_part_" in tid for tid in by_id)
    assert not any("_tool_texture_" in tid for tid in by_id)
    assert not any("_tool_judge_part_" in tid for tid in by_id)

    # SubgraphTask present, configured for design-driven expansion
    sg = by_id["02_subgraph_parts"]
    assert sg["kind"] == "subgraph"
    assert sg["expansion_kind"] == "articulated_parts"
    assert sg["expand_from"] == "src/design.json"
    assert sg["deps"] == ["01_agent_design"]

    # build depends on the subgraph (not per-part), runner gates by
    # all-children-complete (see test_runner_subgraph_expansion.py)
    build = by_id["03_agent_build"]
    assert build["deps"] == ["02_subgraph_parts"]


def test_plan_generator_validates_through_plan_schema():
    """Cross-check: the dict plan_generator produces validates through the
    pydantic Plan schema (no extra-fields errors), so `topos make` output
    can be saved + reloaded without surgery."""
    from topos.orchestrator.plan_generator import generate_plan_articulated
    from topos.orchestrator.plan_schema import Plan, topo_sort

    plan_dict = generate_plan_articulated("t", "a test articulated object")
    plan = Plan.model_validate(plan_dict)
    ordered = topo_sort(plan.materialised())

    # Required topo order: design → subgraph → build → render → judge
    positions = {t.id: i for i, t in enumerate(ordered)}
    assert positions["01_agent_design"] < positions["02_subgraph_parts"]
    assert positions["02_subgraph_parts"] < positions["03_agent_build"]
    assert positions["03_agent_build"] < positions["05_tool_render_multiview"]
    assert positions["05_tool_render_multiview"] < positions["08_tool_judge"]


def test_plan_generator_sets_expected_outputs_and_build_timeout():
    """The no-op guard relies on design/build/joints declaring expected_outputs,
    and build's soft timeout was bumped 300→600s (gemini builds idle-killed at
    the old deadline)."""
    from topos.orchestrator.plan_generator import generate_plan_articulated
    plan = generate_plan_articulated("t", "a test articulated object")
    by_id = {t["id"]: t for t in plan["tasks"]}
    assert by_id["01_agent_design"]["expected_outputs"] == ["src/design.json"]
    assert by_id["03_agent_build"]["expected_outputs"] == ["src/build.py"]
    assert by_id["04_agent_joints"]["expected_outputs"] == ["src/joints.yaml"]
    assert by_id["03_agent_build"]["timeout_s"] == 600


def test_plan_generator_uses_config_default_backend(monkeypatch):
    from topos.orchestrator.plan_generator import generate_plan_articulated

    monkeypatch.setattr(
        "topos.orchestrator.plan_generator.cfg.load_effective_config",
        lambda: {"backends": {"default": "gemini"}},
    )
    plan = generate_plan_articulated("t", "a test articulated object")
    by_id = {t["id"]: t for t in plan["tasks"]}
    assert by_id["01_agent_design"]["backend"] == "gemini"
    assert by_id["02_subgraph_parts"]["backend"] == "gemini"
    assert by_id["03_agent_build"]["backend"] == "gemini"
    assert by_id["04_agent_joints"]["backend"] == "gemini"
