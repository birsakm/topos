"""Inside-Blender export wrapper.

Runs the agent's geometry script via runpy, strips any cameras/lights, then
exports in one of two modes:

- ``glb``   — whole scene → single ``output.glb``. Each object's world
              transform (location, rotation, scale) is baked into its mesh
              data before export so the GLB looks identical in any viewer
              regardless of how it composes the GLTF node hierarchy. Node
              transforms in the output GLB are therefore identity.

- ``parts`` — every MESH object → ``<output_dir>/<obj_name>.glb`` in
              object-local coordinates (origin-centred, rotation/scale baked
              into mesh data). Plus a ``manifest.json`` recording each
              object's *original* world transform so the URDF writer can
              compose link/joint frames correctly. URDF mesh references will
              point at these per-part GLB files.

Why bake transforms?
  Blender objects can carry a non-trivial transform (especially scale) on
  top of their mesh data. Naïve "selected object" export hands the GLTF/OBJ
  exporter just the mesh data and a node transform; viewers that don't
  honor node hierarchy then render at the wrong size. Worse, after a
  ``bpy.ops.object.join()`` with a tiny-scaled active object the local
  mesh data can have wildly stretched vertices that are only correct after
  the node scale is applied. Baking sidesteps all of that.

No ``topos`` imports here — this runs in Blender's bundled Python.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from pathlib import Path

import bpy


def _parse_args() -> argparse.Namespace:
    if "--" not in sys.argv:
        raise SystemExit("export_wrapper: no '--' separator in argv")
    raw = sys.argv[sys.argv.index("--") + 1:]
    p = argparse.ArgumentParser(prog="export_wrapper")
    p.add_argument("--mode", required=True, choices=["glb", "parts"])
    p.add_argument("--script", required=True)
    p.add_argument("--output")             # file (glb mode)
    p.add_argument("--output-dir")         # directory (parts mode)
    p.add_argument("--y-up", action="store_true",
                   help="ignored at v2; GLB axis convention handled per-mode")
    p.add_argument(
        "--bake-procedural", choices=["on", "off"], default="on",
        help=(
            "Bake procedural shader Base Color (TexWave/TexNoise/etc) to "
            "an embedded image before GLB export, so the GLB carries real "
            "textures viewers can render. 'off' skips bake (materials become "
            "empty PBR in GLB). Default 'on'."
        ),
    )
    p.add_argument(
        "--bake-resolution", type=int, default=1024,
        help="Pixels per side for baked textures. Default 1024.",
    )
    p.add_argument(
        "--texture-save-dir", default=None,
        help=(
            "If set, also save each baked texture PNG to this directory "
            "(under the workspace) for inspection. Textures are always packed "
            "into the GLB regardless; this is opt-in for visibility."
        ),
    )
    return p.parse_args(raw)


def _clean_factory() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _run_agent_script(path: str) -> None:
    """Execute the agent's geometry script. Inserts the script's parent dir
    at the front of ``sys.path`` so multi-file projects (e.g. with
    ``parts/<name>.py`` siblings of ``build.py``) can import internally."""
    script_path = Path(path)
    if not script_path.is_file():
        raise SystemExit(f"export_wrapper: agent script not found: {path}")
    script_dir = str(script_path.parent.resolve())
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    saved_argv = sys.argv[:]
    sys.argv = [path]
    try:
        runpy.run_path(path, run_name="__main__")
    finally:
        sys.argv = saved_argv


def _strip_non_geometry() -> list:
    for obj in list(bpy.context.scene.objects):
        if obj.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.context.view_layer.update()
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def _select_only(obj) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _bake_world_transform_into_mesh(obj) -> None:
    """Apply location + rotation + scale into mesh data. Object transform
    becomes identity. Destructive (mutates the object's mesh data)."""
    _select_only(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def _bake_rotation_scale_into_mesh(obj) -> None:
    """Apply rotation + scale only; location stays on the object transform.
    Used for per-part export so the mesh data is in object-local but
    correctly-sized coordinates."""
    _select_only(obj)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


# ---------- procedural-shader → baked-image conversion ----------
#
# Why: GLTF/GLB only carries images, never procedural shader graphs. If the
# agent's material has TexWave/TexNoise/etc feeding Principled BSDF Base Color,
# Blender's GLTF exporter silently drops them — GLB ships with empty PBR
# materials, viewers render gray meshes. We bake to an image and rewire so the
# BSDF Base Color is an ImageTexture, which GLTF embeds verbatim.

def _looks_like_procedural_material(mat) -> bool:
    """Return True if the material's BSDF Base Color is fed by a procedural
    chain (Wave/Noise/ColorRamp/etc) and NOT directly/transitively by an
    ImageTexture. We detect by walking the link graph back from Base Color
    and flagging an image-based chain only if some upstream node is
    ShaderNodeTexImage."""
    if not mat.use_nodes or not mat.node_tree:
        return False
    nodes = mat.node_tree.nodes
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if not bsdf:
        return False
    bc_input = bsdf.inputs.get("Base Color")
    if not bc_input or not bc_input.is_linked:
        # baseColorFactor-only: nothing procedural; nothing to bake
        return False
    # Walk back the chain; if any node is TEX_IMAGE we treat as image-based
    visited: set = set()
    stack = [bc_input.links[0].from_node]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        if node.type == "TEX_IMAGE":
            return False
        for inp in node.inputs:
            for link in inp.links:
                stack.append(link.from_node)
    return True


def _ensure_uv_unwrap(obj) -> None:
    """Ensure obj has a usable UV layer. If missing or degenerate, run a
    smart_project. Cycles bake requires non-overlapping UVs covering [0,1]²."""
    needs_unwrap = True
    if obj.data.uv_layers:
        uv_layer = obj.data.uv_layers.active
        if uv_layer and len(uv_layer.data) > 0:
            xs = [uv.uv.x for uv in uv_layer.data]
            ys = [uv.uv.y for uv in uv_layer.data]
            if (max(xs) - min(xs)) > 0.01 and (max(ys) - min(ys)) > 0.01:
                needs_unwrap = False
    if not needs_unwrap:
        return
    import math
    _select_only(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


def _bake_material_base_color_to_image(obj, mat, image_size: int):
    """Bake the material's current Base Color shader chain into a new image,
    then rewire the BSDF to consume that image. Destructive: removes the old
    procedural links. Returns the bpy.types.Image we just baked."""
    scn = bpy.context.scene
    scn.render.engine = "CYCLES"
    # Low sample count is fine for diffuse procedural bake — the shader is
    # deterministic, not noisy lighting.
    scn.cycles.samples = 16
    scn.render.bake.use_pass_direct = False
    scn.render.bake.use_pass_indirect = False
    scn.render.bake.use_pass_color = True
    scn.render.bake.margin = 8  # pixels of bleed at UV island borders

    # Image name doubles as the GLTF image label + the saved PNG filename.
    # If the material's name already begins with the object's name (agent
    # convention like "Frame_walnut"), don't repeat the prefix — keep names
    # clean for the user browsing artifacts/textures/.
    if mat.name.lower().startswith(obj.name.lower()):
        img_name = mat.name
    else:
        img_name = f"{obj.name}_{mat.name}"
    # If a previous bake left a stale image with the same name, reuse it
    bake_img = bpy.data.images.get(img_name)
    if bake_img is None:
        bake_img = bpy.data.images.new(
            name=img_name, width=image_size, height=image_size, alpha=False,
        )

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Add ImageTexture node bound to bake_img; set it as the bake target
    img_node = nodes.new("ShaderNodeTexImage")
    img_node.image = bake_img
    img_node.location = (-600, 400)
    for n in nodes:
        n.select = False
    img_node.select = True
    nodes.active = img_node

    # Bake — Cycles evaluates the existing shader at every UV pixel
    _select_only(obj)
    bpy.ops.object.bake(type="DIFFUSE")

    # Pack the image data into the .blend so the GLTF exporter can embed it.
    # (Without pack, GLTF will try to reference a filesystem path that doesn't
    # exist after the wrapper exits.)
    bake_img.pack()

    # Rewire: drop any link feeding BSDF.Base_Color, connect baked image instead
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    for link in list(links):
        if link.to_node == bsdf and link.to_socket.name == "Base Color":
            links.remove(link)
    links.new(img_node.outputs["Color"], bsdf.inputs["Base Color"])

    return bake_img


def _mirror_image_based_textures(texture_save_dir: Path) -> int:
    """Copy any ImageTexture node's source file (i.e. image-kind textures,
    e.g. produced by the framework's ``generate_texture_image`` ToolTask
    and saved under ``src/textures/``) into ``texture_save_dir`` so
    artifacts/textures/ contains every image that
    ends up in the GLB regardless of whether it came from a bake or from a
    user-authored PNG. Returns the count of mirrored files. Packed-only
    images (no on-disk source) are skipped — they're already in the GLB,
    and our bake step handles saving them separately if requested."""
    import shutil
    texture_save_dir.mkdir(parents=True, exist_ok=True)
    mirrored = 0
    seen: set = set()
    for mat in bpy.data.materials:
        if not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type != "TEX_IMAGE" or node.image is None:
                continue
            img = node.image
            raw = img.filepath_from_user() if img.filepath_from_user else img.filepath
            if not raw:
                continue
            src_path = Path(bpy.path.abspath(raw)).resolve()
            if not src_path.is_file():
                continue
            if src_path in seen:
                continue
            seen.add(src_path)
            dest = (texture_save_dir / src_path.name).resolve()
            if dest == src_path:
                continue
            try:
                shutil.copy2(src_path, dest)
                mirrored += 1
                print(f"[export_wrapper] texture: mirrored {src_path.name} → {dest.relative_to(texture_save_dir.parent)}")
            except OSError as e:
                print(f"[export_wrapper] texture: mirror failed for {src_path.name}: {e}")
    return mirrored


def _bake_procedural_materials(image_size: int, texture_save_dir: Path | None) -> dict:
    """For every mesh+material in the scene with a procedural Base Color chain,
    UV-unwrap (if needed) and bake to an embedded image. Idempotent for
    materials already using ImageTexture (they're skipped). Returns a manifest
    of {obj_name: [baked_image_paths_or_packed_marker]}."""
    manifest: dict[str, list[str]] = {}
    if texture_save_dir is not None:
        texture_save_dir.mkdir(parents=True, exist_ok=True)
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for obj in mesh_objs:
        proc_mats = [
            m for m in obj.data.materials
            if m is not None and _looks_like_procedural_material(m)
        ]
        if not proc_mats:
            continue
        # UV unwrap once per object (covers all its materials)
        try:
            _ensure_uv_unwrap(obj)
        except RuntimeError as e:
            print(f"[export_wrapper] texture: UV unwrap failed for {obj.name}: {e}; skipping")
            continue
        texture_paths: list[str] = []
        for mat in proc_mats:
            try:
                bake_img = _bake_material_base_color_to_image(obj, mat, image_size)
            except RuntimeError as e:
                print(f"[export_wrapper] texture: bake failed {obj.name}/{mat.name}: {e}")
                continue
            if texture_save_dir is not None:
                save_path = texture_save_dir / f"{bake_img.name}.png"
                bake_img.filepath_raw = str(save_path)
                bake_img.file_format = "PNG"
                bake_img.save()
                texture_paths.append(str(save_path))
                print(f"[export_wrapper] texture: {obj.name}/{mat.name} → {save_path.relative_to(texture_save_dir.parent)} ({image_size}px)")
            else:
                texture_paths.append("<packed>")
                print(f"[export_wrapper] texture: {obj.name}/{mat.name} → packed image ({image_size}px)")
        if texture_paths:
            manifest[obj.name] = texture_paths
    return manifest


# ---------- whole-scene GLB ----------

def _export_glb(output_path: str) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Bake every mesh's full world transform into its mesh data so the GLB
    # is rendered identically by any viewer (and matches the per-part files).
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for obj in mesh_objs:
        _bake_world_transform_into_mesh(obj)

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(out),
        export_format="GLB",
        use_selection=False,
        export_apply=True,       # apply modifiers (transforms already baked above)
        # Whole-scene GLB is meant for visual inspection in glTF viewers, which
        # universally default to Y-up. Topos authors meshes in Z-up (Topos /
        # robotics convention), so we ask the glTF exporter to do the standard
        # Z-up → Y-up axis conversion on the way out — otherwise the cabinet
        # appears tipped on its back (cavity opening pointing at the floor) in
        # any default viewer. Per-part GLBs (below) stay Z-up because they are
        # consumed by URDF / ROS / RViz / Webots which expect Z-up.
        export_yup=True,
    )
    print(f"[export_wrapper] glb: wrote {out} ({len(mesh_objs)} mesh objects baked + exported)")


# ---------- per-part GLB ----------

def _export_one_glb_local(obj, out_path: Path) -> None:
    """Duplicate ``obj``, bake rotation+scale into the duplicate's mesh,
    move duplicate to origin, export it alone as GLB, then delete the
    duplicate so the original is untouched."""
    _select_only(obj)
    bpy.ops.object.duplicate()
    dup = bpy.context.active_object
    try:
        _bake_rotation_scale_into_mesh(dup)
        dup.location = (0.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        _select_only(dup)
        bpy.ops.export_scene.gltf(
            filepath=str(out_path),
            export_format="GLB",
            use_selection=True,
            export_apply=True,
            export_yup=False,    # URDF / ROS use Z-up; keep mesh in Z-up
        )
    finally:
        bpy.data.objects.remove(dup, do_unlink=True)


def _export_parts(output_dir: str, *, y_up: bool) -> None:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"objects": []}
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for obj in mesh_objs:
        # ``_export_one_glb_local`` bakes rotation + scale into the duplicate's
        # mesh before writing the per-part GLB. To keep the manifest consistent
        # with what's actually in the GLB, bake the SAME way on the original
        # first — then matrix_world is guaranteed to have rotation = identity
        # and scale = (1, 1, 1), and the manifest records only the residual
        # translation (the part's pivot in world space).
        #
        # Why this matters: agents differ in how much of the world transform
        # they bake during ``build_<part>()`` (some clear rotation_euler via
        # transform_apply, some leave it on the object). If we captured
        # matrix_world BEFORE this bake and recorded a non-zero world_rpy,
        # the URDF writer would apply that rotation AGAIN as visual_rpy on
        # top of the already-baked mesh — producing a doubly-rotated link
        # (e.g. a handle that lies flat instead of standing). Baking up front
        # erases that ambiguity.
        _bake_rotation_scale_into_mesh(obj)
        loc = obj.matrix_world.to_translation()
        vert_count = len(obj.data.vertices)

        out_path = out_dir / f"{obj.name}.glb"
        _export_one_glb_local(obj, out_path)
        manifest["objects"].append({
            "name": obj.name,
            "mesh_path": out_path.name,
            "world_xyz": [loc.x, loc.y, loc.z],
            "world_rpy": [0.0, 0.0, 0.0],        # rotation baked into mesh
            "world_scale": [1.0, 1.0, 1.0],      # scale baked into mesh
            "vertex_count": vert_count,
        })
        print(
            f"[export_wrapper] parts: wrote {out_path} "
            f"({vert_count} verts, world={list(loc)})"
        )

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[export_wrapper] parts: manifest at {manifest_path} ({len(manifest['objects'])} objects)")


def main() -> int:
    args = _parse_args()

    _clean_factory()
    _run_agent_script(args.script)
    _strip_non_geometry()

    texture_save_dir = Path(args.texture_save_dir) if args.texture_save_dir else None

    if args.bake_procedural == "on":
        bake_manifest = _bake_procedural_materials(
            image_size=args.bake_resolution, texture_save_dir=texture_save_dir,
        )
        if bake_manifest:
            n_imgs = sum(len(v) for v in bake_manifest.values())
            print(f"[export_wrapper] texture: baked {n_imgs} procedural image(s) across {len(bake_manifest)} object(s)")
        else:
            print("[export_wrapper] texture: no procedural materials found")

    # Mirror image-kind textures (image-based PNGs loaded into ShaderNodeTexImage
    # from src/textures/ etc.) into the same inspection dir, so artifacts/textures/
    # gives a uniform view of every texture that ends up in the GLB.
    if texture_save_dir is not None:
        n_mirrored = _mirror_image_based_textures(texture_save_dir)
        if n_mirrored:
            print(f"[export_wrapper] texture: mirrored {n_mirrored} image-based texture(s) into {texture_save_dir.name}/")

    if args.mode == "glb":
        if not args.output:
            raise SystemExit("export_wrapper: --output is required for mode=glb")
        _export_glb(args.output)
    elif args.mode == "parts":
        if not args.output_dir:
            raise SystemExit("export_wrapper: --output-dir is required for mode=parts")
        _export_parts(args.output_dir, y_up=args.y_up)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
