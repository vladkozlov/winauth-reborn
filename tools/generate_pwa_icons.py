"""Generate the project's original, font-independent PWA mark."""

from pathlib import Path

from PIL import Image, ImageDraw

target = Path(__file__).resolve().parents[1] / "winauth_reborn" / "static" / "pwa"
target.mkdir(parents=True, exist_ok=True)
image = Image.new("RGB", (512, 512), "#101214")
draw = ImageDraw.Draw(image)
draw.polygon(
    [
        (256, 100),
        (395, 156),
        (378, 305),
        (340, 361),
        (256, 412),
        (172, 361),
        (134, 305),
        (117, 156),
    ],
    fill="#b4ef82",
)
draw.line([(180, 212), (217, 305), (256, 247), (295, 305), (332, 212)], fill="#101214", width=28)
for size in (180, 192, 512):
    image.resize((size, size), Image.Resampling.LANCZOS).save(target / f"icon-{size}.png")
image.save(target / "maskable-512.png")
