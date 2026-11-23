"""產生 DjvuReader.ico：藍底圓角方塊上一張摺角白頁，頁上幾行字。"""
import os
import sys
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=200, fill=(38, 92, 168))

# 摺角白頁
L, T, R, B, F = 250, 150, 774, 874, 170
d.polygon([(L, T), (R - F, T), (R, T + F), (R, B), (L, B)], fill=(250, 250, 250))
d.polygon([(R - F, T), (R - F, T + F), (R, T + F)], fill=(196, 208, 226))

# 文字行
y = 380
for i, w in enumerate([420, 420, 420, 300, 0, 420, 420, 360]):
    if w:
        d.rounded_rectangle((L + 52, y, L + 52 + w, y + 34), radius=17, fill=(120, 136, 160))
    y += 60
# 標題行
d.rounded_rectangle((L + 52, 250, L + 300, 300), radius=25, fill=(204, 62, 54))

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(here, "DjvuReader.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(os.path.join(here, "icon_preview.png"))
print(out)
