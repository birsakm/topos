"""Build a contact-sheet.png + summary.md from a matrix run directory.

Usage:
    python summarize.py <matrix_dir>

The contact sheet is one column per cell; each column shows the condition
image, the gemini-returned image, and the three final renders, with a
label row at top. Reads manifest.json for ordering + metadata.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


_THUMB = 256          # per-tile size on the contact sheet
_PAD = 8              # padding between tiles
_LABEL_H = 60         # height of the per-cell label band
_ROW_LABELS = ["cond", "gen", "final_front", "final_3q", "final_back"]


def _thumb(path: Path, size: int) -> Image.Image:
    if not path.is_file():
        # Placeholder: a striped error tile so missing files are obvious.
        img = Image.new("RGB", (size, size), "white")
        draw = ImageDraw.Draw(img)
        for y in range(0, size, 16):
            draw.line([(0, y), (size, y)], fill="red", width=1)
        draw.text((4, size // 2 - 6), "MISSING", fill="red")
        return img
    img = Image.open(path).convert("RGB")
    img.thumbnail((size, size))
    # Pad to square if not already.
    if img.size != (size, size):
        bg = Image.new("RGB", (size, size), "white")
        bg.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
        img = bg
    return img


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_contact_sheet(matrix_dir: Path) -> Path:
    manifest = json.loads((matrix_dir / "manifest.json").read_text())
    cells = manifest["cells"]

    n = len(cells)
    rows = len(_ROW_LABELS)
    col_w = _THUMB + _PAD
    row_h = _THUMB + _PAD
    label_col_w = 120
    cell_label_h = _LABEL_H
    top_label_h = _LABEL_H

    W = label_col_w + n * col_w + _PAD
    H = top_label_h + cell_label_h + rows * row_h + _PAD

    sheet = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(sheet)
    font_small = _load_font(11)
    font_label = _load_font(14)
    font_title = _load_font(18)

    draw.text((_PAD, _PAD),
              f"{manifest['slug']}/{manifest['part']}  view={manifest['view']}  "
              f"size={manifest['size']}  total=${manifest.get('total_cost_usd', 0):.4f}",
              fill="black", font=font_title)

    # Row labels (left gutter).
    for r, label in enumerate(_ROW_LABELS):
        y = top_label_h + cell_label_h + r * row_h + row_h // 2 - 8
        draw.text((4, y), label, fill="black", font=font_label)

    # Per-cell columns.
    for c, cell in enumerate(cells):
        x0 = label_col_w + c * col_w

        ok = cell.get("success", False)
        head = (f"cond={cell['condition']}\n"
                f"proj={cell['projection']}\n"
                f"prompt: {_truncate(cell['prompt'], 30)}\n"
                f"cost=${cell.get('cost_usd', 0):.4f}"
                + ("" if ok else "\n[FAILED]"))
        draw.text((x0 + 2, top_label_h),
                  head, fill="black" if ok else "red", font=font_small)

        cell_dir = matrix_dir / (
            f"{manifest['part']}__{manifest['view']}__"
            f"{cell['condition']}__{cell['projection']}__{cell['prompt_slug']}"
        )
        files = {
            "cond":        cell_dir / "cond.png",
            "gen":         cell_dir / "gen.png",
            "final_front": cell_dir / "final_front.png",
            "final_3q":    cell_dir / "final_3q.png",
            "final_back":  cell_dir / "final_back.png",
        }

        for r, label in enumerate(_ROW_LABELS):
            y0 = top_label_h + cell_label_h + r * row_h
            img = _thumb(files[label], _THUMB)
            sheet.paste(img, (x0, y0))

    out = matrix_dir / "contact-sheet.png"
    sheet.save(out)
    return out


def build_summary_md(matrix_dir: Path) -> Path:
    manifest = json.loads((matrix_dir / "manifest.json").read_text())
    lines = [
        f"# Matrix run: {manifest['slug']} / {manifest['part']}",
        "",
        f"- view: `{manifest['view']}`",
        f"- size: `{manifest['size']}`",
        f"- conditions: `{','.join(manifest['conditions'])}`",
        f"- projections: `{','.join(manifest['projections'])}`",
        f"- prompts ({len(manifest['prompts'])}):",
    ]
    for p in manifest["prompts"]:
        lines.append(f"  - `{p}`")
    lines += [
        "",
        f"**Total cost: ${manifest.get('total_cost_usd', 0):.4f}**",
        "",
        "## Cells",
        "",
        "| # | condition | projection | prompt | ok | cost | gemini_s | phase1_s | phase3_s | dir |",
        "| - | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, cell in enumerate(manifest["cells"], 1):
        ok = "✓" if cell.get("success") else "✗"
        lines.append(
            f"| {i} "
            f"| {cell['condition']} "
            f"| {cell['projection']} "
            f"| `{_truncate(cell['prompt'], 40)}` "
            f"| {ok} "
            f"| ${cell.get('cost_usd', 0):.4f} "
            f"| {cell.get('gemini_duration_s', 0):.1f} "
            f"| {cell.get('phase1_duration_s', 0):.1f} "
            f"| {cell.get('phase3_duration_s', 0):.1f} "
            f"| `{Path(cell.get('cell_dir', '')).name}` |"
        )
    out = matrix_dir / "summary.md"
    out.write_text("\n".join(lines))
    return out


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("matrix_dir")
    args = ap.parse_args()
    d = Path(args.matrix_dir)
    if not (d / "manifest.json").is_file():
        raise SystemExit(f"no manifest.json in {d!s}")
    sheet = build_contact_sheet(d)
    md = build_summary_md(d)
    print(f"[summarize] {sheet}")
    print(f"[summarize] {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
