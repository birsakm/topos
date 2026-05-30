# optimus_prime_bay_v1 — showcase example

A 2.5 m humanoid Optimus Prime (Bay-era / Michael Bay live-action design) produced via `topos make`. 21 parts: pelvis, torso, head, face mask, spark window, paired shoulders / smokestacks / upper arms / forearms / hands / thighs / shins / feet. T-pose at rest.

Included in this directory:

| File | What it is |
|---|---|
| `object.glb` | Whole-scene GLB (9.8 MB) with 14 Nano Banana–generated PBR textures embedded. Open in any glTF viewer to see the result. |
| `spec.yaml` | The spec the framework derived from the NL prompt. |
| `prompts/intent.md` | The frozen intent the design / part / build agents read. |

This is a **showcase output**, not a replayable seed — the `src/` tree (21 part Python files + `build.py` + `joints.yaml`) and the per-part `parts_render/` renders are NOT included here, because they were heavily hand-refined post-`topos make` to demonstrate framework improvements (smokestack mount bracket, L/R mirror delegate pattern, joint-gap closures, shoulder back-pauldron extension, nano-banana textures). Those refinements drove the SKILL updates and the orchestrator commits landed alongside this example; see git log for the details.

To reproduce the *initial* (un-refined) version from scratch on a fresh workspace:

```bash
topos make "$(cat examples/optimus_prime_bay_v1/prompts/intent.md)" --slug my_optimus
```

Expect ~$3-6 in agent cost over 8-15 minutes of wall time for the first pass.
