"""Render a 400x300 3-colour frame: weekly calendar (left) + credits/tasks (right)."""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

W, H = 400, 300
BLACK = (0, 0, 0)
RED = (198, 32, 32)
WHITE = (255, 255, 255)

def _pick_font(candidates, fallback):
    """按候选顺序取第一个存在的字体，便于跨平台与跨机器复用。"""
    for p in candidates:
        if os.path.exists(p):
            return p
    return fallback


F_CJK = _pick_font([
    os.environ.get("EPD_FONT_CJK", ""),
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
], "C:/Windows/Fonts/simhei.ttf")

# 雅黑笔画更细，用于天气/节日这类易黏连的小字
F_UI = _pick_font([
    os.environ.get("EPD_FONT_UI", ""),
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttf",
    "/System/Library/Fonts/PingFang.ttc",
], F_CJK)

F_NUM = _pick_font([
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
], "C:/Windows/Fonts/arialbd.ttf")

_cache: dict = {}


def font(size: int, num: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, num)
    if key not in _cache:
        _cache[key] = ImageFont.truetype(F_NUM if num else F_CJK, size)
    return _cache[key]


def ufont(size: int) -> ImageFont.FreeTypeFont:
    key = ("ui", size)
    if key not in _cache:
        _cache[key] = ImageFont.truetype(F_UI, size)
    return _cache[key]


def text_on_dark(img: Image.Image, xy, text: str, fnt, anchor=None, thresh: int = 60) -> None:
    """Draw light text onto a dark area: antialiased edges are promoted to solid fill.

    pack_bw() thresholds at 140, so a thin stroke whose coverage lands near 50%
    would otherwise be quantised to the background colour and vanish.
    """
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).text(xy, text, font=fnt, fill=255, anchor=anchor)
    img.paste(WHITE, (0, 0), mask.point(lambda v: 255 if v >= thresh else 0))


def text_solid(img: Image.Image, xy, text: str, fnt, colour, anchor=None, thresh: int = 110) -> None:
    """Draw coloured text through a coverage mask, then fill with a pure colour.

    The red plane used to accept any pixel with r > 160 && r > g && r > b, which
    included the pale pink fringe of antialiased glyphs — the glyphs grew outward
    and adjacent strokes merged. Painting pure red removes that fringe.
    """
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).text(xy, text, font=fnt, fill=255, anchor=anchor)
    img.paste(colour, (0, 0), mask.point(lambda v: 255 if v >= thresh else 0))


# ---------------- weather glyphs (drawn, not fonts) ----------------
ICON = 16


def _sun():
    m = Image.new("L", (ICON, ICON), 0)
    d = ImageDraw.Draw(m)
    d.ellipse([5, 5, 10, 10], fill=255)
    for x0, y0, x1, y1 in [(7, 0, 7, 2), (7, 13, 7, 15), (0, 7, 2, 7), (13, 7, 15, 7)]:
        d.line([x0, y0, x1, y1], fill=255)
    for a, b in [(3, 3), (11, 3), (3, 11), (11, 11)]:
        d.line([a, b, a + 1, b + 1], fill=255)
    return m


def _cloud(dy=0, x0=2, erase=None):
    m = Image.new("L", (ICON, ICON), 0)
    d = ImageDraw.Draw(m)
    d.ellipse([x0, 7 + dy, x0 + 5, 11 + dy], fill=255)
    d.ellipse([x0 + 4, 3 + dy, x0 + 10, 9 + dy], fill=255)
    d.ellipse([x0 + 8, 6 + dy, x0 + 13, 11 + dy], fill=255)
    d.rectangle([x0 + 2, 8 + dy, x0 + 12, 11 + dy], fill=255)
    return m


def weather_glyph(code: int) -> Image.Image:
    c = int(code)
    if c in (0, 1):
        return _sun()
    if c == 2:
        m = _sun()
        ImageDraw.Draw(m).rectangle([6, 6, 15, 15], fill=0)
        m.paste(255, (0, 0), _cloud(dy=3, x0=2))
        return m
    if c in (3,):
        return _cloud()
    if c in (45, 48):
        m = Image.new("L", (ICON, ICON), 0)
        d = ImageDraw.Draw(m)
        for y, a, b in [(3, 2, 13), (7, 1, 14), (11, 3, 12), (14, 2, 9)]:
            d.line([a, y, b, y], fill=255)
        return m
    if c in (71, 73, 75, 77, 85, 86):
        m = _cloud(dy=-2)
        d = ImageDraw.Draw(m)
        for x in (4, 8, 12):
            d.line([x, 12, x, 14], fill=255)
            d.line([x - 1, 13, x + 1, 13], fill=255)
        return m
    if c in (95, 96, 99):
        m = _cloud(dy=-3)
        ImageDraw.Draw(m).polygon([(9, 10), (5, 15), (8, 15), (6, 15), (11, 12), (8, 12)], fill=255)
        return m
    # 各类降雨：用雨滴数量表达强度
    drops = {51: 1, 53: 2, 61: 2, 80: 2, 55: 3, 63: 3, 65: 3, 81: 3, 82: 3, 66: 2, 67: 3}.get(c, 2)
    m = _cloud(dy=-2)
    d = ImageDraw.Draw(m)
    xs = {1: [8], 2: [5, 11], 3: [4, 8, 12]}[drops]
    for x in xs:
        d.line([x, 12, x - 1, 15], fill=255)
    return m


def fit(draw: ImageDraw.ImageDraw, text: str, max_w: float, size: int, num: bool = False) -> str:
    f = font(size, num)
    if draw.textlength(text, font=f) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return text + "…"


def render(data: dict) -> Image.Image:
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    def txt(xy, text, fnt, fill=BLACK, anchor=None):
        """统一入口：红色走覆盖率蒙版，白色（黑底）走低阈值，黑色直绘。"""
        if fill is RED:
            text_solid(img, xy, text, fnt, RED, anchor)
        elif fill is WHITE:
            text_on_dark(img, xy, text, fnt, anchor)
        else:
            d.text(xy, text, font=fnt, fill=BLACK, anchor=anchor)

    # ================= left: week calendar =================
    lx, rx = 8, 246
    txt((lx, 4), "本周", font(15), RED)
    txt((lx + 42, 8), data["range"], font(12))
    txt((rx, 8), f"第{data['week_no']}周", font(12), RED, "ra")
    d.line([lx, 26, rx, 26], fill=BLACK, width=1)

    y = 30
    rh = 36
    for day in data["days"]:
        # 休息日：法定节假日，或周六周日；但调休补班的周末算工作日，不标红
        is_rest = (bool(day["holiday"]) or day["weekend"]) and not day["workday"]
        if day["today"]:
            d.rectangle([lx - 2, y + 1, rx + 2, y + rh - 3], fill=BLACK)
            fg = WHITE
        elif is_rest:
            fg = RED
        else:
            fg = BLACK
        txt((lx + 2, y + 8), day["week"], font(13), fg)
        txt((lx + 78, y + 9), f"{day['month']}/{day['day']:02d}", font(12, num=True), fg, "ra")
        txt((lx + 84, y + 8), day["lunar"], font(12), fg)

        if day["holiday"]:
            txt((rx + 2, y + 8), day["holiday"], ufont(14), fg, "ra")
        else:
            if day["workday"]:
                txt((rx + 2, y + 9), "补班", font(12), fg, "ra")
                wx_x = rx - 26
            else:
                wx_x = rx + 2
            if day["tmax"] is not None:
                temp = f"{day['tmax']}/{day['tmin']}"
                if data.get("weather_icon"):
                    tw = d.textlength(temp, font=ufont(14))
                    ix = wx_x - 2 - tw - ICON
                    glyph = weather_glyph(day.get("code", 0))
                    if fg is RED:
                        img.paste(RED, (int(ix), int(y + 3)), glyph)
                    elif fg is WHITE:
                        img.paste(WHITE, (int(ix), int(y + 3)), glyph)
                    else:
                        img.paste(BLACK, (int(ix), int(y + 3)), glyph)
                    txt((wx_x, y + 7), temp, ufont(14), fg, "ra")
                else:
                    txt((wx_x, y + 7), f"{day['weather']} {temp}", ufont(14), fg, "ra")
        y += rh

    # ================= divider =================
    d.line([250, 4, 250, H - 4], fill=BLACK, width=1)

    # ================= right: credits =================
    rx0 = 258
    cr = data["credits"]
    txt((rx0, 1), "积分", font(12))
    if cr.get("remaining") is not None:
        big, label = cr["remaining"], "剩余"
    else:
        big, label = cr["month"], "本月已用"
    txt((rx0 - 2, 12), f"{big:.1f}", font(36, num=True))
    txt((rx0, 50), label, font(12), RED)
    txt((rx0, 64), f"今日 -{cr['today']:.1f}", font(12))
    d.line([rx0, 82, W - 8, 82], fill=BLACK, width=1)

    # ================= right: running tasks =================
    tasks = data["tasks"]
    txt((rx0, 84), "进行中", font(12))
    txt((W - 8, 85), str(len(tasks)), font(12, num=True), RED, "ra")
    ty = 104
    if not tasks:
        txt((rx0, ty + 6), "暂无执行中任务", font(12))
        ty += 26
    for t in tasks[:3]:
        txt((rx0, ty), fit(d, t["title"], W - 10 - rx0, 12), font(12))
        meta = f"{t['scope']} · {t['minutes']} 分钟"
        if t.get("auto"):
            meta = "自动 · " + meta
        txt((rx0, ty + 16), meta, font(12), RED)
        ty += 34

    # ================= 今日已完成 =================
    done = data.get("done") or {"count": 0, "items": []}
    dy = min(ty + 4, 227)
    d.line([rx0, dy - 6, W - 8, dy - 6], fill=BLACK, width=1)
    txt((rx0, dy - 2), "今日完成", font(12))
    txt((W - 8, dy - 1), str(done["count"]), font(12, num=True), RED, "ra")
    items = done.get("items") or []
    if not items:
        txt((rx0, dy + 16), "—", font(12))
    room = max(1, (H - 32 - (dy + 16)) // 34)
    for i, it in enumerate(items[:room]):
        yy = dy + 16 + i * 34
        txt((rx0, yy), fit(d, it["title"], W - 10 - rx0, 12), font(12))
        txt((rx0, yy + 16), f"{it['time']} · {it['scope']}", font(12), RED)

    # ================= footer =================
    d.line([rx0, H - 30, W - 8, H - 30], fill=BLACK, width=1)
    txt((rx0, H - 24), f"更新 {data['generated_at']}", font(12))
    txt((W - 8, H - 24), data["city"], font(12), BLACK, "ra")
    return img


if __name__ == "__main__":
    import collect

    im = render(collect.build(0))
    im.save("preview.png")
    im.resize((800, 600), Image.NEAREST).save("preview_2x.png")
    print("saved preview.png")
