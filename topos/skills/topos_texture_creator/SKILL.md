---
name: topos_texture_creator
description: How to add materials and textures to parts in a Topos build — procedural shader nodes for simple cases, generate_texture_image tool for image-based textures, plus UV unwrap and the per-part texture_<name>() pattern.
when_to_use: When a part's design calls for a non-trivial material (wood grain, fabric, brushed metal, painted surface) and you want richer than a flat Principled BSDF color.
---

# Texturing parts in Topos

Default render path already wires every mesh through Principled BSDF with the
`material` field from `design.json` (color + roughness + metallic). This skill
is for when you want **more than a flat color** — image textures or richer
procedural shaders.

## Decision: procedural vs image-based

**Procedural (shader nodes only — no external image)** is preferred when:
- The texture is mostly regular: brushed metal anisotropy, stripes, simple wood rings (`ShaderNodeTexWave`), noise paint
- You don't need photorealistic detail
- Cost: zero. Speed: instant. Determinism: perfect.

**Image-based (generate PNG via `generate_texture_image` tool, then map onto UVs)** is preferred when:
- You want a specific look like "rough walnut plank" or "dirty linen fabric"
- You want a sketch-conditioned variant (e.g. user provided a silhouette)
- You're willing to spend a Gemini image API call (~$0.001 each)

When in doubt: **start procedural, only escalate to image-based when it visibly matters**.

## The `texture_<part>(obj)` function pattern

If a part has a non-trivial material, the part's Python file defines an
**optional second function** alongside `build_<name>`:

```python
def build_drawer_front(parent=None):
    # ... mesh construction, returns the obj
    return obj

def texture_drawer_front(obj):
    """Attach material + (optionally) image texture to the drawer front mesh."""
    ...
```

The build orchestrator calls `texture_<name>(obj)` automatically after
`build_<name>()` if the function exists. Keeping the two split means:
- `build_*` is pure geometry — fast, deterministic, no API calls
- `texture_*` is the "make it look good" layer — can be skipped for fast iteration

## Pattern 1 — Procedural wood (no image)

```python
def texture_drawer_front(obj):
    import bpy
    mat = bpy.data.materials.new(name=f"{obj.name}_wood")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    tex = nodes.new("ShaderNodeTexWave")            # rings
    noise = nodes.new("ShaderNodeTexNoise")          # roughness variation
    colramp = nodes.new("ShaderNodeValToRGB")

    tex.inputs["Scale"].default_value = 12.0
    tex.inputs["Distortion"].default_value = 2.0
    noise.inputs["Scale"].default_value = 50.0
    colramp.color_ramp.elements[0].color = (0.18, 0.09, 0.04, 1)   # dark walnut
    colramp.color_ramp.elements[1].color = (0.48, 0.27, 0.13, 1)   # light walnut

    links.new(tex.outputs["Color"], colramp.inputs["Fac"])
    links.new(colramp.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(noise.outputs["Fac"], bsdf.inputs["Roughness"])
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
```

## Pattern 2 — Image texture via Gemini

**You do NOT call any CLI or tool yourself.** The framework dispatches a
dedicated `generate_texture_image` ToolTask per part — it reads
`design.json[parts.<your_part>.texture]`, calls Gemini Nano Banana 2, and
writes the PNG to `image_relpath` from the design contract. That ToolTask
runs BETWEEN your part task and the build task, so by the time
`texture_<part>(obj)` is invoked at build time the PNG is already on disk.

Your only job for image textures: write `texture_<part>(obj)` that
UV-unwraps and binds the PNG. Always include a **fallback path** in case
the texture ToolTask failed (image-gen API timeout / quota), so build
doesn't crash on a missing file.

```python
def texture_drawer_front(obj):
    import bpy, json
    from pathlib import Path

    # 1) Resolve the texture PNG path from design.json — single source of
    #    truth, same path the framework's texture ToolTask wrote to.
    src = Path(__file__).parent.parent           # src/
    design = json.loads((src / "design.json").read_text())
    part = next((p for p in design["parts"] if p["name"] == obj.name), None)
    tex_spec = (part or {}).get("texture") or {}
    image_relpath = tex_spec.get("image_relpath")
    tex_path = (src.parent / image_relpath) if image_relpath else None

    mat = bpy.data.materials.new(name=f"{obj.name}_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")

    if tex_path is None or not tex_path.is_file():
        # Fallback: texture ToolTask didn't produce a PNG (no image_relpath
        # in design.json, or image-gen failed). Use the material_hint color
        # if available, otherwise a neutral grey. Build continues either way.
        hint = tex_spec.get("material_hint", "")
        bsdf.inputs["Base Color"].default_value = (0.6, 0.45, 0.25, 1.0)  # warm wood-ish
        bsdf.inputs["Roughness"].default_value = 0.6
        print(f"[texture] {obj.name}: PNG missing ({tex_path}); falling back to flat color (hint={hint!r})")
    else:
        # 2) UV unwrap — required BEFORE binding image, or sampling is undefined.
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")

        # 3) Wire shader: ImageTexture → BSDF Base Color.
        img_tex = nodes.new("ShaderNodeTexImage")
        img_tex.image = bpy.data.images.load(str(tex_path))
        bsdf.inputs["Roughness"].default_value = 0.55
        links.new(img_tex.outputs["Color"], bsdf.inputs["Base Color"])

    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
```

Workflow seen from the part-task author's perspective:

```
1. Read design.json — see part has texture: {kind: "image", prompt: "...", image_relpath: "..."}
2. Write parts/<part>.py with build_<part>() AND texture_<part>(obj)
3. texture_<part>(obj) reads image_relpath from design.json, falls back if PNG missing
4. — done. The framework will:
   • dispatch ``generate_texture_image`` ToolTask after your part agent (Gemini Nano Banana 2)
   • call texture_<part>(obj) automatically after build_<part>() at build time
   • record the image-gen cost in the run_report's per-task and per-kind totals
```

For procedural textures (`texture.kind: "procedural"` or no `texture` field
in design.json), the framework's texture ToolTask is a free no-op — your
`texture_<part>(obj)` runs Pattern 1's shader-only code.

## Required API key (one-time setup)

The Gemini backend reads its key from `~/.config/topos/config.yaml`:

```bash
topos config set image_gen.gemini.api_key <your-key>     # writes user-global
# OR
export TOPOS__IMAGE_GEN__GEMINI__API_KEY=<your-key>      # one-shot env override
```

Get a key at https://aistudio.google.com/app/apikey. Without it, the tool
returns `success: false` with a clear error and `build.py` proceeds with the
flat material (you don't get a hard crash).

## Gotchas

- **UV unwrap before binding image**, or sampling is undefined and you'll see streaks.
- **Procedural textures don't need UVs** — they use generated/object coordinates by default.
- **One material per part is enough** for furniture-grade quality. Don't create three when one works.
- **Tile-able prompts** matter: ask Gemini for "seamless tileable" if the texture will repeat.
- **Don't bind an image that doesn't exist on disk** — `bpy.data.images.load` will fail. Either generate it before build runs, or have `texture_*` gracefully fall back to procedural when the file is missing.

## Procedural + GLB export — automatic bake

If you choose Pattern 1 (procedural shader nodes), the GLB exporter
**automatically bakes** your procedural Base Color chain to an embedded
image before writing the GLB. This means:

- Inside Blender render: you see your full procedural shader (Wave/Noise/etc).
- In the exported GLB: viewers (Three.js / Babylon / RViz / Webots) see a
  UV-unwrapped baked PNG that captures the same look. They cannot evaluate
  procedural nodes themselves, so the bake is the bridge.
- Bake takes ~1-2s per material at 1024px, runs only at GLB export time.
- Disabled with `bake_procedural: "off"` in the export_glb / export_urdf
  tool args (you almost never want to disable it).

So you don't have to choose procedural vs image-based based on
exportability — both work. Pick procedural for parametric / regular
patterns, pick image (Nano Banana 2) for photoreal detail.
