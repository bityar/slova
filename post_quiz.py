"""Публикует по одной следующей викторине в каждый настроенный канал.

Для каждого языка в папке channels/<код>/ лежат:
  questions.json, questions_002.json, … — очередь вопросов (файлы читаются по порядку)
  topics.json    — названия тем для карточек и хэштегов
  state.json     — какой вопрос публиковать следующим

Переменные окружения:
  TELEGRAM_BOT_TOKEN   — токен бота от @BotFather (один на все каналы)
  TELEGRAM_CHANNEL_DE / _EN / _ES / _ZH — @адреса каналов
  PIXABAY_API_KEY      — ключ Pixabay для тематических фото (или PEXELS_API_KEY)
                         без ключа — карточки тем
  DRY_RUN=1            — ничего не публиковать, сохранить картинки в preview/
  CHECK_HOUR=true      — запуск по расписанию: публиковать, если наступил слот (время Германии)
  BUFFER_API_KEY, VIDEO_REPO_TOKEN — ролики в TikTok / Reels через Buffer (VIDEO_HOURS)
  FORCE_VIDEO=true     — ручная проверка: сразу один ролик, без Telegram
  TELEGRAM_ADMIN_CHAT_ID — твой id в Telegram для уведомлений; TEST_NOTIFY=true — тестовое сообщение
Канал без адреса или без вопросов пропускается.
"""
import html
import json
import os
import random
import sys
import uuid
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))

LANGS = ["de", "en", "es", "zh"]
CHANNEL_NAMES = {"de": "Deutsch за минуту", "en": "English за минуту",
                 "es": "Español за минуту", "zh": "中文 за минуту"}
# часы публикаций, паузы и прочие настройки — в control.json (tools/control_cfg.py)
CATCH_UP_HOURS = 3            # опоздавший слот публикуем, если прошло не больше 3 часов
SLOT_STATE = os.path.join(HERE, "schedule_state.json")
KEEP_ORDER = (["Der", "Die", "Das"],)  # наборы, которые не перемешиваем
LIMITS = {"question": 300, "description": 1024, "explanation": 200, "option": 100}


def build(item, topics, lang=None):
    """Собирает тексты поста из записи вопроса."""
    t = topics.get(item.get("topic"), {})
    sentence = item.get("sentence", "").strip()
    pinyin = item.get("pinyin", "").strip()
    translation = item.get("translation", "").strip()
    code = (lang or "xx").upper()
    if sentence and translation:
        # сначала русский перевод, потом предложение на изучаемом языке — с подписями и пустой строкой
        lines = [f"RU: {translation}", "", f"{code}: {sentence}"]
        if pinyin:
            lines.append(f"PY: {pinyin}")
    else:
        lines = [x for x in (sentence, pinyin, translation) if x]
    question = f"{item.get('emoji', '❓')} " + "\n".join(lines)
    level = item.get("level", "")  # A1–C1, для китайского — «HSK 1» … «HSK 6»
    tags = " ".join("#" + x.replace(" ", "") for x in [level, t.get("tag")] if x)
    description = html.escape(item["task"])
    if level:
        description += f"\n\nУровень: <b>{html.escape(level)}</b>"
    if tags:
        description += ("\n" if level else "\n\n") + tags
    explanation = "💡 " + item.get("explanation", "")
    if item.get("explanation"):  # пояснение скрытым спойлером над вопросом и вариантами
        description += f"\n\n💡 Почему так: <tg-spoiler>{html.escape(item['explanation'])}</tg-spoiler>"
    return question, description, explanation, t


def without_spoiler(description):
    """Описание без строки-спойлера — для запасной публикации."""
    return description.split("\n\n💡 Почему так:")[0]


def validate(items, topics):
    errors = []
    for i, it in enumerate(items):
        q, d, e, _ = build(it, topics)
        if len(q) > LIMITS["question"]:
            errors.append(f"#{i}: вопрос длиннее {LIMITS['question']}")
        if len(d) > LIMITS["description"]:
            errors.append(f"#{i}: описание длиннее {LIMITS['description']}")
        if len(e) > LIMITS["explanation"] or e.count("\n") > 2:
            errors.append(f"#{i}: объяснение длиннее {LIMITS['explanation']} или больше 2 переносов")
        if not 2 <= len(it["options"]) <= 10:
            errors.append(f"#{i}: нужно от 2 до 10 вариантов")
        if any(len(o) > LIMITS["option"] for o in it["options"]):
            errors.append(f"#{i}: вариант длиннее {LIMITS['option']}")
        if not 0 <= it["correct"] < len(it["options"]):
            errors.append(f"#{i}: неверный индекс правильного ответа")
    return errors


def shuffled(item, seed):
    order = list(range(len(item["options"])))
    if item["options"] not in KEEP_ORDER:
        random.Random(seed).shuffle(order)
    return [item["options"][k] for k in order], order.index(item["correct"])


def load_questions(folder):
    """Очередь канала = все файлы questions.json, questions_002.json, questions_003.json…
    по порядку. Новые пачки добавляются новым файлом, старые не трогаются."""
    names = sorted(n for n in os.listdir(folder)
                   if n == "questions.json" or (n.startswith("questions_") and n.endswith(".json")))
    names.sort(key=lambda n: (n != "questions.json", n))
    items = []
    for n in names:
        items += json.load(open(os.path.join(folder, n), encoding="utf-8"))
    return items


def picture(lang, item, t, seed):
    """Тематическое фото из фотобанка с водяным знаком канала, иначе — карточка темы.
    Возвращает (jpeg-байты или None, подпись «автор / фотобанк» или None)."""
    try:
        import make_card as mc
    except Exception as e:
        print(f"[{lang}] картинки недоступны ({e}), публикую без картинки")
        return None, None
    source, key = next(((n, os.environ[v]) for n, v in
                        (("Pixabay", "PIXABAY_API_KEY"), ("Pexels", "PEXELS_API_KEY"))
                        if os.environ.get(v)), (None, ""))
    if item.get("image_url"):  # картинку заранее выбрал Claude, посмотрев кандидатов
        try:
            img, credit = mc.photo_from_url(item["image_url"], item.get("image_credit", ""), lang, CHANNEL_NAMES[lang])
            if img:
                return img, credit
        except Exception as e:
            print(f"[{lang}] выбранное фото не загрузилось ({e})")
    if key and item.get("image"):
        try:
            img, credit = mc.photo_with_watermark(item["image"], source, key, lang, CHANNEL_NAMES[lang], seed)
            if img:
                return img, credit
            print(f"[{lang}] фото по запросу «{item['image']}» не нашлось, рисую карточку")
        except Exception as e:
            print(f"[{lang}] фото не загрузилось ({e}), рисую карточку")
    try:
        return mc.make_card(lang, CHANNEL_NAMES[lang], t.get("ru", "Викторина"),
                            t.get("native", ""), item.get("level", "")), None
    except Exception as e:
        print(f"[{lang}] карточку нарисовать не удалось ({e}), публикую без картинки")
        return None, None


def send_poll(token, fields, image):
    url = f"https://api.telegram.org/bot{token}/sendPoll"
    if image is None:
        data = json.dumps(fields).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    else:  # multipart: картинка идёт файлом, остальные поля — строками
        fields = dict(fields, media={"type": "photo", "media": "attach://card"})
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="card"; filename="card.jpg"\r\n'
                     f"Content-Type: image/jpeg\r\n\r\n".encode() + image + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as e:
        result = json.load(e)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram вернул ошибку: {result}")


def post_one(lang, token, dry):
    channel = os.environ.get(f"TELEGRAM_CHANNEL_{lang.upper()}", "")
    if lang == "de" and not channel:
        channel = os.environ.get("TELEGRAM_CHANNEL", "")
    folder = os.path.join(HERE, "channels", lang)
    items = load_questions(folder)
    tp = os.path.join(folder, "topics.json")
    topics = json.load(open(tp, encoding="utf-8")) if os.path.exists(tp) else {}
    state_path = os.path.join(folder, "state.json")
    state = json.load(open(state_path))

    if not channel and not dry:
        print(f"[{lang}] адрес канала не задан — пропускаю")
        return
    errors = validate(items, topics)
    if errors:
        raise RuntimeError("ошибки в questions.json: " + "; ".join(errors))
    idx = state["next"]
    if idx >= len(items):
        if items and channel:
            raise RuntimeError("вопросы закончились — нужна новая пачка")
        print(f"[{lang}] вопросов в очереди нет — пропускаю")
        return

    item = items[idx]
    question, description, explanation, t = build(item, topics, lang)
    options, correct = shuffled(item, idx)
    image, _ = picture(lang, item, t, idx)

    if dry:
        print(f"[{lang}] [DRY RUN] вопрос #{idx}\n--- описание:\n{description}\n--- вопрос:\n{question}")
        for k, o in enumerate(options):
            print(("  ✅ " if k == correct else "  • ") + o)
        print("--- объяснение: " + explanation)
        if image:
            os.makedirs(os.path.join(HERE, "preview"), exist_ok=True)
            open(os.path.join(HERE, "preview", f"{lang}-{idx}.jpg"), "wb").write(image)
        return

    fields = {
        "chat_id": channel,
        "question": question,
        "options": [{"text": o} for o in options],
        "type": "quiz",
        "correct_option_ids": [correct],
        "description": description,
        "description_parse_mode": "HTML",
        "is_anonymous": True,
    }
    try:
        send_poll(token, fields, image)
    except RuntimeError as e:
        if "<tg-spoiler>" not in description or "Bad Request" not in str(e):
            raise
        # Telegram не принял спойлер в описании — публикуем по-старому: пояснение всплывает после ответа
        print(f"[{lang}] спойлер в описании не принят ({e}), публикую со всплывающим пояснением")
        fields.update(description=without_spoiler(description), explanation=explanation)
        send_poll(token, fields, image)
    state["next"] = idx + 1
    json.dump(state, open(state_path, "w"), indent=2)
    print(f"[{lang}] опубликован вопрос #{idx}, в очереди осталось {len(items) - idx - 1}")
    return True


def due_slot(now, hours):
    """Последний наступивший слот из hours (не старше CATCH_UP_HOURS) или None."""
    for day in (now.date(), now.date() - timedelta(days=1)):
        for h in sorted(hours, reverse=True):
            slot = datetime(day.year, day.month, day.day, h, tzinfo=now.tzinfo)
            if slot <= now:
                return slot if now - slot <= timedelta(hours=CATCH_UP_HOURS) else None
    return None


def load_slots():
    return json.load(open(SLOT_STATE)) if os.path.exists(SLOT_STATE) else {}


def pending(now, slots):
    """Что надо опубликовать сейчас — по расписанию каждого канала.
    Возвращает ([(язык, слот)] для Telegram, [(язык, слот, "q"|"w")] для TikTok)."""
    import control_cfg
    cfg = control_cfg.load()
    last_tg = slots.setdefault("last_tg", {})
    last_vd = slots.setdefault("last_video", {})
    tg_due, vd_due = [], []
    for lang in LANGS:
        sch = control_cfg.schedule(cfg, lang)
        t = due_slot(now, sch["tg"])
        if t:
            key = t.strftime("%Y-%m-%d %H:00")
            prev = last_tg.get(lang, slots.get("last_slot"))  # совместимость со старым общим слотом
            if prev is None or key > prev:
                tg_due.append((lang, key))
        # ролики (викторина/«15 слов» — нужен Buffer) и уроки (TikTok + Telegram, работают и без Buffer)
        hours = [h for h, t in sch["tt"].items() if t == "e" or os.environ.get("BUFFER_API_KEY")]
        if hours:
            v = due_slot(now, hours)
            if v:
                key = v.strftime("%Y-%m-%d %H:00")
                prev = last_vd.get(lang, slots.get("last_video_slot"))
                if prev is None or key > prev:
                    vd_due.append((lang, key, sch["tt"][v.hour]))
    return tg_due, vd_due


def post_telegram(dry, slots, only_lang=None, ignore_pause=False):
    import control_cfg
    import notify
    cfg = control_cfg.load()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token and not dry:
        sys.exit("Не задан TELEGRAM_BOT_TOKEN")
    failed = []
    for lang in LANGS:
        if only_lang and lang != only_lang:
            continue
        if not ignore_pause and control_cfg.is_paused(cfg, "tg", lang):
            print(f"[{lang}] Telegram на паузе — пропускаю")
            continue
        try:
            if post_one(lang, token, dry) and not dry:
                notify.log_post(slots, lang, "tg")
        except Exception as e:  # один сломанный канал не мешает остальным
            print(f"[{lang}] ОШИБКА: {e}")
            failed.append(lang)
            if not dry:
                notify.alert(slots, f"Telegram: {e}", lang)
    return failed


def video_langs():
    """Языки, для которых в Buffer подключены каналы роликов."""
    if not os.environ.get("BUFFER_API_KEY"):
        return []
    try:
        import social
        from video_publish import CONFIG
        return [l for l, c in CONFIG.items() if social.channels_for(c.get("buffer_names") or [c["handle"]])]
    except Exception:
        return []


def main():
    import notify
    dry = os.environ.get("DRY_RUN") == "1"
    now = datetime.now(ZoneInfo("Europe/Berlin"))

    if "--plan" in sys.argv:  # для шага в GitHub Actions: нужен ли сейчас ffmpeg
        _, vd_due = pending(now, load_slots())
        scheduled = os.environ.get("CHECK_HOUR") == "true"
        need = bool(vd_due) and scheduled or os.environ.get("FORCE_VIDEO") == "true"
        import remote
        if remote.enabled():  # ролики собирает публичный репозиторий — здесь ничего ставить не нужно
            need = False
        words = need and (os.environ.get("VIDEO_FORMAT") == "words" or (scheduled and any(t == "w" for _, _, t in vd_due)))
        print(f"video={'true' if need else 'false'} words={'true' if words else 'false'}")
        if os.environ.get("GITHUB_OUTPUT"):
            open(os.environ["GITHUB_OUTPUT"], "a").write(
                f"video={'true' if need else 'false'}\nwords={'true' if words else 'false'}\n")
        return

    slots = load_slots()
    try:
        run(now, slots, dry, notify)
    except SystemExit:
        raise
    except Exception as e:  # непредвиденная ошибка — тоже сообщаем
        if not dry:
            notify.alert(slots, f"Непредвиденная ошибка: {e}")
        raise
    finally:
        if not dry:
            json.dump(slots, open(SLOT_STATE, "w"), indent=2)
            import status
            status.write()


def run(now, slots, dry, notify):
    if os.environ.get("TEST_NOTIFY") == "true":  # проверка уведомлений
        status = notify.queue_status(HERE, load_questions, video_langs())
        ok = notify.send("✅ <b>Уведомления работают.</b>\n\nЗапас контента (дней):\n" + "\n".join(
            f"{notify.LANG_NAME[l]}: " + ", ".join(x for x in (
                f"Telegram {int(dt)}" if dt is not None else None, f"ролики {int(dv)}" if dv is not None else None,
                f"слова {int(dw)}" if dw is not None else None) if x)
            for l, dt, dv, dw in status), [[("🏠 Меню", "m")]])
        if not ok:
            sys.exit("Не удалось отправить тестовое сообщение — проверь TELEGRAM_ADMIN_CHAT_ID и нажми /start у бота")
        return

    if os.environ.get("FORCE_VIDEO") == "true":  # ручная проверка: один ролик сейчас, без Telegram
        from video_publish import publish_video_slot
        errors, done = publish_video_slot(now.strftime("%Y-%m-%d %H:%M") + " test")
        for lang in done:
            notify.log_post(slots, lang, "video")
        for e in errors:
            print("ОШИБКА:", e)
            notify.alert(slots, e, e[1:3] if e.startswith("[") else None)
        if errors:
            sys.exit(1)
        return

    if os.environ.get("CHECK_HOUR") != "true":  # ручной запуск: викторины сразу
        failed = post_telegram(dry, slots)
        if failed:
            sys.exit(f"Не удалось опубликовать в: {', '.join(failed)}")
        return

    tg_due, vd_due = pending(now, slots)
    problems = []
    import remote
    if os.environ.get("VIDEO_ONLY") == "1":  # запуск в публичном репозитории: только ролики и уроки
        tg_due = []
    if remote.enabled() and not dry and os.environ.get("VIDEO_ONLY") != "1":
        today = now.strftime("%Y-%m-%d")
        if now.hour >= 9 and now.hour < 12 and slots.get("prerender_day") != today:
            try:
                remote.dispatch("prerender")
                slots["prerender_day"] = today
            except Exception as e:
                print(f"[заготовка] не запустилась: {e}")
    if os.environ.get("VIDEO_ONLY") == "1":
        pass
    elif vd_due and remote.enabled() and not dry:
        try:
            remote.dispatch("slot")
        except Exception as e:
            problems.append(f"не удалось передать сборку роликов в quiz-videos: {str(e)[:200]}")
            notify.alert(slots, problems[-1], None)
        vd_due = []
    if not tg_due and not vd_due:
        print(f"{now:%H:%M} по Германии — публиковать нечего")
    for lang, key in tg_due:
        print(f"[{lang}] викторина за слот {key}")
        failed = post_telegram(dry, slots, only_lang=lang)
        if not dry:  # слот отмечаем, даже если публикация упала, — чтобы не задвоить
            slots["last_tg"][lang] = key
        problems += [f"Telegram {x}" for x in failed]
    if vd_due:
        from video_publish import publish_video_slot
    for lang, key, kind in [x for x in vd_due if x[2] == "e"]:
        print(f"[{lang}] урок за слот {key}")
        if dry:
            continue
        import lesson_publish
        try:
            errors, report = lesson_publish.publish_lesson(lang, key)
        except Exception as e:
            errors, report = [f"[{lang}] урок не собран: {str(e)[:300]}"], []
        print("\n".join(report))
        notify.remember_sent(slots)
        slots["last_video"][lang] = key
        if errors and "нет готовых уроков" in errors[0]:
            notify.lessons_empty(slots, lang)
        else:
            for e in errors:
                notify.alert(slots, e, lang)
            problems += errors
    vd_due = [x for x in vd_due if x[2] != "e"]
    for lang, key, kind in vd_due:
        print(f"[{lang}] ролик ({'«15 слов»' if kind == 'w' else 'викторина'}) за слот {key}")
        os.environ["VIDEO_FORMAT"] = "words" if kind == "w" else "quiz"
        errors, done = ([], []) if dry else publish_video_slot(key, only_lang=lang, ignore_pause=False)
        os.environ.pop("VIDEO_FORMAT", None)
        for l in done:
            notify.log_post(slots, l, "video")
        for e in errors:
            print("ОШИБКА:", e)
            notify.alert(slots, e, lang)
        if not dry:
            slots["last_video"][lang] = key
            notify.remember_sent(slots)
        problems += errors
    if not dry and os.environ.get("VIDEO_ONLY") != "1":
        daily_tasks(now, slots, notify)
        if os.environ.get("BUFFER_API_KEY"):
            notify.buffer_stuck_check(slots)
    if problems:
        sys.exit("Были ошибки: " + "; ".join(problems))


def daily_tasks(now, slots, notify):
    """Раз в день и раз в неделю: запас контента, подписчики, рубежи, токены, итоги."""
    import control_cfg
    cfg = control_cfg.load()
    today = now.strftime("%Y-%m-%d")
    try:
        if now.hour >= 9 and slots.get("daily_done") != today:
            slots["daily_done"] = today
            status = notify.queue_status(HERE, load_questions, video_langs())
            notify.low_queue_warning(slots, status, today)
            notify.subscribers_snapshot(today, cfg, slots)
            notify.token_reminders(slots, cfg, today)
            notify.lessons_low(slots, today)
            if os.environ.get("VIDEO_REPO_TOKEN"):
                import social
                for l in LANGS:
                    try:
                        social.cleanup_old_videos(l)
                    except Exception as e:
                        print(f"[{l}] очистка старых роликов: {e}")
        if now.weekday() == 6 and now.hour >= 19 and slots.get("weekly_done") != today:
            slots["weekly_done"] = today
            notify.weekly_report(slots, notify.queue_status(HERE, load_questions, video_langs()))
        if cfg.get("digest") and now.hour >= 21 and slots.get("digest_done") != today:
            slots["digest_done"] = today
            notify.daily_digest(slots, today)
    except Exception as e:  # служебные сообщения не должны ронять публикацию
        print(f"[ежедневные задачи] ошибка: {e}")


if __name__ == "__main__":
    main()
