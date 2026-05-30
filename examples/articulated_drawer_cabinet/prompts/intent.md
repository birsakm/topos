A small wooden single-drawer cabinet.

- 3 parts:
  - **Frame** — hollow cabinet body, opens on -Y (front)
  - **Drawer** — open-top 5-panel box that slides into Frame's cavity
  - **Handle** — recognizable handle on the drawer's front face (NOT a plain cube)
- 2 joints:
  - **drawer_slide** — prismatic joint connecting Drawer to Frame, axis [0, -1, 0]
  - **handle_to_drawer** — fixed joint connecting Handle to Drawer
- **Rest pose**: drawer half-out the front so all three parts are clearly visible from any viewpoint
- Cabinet outer ~30 × 30 × 30 cm
- Drawer must fit Frame's cavity with **2-5mm clearance per side** (real-furniture precision — NOT 10mm)
- Add `outer_bevel_radius` ~3-5mm to Frame, `front_inset_depth` ~3-5mm and `front_inset_margin` ~10-15mm to Drawer
- Color hints: Frame [0.45, 0.27, 0.15, 1.0] (wood brown), Drawer [0.60, 0.42, 0.25, 1.0] (lighter wood), Handle [0.30, 0.20, 0.10, 1.0] (dark wood)
- Joint limits: drawer_slide can slide -0.10 to +0.15 m from rest
- **Realistic image-based textures** (Pattern 2 in `topos_texture_creator`): emit `texture: {kind: "image", prompt: "...", image_relpath: "src/textures/<part>.png"}` for **Frame and Drawer** in design.json. The framework's per-part `generate_texture_image` ToolTask will materialize the real photo-grade PNG via Nano Banana 2; the part agent's `texture_<name>(obj)` just binds the PNG via `ShaderNodeTexImage`. Suggested prompts:
  - Frame: `seamless tileable photorealistic rough walnut wood plank, 4k, top-down`
  - Drawer: `seamless tileable photorealistic light oak wood, fine grain, 4k, top-down`
- **Handle texture**: a simple procedural shader is enough — the handle's surface is small and the image-gen budget is better spent on Frame/Drawer. **You must still write `texture_handle(obj)`** following Pattern 1 in `topos_texture_creator`: a Principled BSDF with a wood-tint Base Color and modest roughness. A flat baseColorFactor (no Wave/Noise needed) is fine; the point is that every part gets a material so the exported GLB has materials for all three parts, not just Frame and Drawer.

Coords meters, Z up, -Y is the cabinet's front.
