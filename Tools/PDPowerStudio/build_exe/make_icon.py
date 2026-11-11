"""Gera assets/pdpower.ico: raio com degradê azul→violeta sobre fundo escuro arredondado."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([16, 16, S - 16, S - 16], radius=110, fill=(11, 15, 23, 255), outline=(44, 61, 92, 255), width=10)
# raio (mesmo desenho do SVG do app, viewBox 32 → escala 16)
pts = [(18, 2), (6, 18), (14.5, 18), (12, 30), (25, 13), (16.5, 13)]
pts = [(x * 13 + 48, y * 13 + 48) for x, y in pts]
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).polygon(pts, fill=255)
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for k in range(S):
    t = k / (S - 1)
    c = (int(56 + (167 - 56) * t), int(189 + (139 - 189) * t), int(248 + (250 - 248) * t), 255)
    gd.line([(0, k), (S, k)], fill=c)
glow = Image.new("RGBA", (S, S), (56, 189, 248, 0))
glow.putalpha(mask.filter(ImageFilter.GaussianBlur(18)).point(lambda a: a * 0.55))
img = Image.alpha_composite(img, glow)
img.paste(grad, (0, 0), mask)
out = Path(__file__).resolve().parent.parent / "assets"
out.mkdir(exist_ok=True)
img.save(out / "pdpower.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(out / "pdpower.png")
print("ok", out)
