"""Render polished transparent turntable GIFs of finished GLBs for the README.

Drives scripts/glb_turntable.py (Blender, Cycles, transparent film) on each
case's artifacts/object.glb, then assembles supersampled transparent GIFs
under docs/assets/.

Usage: python scripts/make_turntable_gifs.py [label ...]   (default: all)
"""
import glob
import subprocess
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, "/lab/yipeng/opentopos")
from topos.tools._blender_subprocess import resolve_blender_binary  # noqa: E402

REPO = Path("/lab/yipeng/opentopos")
TT = REPO / "scripts/glb_turntable.py"
OUT = REPO / "docs/assets"
BLENDER = resolve_blender_binary()

# label -> output slug whose artifacts/object.glb we render
CASES = {
    "ferris":  "ferris_wheel_v3",
    "optimus": "optimus_opus",
    "bike":    "bike_gemini4",
    "cabinet": "cab_a9_palace3",
}

N_FRAMES = 30
RENDER_RES = 960   # render high, downscale to GIF_SIZE → supersampled / crisp
GIF_SIZE = 460
FRAME_MS = 80


def _frame_to_p(path: str):
    im = Image.open(path).convert("RGBA").resize((GIF_SIZE, GIF_SIZE), Image.LANCZOS)
    alpha = im.split()[3]
    p = im.convert("RGB").quantize(colors=255, method=Image.MEDIANCUT)
    mask = alpha.point(lambda a: 255 if a < 128 else 0)
    p.paste(255, mask)
    p.info["transparency"] = 255
    return p


def render(label: str, slug: str) -> None:
    glb = REPO / "outputs" / slug / "artifacts" / "object.glb"
    if not glb.is_file():
        print(f"[{label}] no GLB at {glb}", flush=True); return
    frames_dir = REPO / "outputs" / slug / "artifacts" / "turntable"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("frame_*.png"):
        old.unlink()
    cmd = [
        BLENDER, "--background", "--python", str(TT), "--",
        "--glb", str(glb), "--out", str(frames_dir),
        "--frames", str(N_FRAMES), "--res", str(RENDER_RES), "--engine", "cycles",
    ]
    print(f"[{label}] rendering {slug} (cycles, {RENDER_RES}px x{N_FRAMES}) ...", flush=True)
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=1800)
    frames = sorted(glob.glob(str(frames_dir / "frame_*.png")))
    if not frames:
        print(f"[{label}] NO FRAMES. stderr tail:\n{r.stderr[-800:]}", flush=True); return
    imgs = [_frame_to_p(f) for f in frames]
    gif = OUT / f"demo_{label}.gif"
    imgs[0].save(gif, save_all=True, append_images=imgs[1:], duration=FRAME_MS,
                 loop=0, optimize=True, disposal=2, transparency=255)
    print(f"[{label}] -> {gif.name}  {len(frames)} frames  {gif.stat().st_size/1e6:.1f}MB", flush=True)


def main():
    for label in (sys.argv[1:] or list(CASES)):
        if label in CASES:
            render(label, CASES[label])


if __name__ == "__main__":
    main()
