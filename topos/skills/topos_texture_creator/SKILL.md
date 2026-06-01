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
  "prompt": "seamless tileable brushed aluminium, fine horizontal grain, matte, 4k",
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

Nano Banana renders a **flat, tiling material swatch** that gets wrapped onto
the part. So describe a *surface*, seamlessly, with material vocabulary:

- **Lead with "seamless tileable"** — the PNG repeats across UVs; without this
  you get visible seams.
- **Name the material + finish + fine structure**: `"seamless tileable
  walnut wood plank, straight grain, satin finish, 4k"`, `"seamless tileable
  knurled steel, fine diamond pattern, brushed, matte"`, `"seamless tileable
  black vulcanized rubber tyre tread, fine sipes"`.
- **State the surface qualities** you want the shader to read: matte / satin /
  glossy, rough / smooth, brushed / polished / cast.
- **End with `4k`** (or `high detail`) for crisp grain.
- **Do NOT** put scene words in the prompt: no lighting, no camera, no
  background, no "a photo of a …", no whole-object words. You want the
  *material*, flat and repeatable — not a product shot.

### Good vs bad

| Part | ✅ good prompt | ❌ bad prompt |
|---|---|---|
| Bike frame | `seamless tileable glossy teal powder-coated metal, smooth, subtle clearcoat, 4k` | `a teal bicycle frame` |
| Tyre | `seamless tileable black rubber tyre tread, fine sipes, matte, 4k` | `a round black wheel on a road` |
| Saddle | `seamless tileable black leather, fine pebbled grain, low sheen, 4k` | `bicycle seat, studio lighting` |
| Wood drawer | `seamless tileable walnut veneer, straight grain, satin, 4k` | `brown wood` |

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
