"""Картинки для викторин.

photo_with_watermark — тематическое фото (Pixabay или Pexels) + плашка канала в углу.
make_card           — запасная карточка темы, если фото найти не удалось.
"""
import io
import json
import os
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
NAVY, YELLOW, CREAM, MUTED = "#1F3557", "#F2C14E", "#F6F1E6", "#AEB9CB"


def font(name, size, weight):
    f = ImageFont.truetype(os.path.join(HERE, name), size)
    f.set_variation_by_axes([32, weight] if name.startswith("Inter") else [weight])
    return f


def has_cjk(text):
    return any("\u2e80" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff" for ch in text)


def font_for(text, name, size, weight):
    """Для строк с иероглифами берём Noto Sans SC — в нём есть и иероглифы, и кириллица."""
    return font("NotoSansSC.ttf", size, min(weight, 900)) if has_cjk(text) else font(name, size, weight)


def flag_de(d, x, y, w, h):
    for i, c in enumerate(["#000000", "#DD0000", "#FFCE00"]):
        d.rectangle([x, y + i * h / 3, x + w, y + (i + 1) * h / 3], fill=c)


def flag_es(d, x, y, w, h):
    d.rectangle([x, y, x + w, y + h], fill="#AA151B")
    d.rectangle([x, y + h / 4, x + w, y + 3 * h / 4], fill="#F1BF00")


def flag_en(d, x, y, w, h):
    t = max(1, h / 6)
    d.rectangle([x, y, x + w, y + h], fill="#012169")
    d.line([x, y, x + w, y + h], fill="#FFFFFF", width=round(t * 1.2))
    d.line([x, y + h, x + w, y], fill="#FFFFFF", width=round(t * 1.2))
    d.line([x, y, x + w, y + h], fill="#C8102E", width=max(1, round(t * 0.5)))
    d.line([x, y + h, x + w, y], fill="#C8102E", width=max(1, round(t * 0.5)))
    d.rectangle([x + w / 2 - t, y, x + w / 2 + t, y + h], fill="#FFFFFF")
    d.rectangle([x, y + h / 2 - t, x + w, y + h / 2 + t], fill="#FFFFFF")
    d.rectangle([x + w / 2 - t * 0.6, y, x + w / 2 + t * 0.6, y + h], fill="#C8102E")
    d.rectangle([x, y + h / 2 - t * 0.6, x + w, y + h / 2 + t * 0.6], fill="#C8102E")


def _star(cx, cy, r, rot=0.0):
    import math
    pts = []
    for i in range(10):
        a = math.radians(-90 + rot + i * 36)
        rr = r if i % 2 == 0 else r * 0.382
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def flag_zh(d, x, y, w, h):
    d.rectangle([x, y, x + w, y + h], fill="#EE1C25")
    u = w / 30
    d.polygon(_star(x + 5 * u, y + 5 * u, 3 * u), fill="#FFFF00")
    for cx, cy, rot in ((10, 2, 23), (12, 4, 46), (12, 7, 70), (10, 9, 21)):
        d.polygon(_star(x + cx * u, y + cy * u, u, rot), fill="#FFFF00")


FLAGS = {"de": flag_de, "en": flag_en, "es": flag_es, "zh": flag_zh}


def _png(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _watermark(img, lang, channel_name):
    """Полупрозрачная плашка с флагом и названием канала в левом нижнем углу."""
    W, H = img.size
    s = W / 1200
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = font_for(channel_name, "Unbounded.ttf", int(24 * s), 600)
    fw, fh = int(40 * s), int(26 * s)
    pad, gap = int(16 * s), int(12 * s)
    tw = d.textlength(channel_name, font=f)
    bw, bh = pad * 2 + fw + gap + tw, int(56 * s)
    x0, y0 = int(24 * s), H - int(24 * s) - bh
    d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=bh // 2, fill=(31, 53, 87, 215))
    fx, fy = x0 + pad, y0 + (bh - fh) / 2
    d.rounded_rectangle([fx - 2, fy - 2, fx + fw + 2, fy + fh + 2], radius=4, fill=CREAM)
    if lang in FLAGS:
        FLAGS[lang](d, fx, fy, fw, fh)
    d.text((fx + fw + gap, y0 + bh / 2), channel_name, font=f, fill=YELLOW, anchor="lm")
    return Image.alpha_composite(img.convert("RGBA"), layer)


def _search_pixabay(query, key):
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": key, "q": query, "image_type": "photo", "orientation": "horizontal",
        "safesearch": "true", "per_page": 5, "min_width": 1200})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "quiz-bot"}), timeout=30) as r:
        hits = json.load(r).get("hits", [])
    return [(h["largeImageURL"], h.get("user", "")) for h in hits]


def _search_pexels(query, key):
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
        {"query": query, "orientation": "landscape", "per_page": 5})
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "quiz-bot"})
    with urllib.request.urlopen(req, timeout=30) as r:
        photos = json.load(r).get("photos", [])
    return [(p["src"]["landscape"], p.get("photographer", "")) for p in photos]


SOURCES = {"Pixabay": _search_pixabay, "Pexels": _search_pexels}


_RECENT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "channels", "images_recent.json")


def _recent():
    try:
        return json.load(open(_RECENT))
    except Exception:
        return []


def photo_from_url(src, credit, lang, channel_name):
    with urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": "quiz-bot"}), timeout=60) as r:
        img = Image.open(io.BytesIO(r.read()))
    return _png(_watermark(_cover(img, 1200, 627), lang, channel_name)), credit or "Pixabay"


def photo_with_watermark(query, source, key, lang, channel_name, seed=0):
    """Ищет фото в фотобанке: самое релевантное из тех, что не было недавно. (jpeg-байты, подпись) или (None, None)."""
    found = SOURCES[source](query, key)
    if not found:
        return None, None
    recent = _recent()
    fresh = [f for f in found if f[0] not in recent]
    src, author = (fresh or found)[0]  # первое в выдаче — самое релевантное
    try:
        json.dump((recent + [src])[-300:], open(_RECENT, "w"))
    except Exception:
        pass
    with urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": "quiz-bot"}), timeout=60) as r:
        img = Image.open(io.BytesIO(r.read()))
    img = _cover(img, 1200, 627)
    credit = f"{author} / {source}" if author else source
    return _png(_watermark(img, lang, channel_name)), credit


def _cover(img, W, H):
    """Обрезает фото под нужные пропорции по центру."""
    sw, sh = img.size
    scale = max(W / sw, H / sh)
    img = img.resize((round(sw * scale), round(sh * scale)), Image.LANCZOS)
    l, t = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((l, t, l + W, t + H))


def make_card(lang, channel_name, topic_ru, topic_native, level):
    """Запасная карточка без фото: тема, уровень, плашка канала."""
    W, H = 1200, 627
    img = Image.new("RGBA", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    big = font_for(topic_ru, "Unbounded.ttf", 76, 800)
    while d.textlength(topic_ru, font=big) > 1040 and big.size > 40:
        big = font_for(topic_ru, "Unbounded.ttf", big.size - 4, 800)
    d.text((80, 250), topic_ru, font=big, fill=CREAM, anchor="ls")
    if topic_native:
        d.text((80, 320), topic_native, font=font_for(topic_native, "Inter.ttf", 44, 600), fill=MUTED, anchor="ls")
    if level:
        lf = font("Inter.ttf", 32, 700)
        tw = d.textlength(level, font=lf)
        d.rounded_rectangle([80, 380, 80 + tw + 44, 440], radius=30, fill=YELLOW)
        d.text((102, 410), level, font=lf, fill=NAVY, anchor="lm")
    return _png(_watermark(img, lang, channel_name))
