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
- **Image textures** (image-gen is the default; see `topos_texture_creator`): give each part a `texture: {prompt: "..."}` in design.json. The framework generates the PNG (Nano Banana 2) and UV-binds it at build time — geometry agents write no texture code, and you do NOT specify a path (it's derived). Suggested prompts:
  - Frame: `seamless tileable rough walnut wood plank, prominent grain, matte, 4k`
  - Drawer: `seamless tileable light oak wood, fine straight grain, satin, 4k`
  - Handle: `seamless tileable dark walnut wood, smooth, low sheen, 4k`

Coords meters, Z up, -Y is the cabinet's front.
