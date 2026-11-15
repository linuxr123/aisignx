"""Draws the add-in's icons (AiSignX.ico and the ribbon PNGs) into AiSignXPowerPoint/Resources.

Needs Pillow (pip install pillow). The icons are checked in, so this only has to be run to
change them: python tools/make_icons.py
"""
import math
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "AiSignXPowerPoint" / "Resources"
SS = 4  # supersampling for smooth edges


def gradient(size, a=(13, 110, 253), b=(111, 66, 193)):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return img


def base(size, badge):
    """A rounded gradient square with a white screen and either a play triangle or an up arrow."""
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img.paste(gradient(s).convert("RGBA"), (0, 0), mask)
    d = ImageDraw.Draw(img)
    # the screen
    x0, y0, x1, y1 = s * 0.17, s * 0.22, s * 0.83, s * 0.66
    d.rounded_rectangle([x0, y0, x1, y1], radius=s * 0.05, outline="white", width=max(2, int(s * 0.055)))
    # the stand
    d.rectangle([s * 0.45, y1, s * 0.55, s * 0.76], fill="white")
    d.rounded_rectangle([s * 0.31, s * 0.755, s * 0.69, s * 0.82], radius=s * 0.03, fill="white")
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if badge == "play":
        r = s * 0.12
        d.polygon([(cx - r * 0.7, cy - r), (cx - r * 0.7, cy + r), (cx + r * 1.1, cy)], fill="white")
    else:  # an arrow pointing up: upload
        w, h = s * 0.075, s * 0.15
        d.polygon([(cx, cy - h), (cx - w * 2.0, cy - h * 0.1), (cx + w * 2.0, cy - h * 0.1)], fill="white")
        d.rectangle([cx - w * 0.75, cy - h * 0.15, cx + w * 0.75, cy + h * 0.85], fill="white")
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icons = [base(n, "play") for n in sizes]
    icons[-1].save(OUT / "AiSignX.ico", sizes=[(n, n) for n in sizes], append_images=icons[:-1])
    for n in (32, 64):
        base(n, "upload").save(OUT / f"ribbon-upload-{n}.png")
    base(256, "play").save(OUT / "AiSignX-256.png")
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
