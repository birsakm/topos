# Currently pinned Blender version

**Pinned binary:** `~/bin/blender` (Blender 5.0.1, build date 2025-12-16).
**Pinned index:** `~/.config/topos/bpy_docs.json` (2,634 symbols).

## Upstream state as of 2026-05-11

| Channel | Version |
|---|---|
| Latest stable | **5.1.1** (also 5.1.0) |
| Current docs (`docs.blender.org/api/current/`) | 5.1 |
| In-development | 5.2 (docs published, no release tarball yet) |
| Previous LTS | 4.5 LTS |

## Should we upgrade?

Topos is **one minor version behind** stable. The Python API surface is
mostly stable across 5.0 → 5.1 — most procedural mesh / material / URDF
code keeps working. The one big area of churn is the **compositor**
(massive node-class removals), which Topos does not use.

See `5_0_to_5_1.md` for what changes if we upgrade. **Bottom line:**
upgrading is low-risk for the current cabinet pipeline; do it when
convenient (and rerun `topos bpy-docs index` afterwards). Not blocking.

## How to upgrade

```bash
# 1. Download blender-5.1.1-linux-x64.tar.xz from download.blender.org
# 2. Extract somewhere, point Topos at it:
topos config set blender.binary /path/to/blender-5.1.1/blender
# 3. Rebuild the API index against the new binary
topos bpy-docs index
# 4. (optional) Smoke test the cabinet:
topos run cab_p123_verify --base outputs
```

After upgrading, write the next file: `5_1_to_5_X.md` with any deltas you
notice between the old and new indexes.
