"""
Generate eSeva Center app icons for all platforms.

Outputs:
  assets/icon.png   — 1024x1024 master PNG  (Linux AppImage + base)
  assets/icon.ico   — multi-size ICO        (Windows)
  assets/icon.icns  — ICNS                  (macOS)

Run from the repo root:
  python scripts/make_icons.py
"""
from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ASSETS.mkdir(exist_ok=True)

# ── Palette ──────────────────────────────────────────────────────────────────
BG_TOP    = (31,  55, 120)   # deep indigo
BG_BOT    = (79,  70, 229)   # vibrant indigo
ACCENT    = (249, 115, 22)   # orange
WHITE     = (255, 255, 255)


def make_base(size: int) -> Image.Image:
    """Draw the icon at `size x size` pixels."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    r = int(size * 0.18)   # corner radius

    # ── Rounded-rect background (gradient approximated with two rects + blend)
    bg_top = Image.new("RGBA", (size, size), BG_TOP + (255,))
    bg_bot = Image.new("RGBA", (size, size), BG_BOT + (255,))
    mask_grad = Image.new("L", (size, size))
    for y in range(size):
        mask_grad.putpixel((0, y), int(255 * y / size))
    # Horizontal strip trick — paste column by column is slow; use a simple linear blend
    bg = Image.blend(bg_top, bg_bot, 0.55)

    # Rounded-rect mask
    rr_mask = Image.new("L", (size, size), 0)
    rr_draw = ImageDraw.Draw(rr_mask)
    rr_draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)

    img.paste(bg, mask=rr_mask)

    draw = ImageDraw.Draw(img)

    # ── Pillar / building silhouette (simplified) ─────────────────────────────
    s = size
    cx = s // 2

    # Base platform
    bh = int(s * 0.10)
    by = int(s * 0.78)
    draw.rectangle([int(s * 0.12), by, int(s * 0.88), by + bh], fill=ACCENT)

    # Three columns
    col_w = int(s * 0.085)
    col_h = int(s * 0.38)
    col_y = by - col_h
    cols_x = [int(s * 0.20), cx - col_w // 2, int(s * 0.715 - col_w // 2)]
    for cx_ in cols_x:
        draw.rectangle([cx_, col_y, cx_ + col_w, by], fill=WHITE)

    # Triangular pediment (roof)
    roof_pts = [
        (int(s * 0.10), col_y),
        (int(s * 0.90), col_y),
        (cx, int(s * 0.20)),
    ]
    draw.polygon(roof_pts, fill=WHITE)

    # Orange top accent line
    draw.rectangle([int(s * 0.10), col_y - int(s * 0.025),
                    int(s * 0.90), col_y], fill=ACCENT)

    # ── "eSeva" text label ────────────────────────────────────────────────────
    font_size = max(int(s * 0.13), 8)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    label = "DocuProcess"
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    tx = (s - tw) // 2
    ty = by + bh + int(s * 0.02)
    draw.text((tx, ty), label, fill=WHITE, font=font)

    return img


# ── PNG 1024x1024 ─────────────────────────────────────────────────────────────
master = make_base(1024)
master.save(ASSETS / "icon.png", format="PNG")
print(f"✓  assets/icon.png  ({(ASSETS/'icon.png').stat().st_size//1024} KB)")


# ── ICO (Windows) — multiple sizes embedded ───────────────────────────────────
ico_sizes = [16, 24, 32, 48, 64, 128, 256]
ico_images = [make_base(s).convert("RGBA") for s in ico_sizes]
master.save(
    ASSETS / "icon.ico",
    format="ICO",
    sizes=[(s, s) for s in ico_sizes],
)
print(f"✓  assets/icon.ico  ({(ASSETS/'icon.ico').stat().st_size//1024} KB)")


# ── ICNS (macOS) — hand-built since Pillow doesn't write ICNS ─────────────────
# ICNS format: 4-byte magic + chunks. Each chunk: OSType (4 bytes) + length (4 bytes) + PNG data
ICNS_MAGIC = b"icns"

ICNS_TYPES: list[tuple[int, bytes]] = [
    (16,   b"icp4"),
    (32,   b"icp5"),
    (64,   b"icp6"),
    (128,  b"ic07"),
    (256,  b"ic08"),
    (512,  b"ic09"),
    (1024, b"ic10"),
]

chunks: list[bytes] = []
for px, ostype in ICNS_TYPES:
    im = make_base(px).convert("RGBA")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    png_data = buf.getvalue()
    chunk_len = 8 + len(png_data)
    chunks.append(ostype + struct.pack(">I", chunk_len) + png_data)

body = b"".join(chunks)
total_len = 8 + len(body)
icns_data = ICNS_MAGIC + struct.pack(">I", total_len) + body
(ASSETS / "icon.icns").write_bytes(icns_data)
print(f"✓  assets/icon.icns ({(ASSETS/'icon.icns').stat().st_size//1024} KB)")

print("\nAll icons written to assets/")
