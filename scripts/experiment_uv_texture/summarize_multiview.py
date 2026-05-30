"""Contact sheet for a multi-view fusion cell.

Layout: one column per view (cond + gen rows) + a final-renders strip.

Usage:
    python summarize_multiview.py <cell_dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


_THUMB = 256
_PAD = 8


def _load_font(size: int):
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _thumb(path: Path, size: int) -> Image.Image:
    if not path.is_file():
        img = Image.new("RGB", (size, size), "white")
        d = ImageDraw.Draw(img)
        for y in range(0, size, 16):
            d.line([(0, y), (size, y)], fill="red", width=1)
        d.text((4, size // 2 - 6), "MISSING", fill="red")
        return img
    img = Image.open(path).convert("RGB")
    img.thumbnail((size, size))
    if img.size != (size, size):
        bg = Image.new("RGB", (size, size), "white")
        bg.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
        img = bg
    return img


def build(cell_dir: Path) -> Path:
    manifest = json.loads((cell_dir / "manifest.json").read_text())
    views = manifest["views"]
    finals = ["final_front", "final_3q", "final_back"]

    label_col_w = 100
    col_w = _THUMB + _PAD
    top_label_h = 80
    sides = manifest.get("sides", ["outer"])
    n_block_rows = 1 + len(sides)   # cond + (outer [, inner])
    cond_gen_block_h = n_block_rows * (_THUMB + _PAD) + 30
    finals_block_h = (_THUMB + _PAD) + 30
    W = label_col_w + max(len(views), len(finals)) * col_w + _PAD
    H = top_label_h + cond_gen_block_h + finals_block_h + 40

    sheet = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(sheet)
    f_title = _load_font(18)
    f_label = _load_font(14)

    draw.text(
        (_PAD, _PAD),
        f"{manifest['slug']}/{manifest['part']} multi-view ({manifest['condition']})  "
        f"prompt={manifest['prompt'][:60]!r}  total=${manifest['total_cost_usd']:.4f}",
        fill="black", font=f_title,
    )

    # Per-view block: row 1 cond, row 2 gen (outer), row 3 gen-inner if present.
    sides = manifest.get("sides", ["outer"])
    has_inner = "inner" in sides
    rows = ["cond", "gen_outer"] + (["gen_inner"] if has_inner else [])
    n_block_rows = len(rows)

    y0 = top_label_h
    for r, label in enumerate(rows):
        draw.text((4, y0 + r * (_THUMB + _PAD) + _THUMB // 2 - 8),
                  label, fill="black", font=f_label)

    for ci, view in enumerate(views):
        x = label_col_w + ci * col_w
        draw.text((x + 4, y0 - 22), f"view={view}", fill="black", font=f_label)

        sheet.paste(_thumb(cell_dir / f"cond_{view}.png", _THUMB), (x, y0))

        # gen_outer — try v2 name first, fall back to v1.
        outer_p = (cell_dir / f"gen_outer_{view}.png")
        if not outer_p.is_file():
            outer_p = cell_dir / f"gen_{view}.png"
        sheet.paste(_thumb(outer_p, _THUMB),
                    (x, y0 + (_THUMB + _PAD)))

        if has_inner:
            sheet.paste(_thumb(cell_dir / f"gen_inner_{view}.png", _THUMB),
                        (x, y0 + 2 * (_THUMB + _PAD)))

    # Finals strip — placed below the (variable-height) cond/gen block.
    y1 = top_label_h + n_block_rows * (_THUMB + _PAD) + 20
    draw.text((4, y1 + _THUMB // 2 - 8), "final", fill="black", font=f_label)
    for fi, fname in enumerate(finals):
        x = label_col_w + fi * col_w
        draw.text((x + 4, y1 - 22), fname, fill="black", font=f_label)
        sheet.paste(_thumb(cell_dir / f"{fname}.png", _THUMB), (x, y1))

    out = cell_dir / "contact-sheet.png"
    sheet.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cell_dir")
    args = ap.parse_args()
    out = build(Path(args.cell_dir))
    print(f"[summarize-mv] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
