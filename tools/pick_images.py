"""Подбор картинок к вопросам Telegram «глазами» Claude.

  python tools/pick_images.py sheets gen/out.json   — для каждого вопроса до 6 кандидатов из Pixabay → листы-превью
                                                       gen/images/sheet-NN.png (вопрос + пронумерованные картинки)
  python tools/pick_images.py apply gen/out.json    — применить выбор из gen/image_choices.json:
                                                       {"0": 3, "1": 0, ...} — номер картинки 1–6, 0 — ни одна не подходит

Выбранная картинка записывается в вопрос (image_url, image_credit) — при публикации поиск уже не нужен.
Если ни одна не подошла — вопрос выходит с карточкой темы, а не со случайным фото.
"""
import io
import json
import os
import sys
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_card import font_for  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "gen", "images")
PER_SHEET = 5
THUMB = (300, 170)


def _items(data):
    return data["questions"] if isinstance(data, dict) and "questions" in data else data


def search(query, key, n=6):
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": key, "q": query, "image_type": "photo", "orientation": "horizontal", "safesearch": "true",
        "per_page": max(3, n), "min_width": 1200})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "quiz-bot"}), timeout=30) as r:
        hits = json.load(r).get("hits", [])
    return [{"url": h["largeImageURL"], "preview": h.get("webformatURL") or h["previewURL"], "author": h.get("user", ""),
             "id": h.get("id")} for h in hits[:n]]


def _thumb(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "quiz-bot"}), timeout=30) as r:
        im = Image.open(io.BytesIO(r.read())).convert("RGB")
    im.thumbnail((THUMB[0] * 2, THUMB[1] * 2))
    w, h = im.size
    k = max(THUMB[0] / w, THUMB[1] / h)
    im = im.resize((int(w * k) + 1, int(h * k) + 1))
    x, y = (im.width - THUMB[0]) // 2, (im.height - THUMB[1]) // 2
    return im.crop((x, y, x + THUMB[0], y + THUMB[1]))


def sheets(path):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        print("нет PIXABAY_API_KEY — подбор картинок пропущен")
        return
    data = json.load(open(path, encoding="utf-8"))
    items = _items(data)
    os.makedirs(OUT, exist_ok=True)
    cand = {}
    for i, q in enumerate(items):
        found, seen = [], set()
        for query in [q.get("image")] + list(q.get("images_alt") or []):
            if not query or len(found) >= 6:
                continue
            try:
                for c in search(query, key):
                    if c["id"] not in seen and len(found) < 6:
                        found.append(c)
                        seen.add(c["id"])
            except Exception as e:
                print(f"[{i}] поиск «{query}» не удался: {e}")
        cand[str(i)] = found
    json.dump(cand, open(os.path.join(OUT, "candidates.json"), "w"), ensure_ascii=False)
    ft, fs, fn = font_for("А", "Inter.ttf", 26, 700), font_for("А", "Inter.ttf", 22, 500), font_for("1", "Inter.ttf", 30, 800)
    names = []
    for s0 in range(0, len(items), PER_SHEET):
        rows = list(range(s0, min(s0 + PER_SHEET, len(items))))
        sheet = Image.new("RGB", (60 + 6 * (THUMB[0] + 10), len(rows) * (THUMB[1] + 90) + 20), (24, 26, 33))
        d = ImageDraw.Draw(sheet)
        for r, i in enumerate(rows):
            q = items[i]
            y = 20 + r * (THUMB[1] + 90)
            text = f"#{i}  {q.get('sentence') or ''}  —  {q.get('translation') or ''}".strip()
            d.text((20, y), text[:150], font=font_for(text, "Inter.ttf", 26, 700), fill=(240, 240, 245))
            d.text((20, y + 32), f"поиск: {q.get('image', '')}", font=fs, fill=(150, 156, 170))
            for k, c in enumerate(cand[str(i)]):
                x = 20 + k * (THUMB[0] + 10)
                try:
                    sheet.paste(_thumb(c["preview"]), (x, y + 66))
                except Exception:
                    d.rectangle([x, y + 66, x + THUMB[0], y + 66 + THUMB[1]], outline=(90, 90, 90))
                d.rectangle([x, y + 66, x + 44, y + 106], fill=(255, 206, 84))
                d.text((x + 22, y + 86), str(k + 1), font=fn, fill=(15, 16, 20), anchor="mm")
            if not cand[str(i)]:
                d.text((20, y + 110), "ничего не нашлось", font=fs, fill=(255, 120, 120))
        name = os.path.join(OUT, f"sheet-{s0 // PER_SHEET + 1:02d}.png")
        sheet.save(name)
        names.append(name)
    print("листы:", *names, sep="\n  ")


def apply(path):
    data = json.load(open(path, encoding="utf-8"))
    items = _items(data)
    cp = os.path.join(ROOT, "gen", "image_choices.json")
    cand = json.load(open(os.path.join(OUT, "candidates.json")))
    choices = json.load(open(cp)) if os.path.exists(cp) else {}
    chosen = none = 0
    for i, q in enumerate(items):
        k = int(choices.get(str(i), 0) or 0)
        c = cand.get(str(i), [])
        if 1 <= k <= len(c):
            q["image_url"], q["image_credit"] = c[k - 1]["url"], f"{c[k - 1]['author']} / Pixabay"
            chosen += 1
        elif str(i) in choices:
            q["image"] = ""  # ни одна не подошла — будет карточка темы
            q.pop("image_url", None)
            none += 1
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"картинки выбраны: {chosen}, без фото (карточка темы): {none}, без решения: {len(items) - chosen - none}")


if __name__ == "__main__":
    {"sheets": sheets, "apply": apply}[sys.argv[1]](sys.argv[2])
