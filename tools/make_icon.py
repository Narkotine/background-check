"""Génère `bgc.ico` (Background Check) : un « pouls » sur fond sombre, multi-tailles.

Icône neutre (plus celle de Hermes, qui reste l'icône de SA tuile) : un tracé de moniteur —
l'app garde un œil sur des services en arrière-plan. Relancer : `py tools/make_icon.py`.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

BG_TOP, BG_BOTTOM = (28, 36, 54), (14, 18, 28)
BORDER = (64, 82, 120)
PULSE = (61, 220, 151)
GLOW = (61, 220, 151, 70)
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Fond : carré arrondi en dégradé vertical.
    grad = Image.new("RGBA", (size, size))
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / (size - 1)
        gd.line([(0, y), (size, y)], fill=tuple(int(a + (b - a) * t)
                                                for a, b in zip(BG_TOP, BG_BOTTOM)) + (255,))
    mask = Image.new("L", (size, size), 0)
    pad = size * 0.04
    radius = size * 0.22
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, size - pad, size - pad], radius, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius, outline=BORDER,
                        width=max(1, int(size * 0.018)))
    # Tracé de pouls : plat, pic haut, creux, plat — lisible même en 16 px.
    s = size
    points = [(0.14, 0.56), (0.36, 0.56), (0.45, 0.30), (0.56, 0.76), (0.64, 0.50),
              (0.70, 0.56), (0.86, 0.56)]
    pts = [(x * s, y * s) for x, y in points]
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=GLOW, width=int(s * 0.12), joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.03))
    glow.putalpha(Image.composite(glow.getchannel("A"), Image.new("L", (size, size), 0), mask))
    img.alpha_composite(glow)
    d.line(pts, fill=PULSE + (255,), width=int(s * 0.075), joint="curve")
    for x, y in (pts[0], pts[-1]):
        r = s * 0.0375
        d.ellipse([x - r, y - r, x + r, y + r], fill=PULSE + (255,))
    return img


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    big = draw()
    images = [big.resize((n, n), Image.LANCZOS) for n in SIZES]
    out = os.path.join(root, "bgc.ico")
    images[-1].save(out, format="ICO", sizes=[(n, n) for n in SIZES],
                    append_images=images[:-1])
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(root, "bgc.png"))
    print(out)


if __name__ == "__main__":
    main()
