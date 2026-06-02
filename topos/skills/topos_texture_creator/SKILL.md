---
name: topos_texture_creator
description: How to author per-part `texture.prompt` strings in design.json so the framework generates good material images (Gemini Nano Banana). For the DESIGN agent — geometry agents never touch texture.
when_to_use: When writing design.json for an articulated/static object and deciding each part's material look. You set `texture.prompt` per part; the framework generates the PNG and UV-binds it automatically at build time.
related_skills:
  - topos_design_articulated
---

# Authoring textures in design.json (design agent)

Texture is **fully decoupled** from geometry in Topos. You — the design agent —
own the entire look, expressed as a short `texture.prompt` per part in
`design.json`. The part-geometry agents write pure geometry and never see
texture. At assembly time the framework:

1. runs `generate_texture_image` per part → a tileable PNG from your `prompt`
   (Gemini Nano Banana 2), written to `src/textures/<part_name>.png` (derived;
   you do **not** specify a path), and
2. UV-unwraps each part (Smart UV Project) and binds its PNG as the material.

Image generation is the **default**: give **every** part a `texture.prompt`
unless you deliberately want it flat.

## The spec (per part)

```json
"texture": {
  "prompt": "brushed aluminium surface, fine horizontal grain, filling the whole image edge to edge, top-down, soft even light, matte",
  "material_hint": "matte aluminium"            // optional, one line
}
```

- **`prompt`** — the only thing that matters. A text-to-image prompt for the
  *material surface*, not the object. (set the wheel's rubber, not "a wheel".)
- **`material_hint`** — optional short label, used as the flat-color fallback
  cue if image-gen is unavailable. Keep it to a couple of words.
- Omit the whole `texture` block for a part you want rendered flat in its
  `color_rgba` (e.g. a tiny internal bracket not worth an image call).

## Writing a good `prompt` — the rules that matter

Nano Banana renders a **flat material surface** that gets wrapped onto the part.
Describe a *surface*, full-frame, with material vocabulary — but phrase it as a
real surface filling the frame, **not** as a stock-texture catalog entry (see
the recitation warning below).

- **End the prompt with the framing clause `surface, filling the whole image
  edge to edge, top-down, soft even light`.** "Filling the whole image edge to
  edge" gives a full-bleed PNG (no seams, no swatch-on-a-background); "top-down,
  soft even light" keeps lighting flat so no highlights/reflections bake into the
  texture.
- **Name the material + finish + fine structure** up front: `"walnut wood plank,
  straight grain, satin finish"`, `"knurled steel, fine diamond pattern,
  brushed"`, `"black vulcanized rubber tyre tread, fine sipes"`.
- **State the surface qualities** you want the shader to read: matte / satin /
  glossy, rough / smooth, brushed / polished / cast.
- **Do NOT** name the object or scene: set the wheel's *rubber*, not "a wheel";
  no background, no whole-object words.

### ⚠️ Avoid IMAGE_RECITATION (empty image, deterministic)

Nano Banana's copyright/recitation guard returns an **empty image** (HTTP 200,
`finishReason: IMAGE_RECITATION`) when a prompt reads like the caption of a
scraped stock-texture image — and it's **deterministic**, so the framework's
retries can't save it; the part falls back to flat. The trigger is the
*phrasing*, not the material. Verified offenders — **never use these tokens**:

- `seamless tileable` and `4k` / `8k` / `high detail` (classic stock-site caption) —
  this combo alone blocks innocuous materials like leather and anodized aluminium.
- `extreme macro close-up`, `swatch` (also gives a swatch-on-background, not full-bleed).
- `sandblasted` (an independent trigger even when photo-framed — say `fine
  even matte speckle` instead).

The framing clause above (`… surface, filling the whole image edge to edge,
top-down, soft even light`) is the tested phrasing that passes — it reads as a
photo of a real surface, not a catalog tile. Verified 2026-06-01.

### Good vs bad

| Part | ✅ good prompt | ❌ bad prompt (recites or off-target) |
|---|---|---|
| Bike frame | `glossy teal powder-coated metal, smooth, subtle clearcoat, surface filling the whole image edge to edge, top-down, soft even light` | `seamless tileable glossy teal powder-coated metal, 4k` |
| Tyre | `black rubber tyre tread, fine sipes, matte, surface filling the whole image edge to edge, top-down, soft even light` | `a round black wheel on a road` |
| Saddle | `black leather, subtle pebbled grain, low sheen, surface filling the whole image edge to edge, top-down, soft even light` | `seamless tileable black leather, fine pebbled grain, 4k` |
| Wood drawer | `walnut veneer, straight grain, satin, surface filling the whole image edge to edge, top-down, soft even light` | `seamless tileable walnut veneer, 4k` |

## Practical tips

- **One prompt per visible material** — parts that share a look can share the
  same prompt text (each still gets its own PNG; that's fine).
- **Match `material_hint` to `color_rgba`** so the flat fallback (if image-gen
  is down) still reads correctly — e.g. a chrome part: `color_rgba` light grey,
  `material_hint` "polished chrome".
- **Cost/time**: each prompt is one Nano Banana call (~60–120s, ~$0.001).
  Every part with a prompt fires one. For a 20-part object that's the bulk of
  the texture phase — worth it for furniture-grade looks, but omit `texture`
  on parts where flat color is genuinely fine.
- The exported GLB embeds these image textures natively (no procedural bake
  needed), so Three.js / RViz / Webots all see the same material.

## Required API key (one-time)

```bash
topos config set image_gen.gemini.api_key <your-key>     # user-global
# or:  export TOPOS__IMAGE_GEN__GEMINI__API_KEY=<your-key>
```

Get a key at https://aistudio.google.com/app/apikey. Without it,
`generate_texture_image` returns a clear error and the build falls back to
flat `color_rgba` — no hard crash.
