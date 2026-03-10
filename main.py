"""
👑 AI Подруга Лия — LEGENDARY VERSION
✅ Викторина и тесты на знания
✅ Расшифровка голосовых сообщений
✅ Краткий пересказ любого текста
✅ Генерация фото (Pollinations AI)
✅ Гороскоп, уход за собой, песни
✅ Любовные письма, список дел
✅ Курс валют, рецепты, медитации
✅ Дневник настроения
✅ Голосовые ответы
✅ Анализ фото и задач

Установка: pip install pyTelegramBotAPI gtts
"""

import telebot
from telebot import types
import requests
import json
import random
import base64
import os
import re
import tempfile
from datetime import datetime
from urllib.parse import quote

# ══════════════════════════════════════════════
TELEGRAM_TOKEN = "8582531187:AAEH94WlDJgUhqb_VwnRPNFqQ49aXPuLWM0"
GROQ_KEY       = "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"
# ══════════════════════════════════════════════

BOT_NAME  = "Лия"
USER_NAME = "Солнышко"

MODEL_TEXT      = "llama-3.3-70b-versatile"
MODEL_VISION    = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_WHISPER   = "whisper-large-v3-turbo"  # для расшифровки голоса

SYSTEM_PROMPT = f"""Ты — дружелюбная и умная AI-подруга по имени {BOT_NAME}.
Ты общаешься с девушкой по имени {USER_NAME}.
Характер: тёплая, заботливая, умная, с лёгким юмором.
Эмодзи используешь уместно. Отвечаешь по-русски как настоящая подруга.
Когда присылают фото с задачей — решай подробно и по шагам."""

bot         = telebot.TeleBot(TELEGRAM_TOKEN)
histories   = {}
modes       = {}
mood_log    = {}
todo_list   = {}
last_answer = {}
quiz_state  = {}   # { uid: {question, answer, score, total} }
MAX_HISTORY = 20

try:
    from gtts import gTTS
    VOICE_ENABLED = True
except ImportError:
    VOICE_ENABLED = False

# ══════════════════════════════════════════════
#  ДАННЫЕ
# ══════════════════════════════════════════════

ZODIAC_SIGNS = [
    "♈ Овен", "♉ Телец", "♊ Близнецы", "♋ Рак",
    "♌ Лев",  "♍ Дева",  "♎ Весы",     "♏ Скорпион",
    "♐ Стрелец", "♑ Козерог", "♒ Водолей", "♓ Рыбы"
]

QUIZ_TOPICS = {
    "🌍 География":   "geography",
    "🎬 Кино":        "movies",
    "🎵 Музыка":      "music",
    "🧪 Наука":       "science",
    "📚 Литература":  "literature",
    "🏆 Спорт":       "sport",
    "🍕 Еда":         "food",
    "💄 Красота":     "beauty",
    "🐾 Животные":    "animals",
    "🌟 Случайное":   "random",
}

COMPLIMENTS = [
    "Просто напоминаю — ты замечательная! ✨",
    "Ты умница и красавица, не забывай об этом 💕",
    "С тобой всегда так интересно! 🌸",
    "Ты справишься со всем, я в тебя верю 💪",
    "Твоя улыбка — лучшее что есть на свете 🌺",
    "Ты особенная, и это не просто слова 🦋",
    "Восхищаюсь тобой каждый день! 🌟",
]

AFFIRMATIONS = [
    "Я достойна любви и счастья 💕",
    "Я справляюсь со всем что встречается на пути 💪",
    "Каждый день я становлюсь лучше ✨",
    "Я окружена людьми которые меня любят 🌸",
    "У меня есть всё необходимое для счастья 🌟",
    "Я верю в себя и свои силы 🦋",
]

MEDITATIONS = [
    {"name": "🌬 Дыхание 4-7-8", "text":
        "Снимает тревогу за 2 минуты:\n\n"
        "1️⃣ Вдох — *4 секунды*\n2️⃣ Задержка — *7 секунд*\n3️⃣ Выдох — *8 секунд*\n\nПовтори 4 раза 🌿"},
    {"name": "🌊 Сканирование тела", "text":
        "Закрой глаза и расслабь:\n\n👣 Ступни → 🦵 Ноги → 🫁 Живот → 💪 Руки → 😌 Лицо\n\nПобудь так 5 минут 🌸"},
    {"name": "☀️ Утренняя настройка", "text":
        "Прямо сейчас:\n\n✨ 3 вещи за которые благодарна\n💫 Скажи: _'Сегодня я справлюсь со всем'_\n🌅 Представь идеальный день\n\nДелай каждое утро 🌟"},
    {"name": "🧘 Сброс стресса 5-4-3-2-1", "text":
        "Назови:\n\n5️⃣ вещей которые *видишь*\n4️⃣ которые *потрогаешь*\n3️⃣ звука которые *слышишь*\n2️⃣ запаха\n1️⃣ вкус\n\nВозвращает в момент 'здесь и сейчас' 💙"},
]

MOOD_EMOJIS = {
    "😊": "Хорошо", "🤩": "Отлично", "😔": "Грустно",
    "😤": "Злюсь",  "😰": "Тревожно", "😴": "Устала",
    "🥰": "Влюблена", "😐": "Нейтрально",
}

# ══════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════

def get_greeting():
    h = datetime.now().hour
    if 5  <= h < 12: return "☀️ Доброе утро"
    if 12 <= h < 17: return "🌤 Добрый день"
    if 17 <= h < 22: return "🌆 Добрый вечер"
    return "🌙 Не спишь"

def get_history(uid): 
    if uid not in histories: histories[uid] = []
    return histories[uid]

def get_mode(uid): return modes.get(uid, "normal")

def mode_system(uid):
    base = SYSTEM_PROMPT
    m = get_mode(uid)
    if m == "study":    return base + "\nРежим УЧЁБЫ: объясняй по шагам."
    if m == "support":  return base + "\nРежим ПОДДЕРЖКИ: будь особенно нежной."
    if m == "creative": return base + "\nРежим ТВОРЧЕСТВА: предлагай необычные идеи."
    return base

def mode_name(uid):
    return {"normal":"💬 Обычный","study":"📚 Учёба",
            "support":"🤗 Поддержка","creative":"🎨 Творчество"}.get(get_mode(uid),"💬 Обычный")

def ask_ai(uid, user_text, image_b64=None, custom_system=None):
    history = get_history(uid)
    sys = custom_system or mode_system(uid)

    if image_b64:
        content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": user_text or "Что на фото? Если задача — реши подробно."}
        ]
        messages = [{"role": "system", "content": sys}, {"role": "user", "content": content}]
        model = MODEL_VISION
    else:
        history.append({"role": "user", "content": user_text})
        if len(history) > MAX_HISTORY:
            histories[uid] = history[-MAX_HISTORY:]
            history = histories[uid]
        messages = [{"role": "system", "content": sys}] + history
        model = MODEL_TEXT

    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        data=json.dumps({"model": model, "messages": messages, "max_tokens": 1500}),
        timeout=40
    )
    data = r.json()
    if "error" in data:
        raise Exception(data["error"].get("message", "Ошибка API"))
    answer = data["choices"][0]["message"]["content"].strip()
    if not image_b64:
        history.append({"role": "assistant", "content": answer})
    return answer

def transcribe_voice(file_bytes, filename="voice.ogg"):
    """Расшифровка голосового через Groq Whisper."""
    r = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_KEY}"},
        files={"file": (filename, file_bytes, "audio/ogg")},
        data={"model": MODEL_WHISPER, "language": "ru", "response_format": "text"},
        timeout=30
    )
    if r.status_code == 200:
        return r.text.strip()
    raise Exception(f"Whisper error {r.status_code}: {r.text[:200]}")

def generate_image(prompt_ru):
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            data=json.dumps({"model": MODEL_TEXT,
                           "messages": [{"role": "user", "content": f"Translate to English for image generation (only translation): {prompt_ru}"}],
                           "max_tokens": 100}),
            timeout=15
        )
        prompt_en = r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        prompt_en = prompt_ru

    image_url = f"https://image.pollinations.ai/prompt/{quote(prompt_en)}?width=768&height=768&nologo=true"
    img_r = requests.get(image_url, timeout=45)
    if img_r.status_code == 200:
        return img_r.content
    return None

def get_currency():
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/RUB", timeout=10)
        rates = r.json().get("rates", {})
        usd = round(1 / rates.get("USD", 0.011), 2)
        eur = round(1 / rates.get("EUR", 0.010), 2)
        kzt = round(rates.get("KZT", 5.5), 2)
        return f"💰 *Курс валют:*\n\n🇺🇸 1 USD = {usd} ₽\n🇪🇺 1 EUR = {eur} ₽\n🇰🇿 1 ₽ = {kzt} ₸\n\n_Обновлено сейчас_ ⏱"
    except Exception:
        return "😔 Не могу получить курс валют."

def send_voice(chat_id, text):
    if not VOICE_ENABLED: return False
    try:
        clean = re.sub(r'[*_`]', '', text)[:500]
        tts = gTTS(text=clean, lang="ru", slow=False)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tts.save(tmp.name)
        with open(tmp.name, "rb") as f:
            bot.send_voice(chat_id, f)
        os.unlink(tmp.name)
        return True
    except Exception:
        return False

def make_quiz_question(uid, topic):
    """Генерирует вопрос для викторины."""
    topic_names = {v: k for k, v in QUIZ_TOPICS.items()}
    topic_label = topic_names.get(topic, "случайная тема")
    prompt = (
        f"Придумай интересный вопрос для викторины на тему: {topic_label}.\n"
        f"Формат ответа СТРОГО JSON:\n"
        f'{{"question": "текст вопроса", "answer": "правильный ответ", "hint": "подсказка", "fun_fact": "интересный факт об ответе"}}\n'
        f"Только JSON, никакого другого текста!"
    )
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        data=json.dumps({"model": MODEL_TEXT,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 300}),
        timeout=20
    )
    text = r.json()["choices"][0]["message"]["content"].strip()
    text = re.sub(r'```json|```', '', text).strip()
    return json.loads(text)

# ══════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════

def main_menu_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💬 Поговорить",      callback_data="mode_normal"),
        types.InlineKeyboardButton("📚 Учёба",           callback_data="mode_study"),
        types.InlineKeyboardButton("🤗 Поддержка",       callback_data="mode_support"),
        types.InlineKeyboardButton("🎨 Творчество",      callback_data="mode_creative"),
        types.InlineKeyboardButton("🎨 Нарисовать фото", callback_data="btn_imagine"),
        types.InlineKeyboardButton("🧠 Викторина",       callback_data="btn_quiz"),
        types.InlineKeyboardButton("🌙 Гороскоп",        callback_data="btn_horoscope"),
        types.InlineKeyboardButton("💄 Уход за собой",   callback_data="btn_beauty"),
        types.InlineKeyboardButton("💌 Любовное письмо", callback_data="btn_love"),
        types.InlineKeyboardButton("📖 Пересказ текста", callback_data="btn_summarize"),
        types.InlineKeyboardButton("🎵 Угадай песню",    callback_data="btn_song"),
        types.InlineKeyboardButton("📝 Список дел",      callback_data="btn_todo"),
        types.InlineKeyboardButton("💰 Курс валют",      callback_data="btn_currency"),
        types.InlineKeyboardButton("🍽 Рецепт",          callback_data="btn_recipe"),
        types.InlineKeyboardButton("🧘 Медитация",       callback_data="btn_meditation"),
        types.InlineKeyboardButton("📊 Настроение",      callback_data="btn_mood"),
        types.InlineKeyboardButton("✨ Комплимент",      callback_data="btn_compliment"),
        types.InlineKeyboardButton("🌟 Аффирмация",      callback_data="btn_affirmation"),
        types.InlineKeyboardButton("📸 Анализ фото",     callback_data="btn_photo_hint"),
        types.InlineKeyboardButton("🔄 Новый диалог",    callback_data="btn_new"),
    )
    return kb

def after_message_kb():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("📋 Меню",  callback_data="btn_menu"),
        types.InlineKeyboardButton("🎵 Голос", callback_data="btn_voice_last"),
        types.InlineKeyboardButton("🔄 Новый", callback_data="btn_new"),
    )
    return kb

def quiz_topic_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    for name, val in QUIZ_TOPICS.items():
        kb.add(types.InlineKeyboardButton(name, callback_data=f"quiz_start_{val}"))
    kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
    return kb

def zodiac_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=3)
    for s in ZODIAC_SIGNS:
        kb.add(types.InlineKeyboardButton(s, callback_data=f"zodiac_{s}"))
    return kb

def mood_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=4)
    kb.add(*[types.InlineKeyboardButton(e, callback_data=f"mood_{e}") for e in MOOD_EMOJIS])
    kb.add(types.InlineKeyboardButton("📈 История настроения", callback_data="mood_history"))
    return kb

def meditation_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, m in enumerate(MEDITATIONS):
        kb.add(types.InlineKeyboardButton(m["name"], callback_data=f"med_{i}"))
    return kb

def todo_keyboard(uid):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, t in enumerate(todo_list.get(uid, [])):
        icon = "✅" if t["done"] else "⬜"
        kb.add(types.InlineKeyboardButton(f"{icon} {t['text']}", callback_data=f"todo_toggle_{i}"))
    kb.add(
        types.InlineKeyboardButton("➕ Добавить", callback_data="todo_add"),
        types.InlineKeyboardButton("🗑 Очистить", callback_data="todo_clear"),
        types.InlineKeyboardButton("📋 Меню",     callback_data="btn_menu"),
    )
    return kb

# ══════════════════════════════════════════════
#  КОМАНДЫ
# ══════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid = msg.from_user.id
    histories[uid] = []
    modes[uid] = "normal"
    name = msg.from_user.first_name or USER_NAME
    bot.reply_to(msg,
        f"{get_greeting()}, {name}! 🌸\n\n"
        f"Я {BOT_NAME} — твоя AI-подруга 👑\n\n"
        f"🎨 Рисую картинки\n"
        f"🧠 Викторина и тесты\n"
        f"🎤 Расшифровка голосовых\n"
        f"📖 Пересказ текста\n"
        f"📸 Решаю задачи с фото\n"
        f"🌙 Гороскоп, 💌 Письма, 💄 Уход\n"
        f"+ ещё много всего!\n\n"
        f"Выбери с чего начнём 👇",
        reply_markup=main_menu_keyboard()
    )

@bot.message_handler(commands=["menu"])
def cmd_menu(msg):
    bot.reply_to(msg, f"Главное меню 🌸\nРежим: {mode_name(msg.from_user.id)}",
                reply_markup=main_menu_keyboard())

@bot.message_handler(commands=["new"])
def cmd_new(msg):
    histories[msg.from_user.id] = []
    bot.reply_to(msg, "🔄 Начнём с чистого листа! Что у тебя? 🌸")

@bot.message_handler(commands=["quiz"])
def cmd_quiz(msg):
    bot.reply_to(msg, "🧠 Выбери тему викторины:", reply_markup=quiz_topic_keyboard())

@bot.message_handler(commands=["summarize"])
def cmd_summarize(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "📖 Напиши текст для пересказа:\n/summarize [вставь текст сюда]")
        return
    _do_summarize(msg.chat.id, msg.from_user.id, parts[1], msg)

# ══════════════════════════════════════════════
#  ГЕНЕРАЦИЯ КАРТИНКИ
# ══════════════════════════════════════════════

def _generate_and_send(chat_id, uid, prompt, reply_to_msg=None):
    bot.send_chat_action(chat_id, "upload_photo")
    wait_msg = bot.send_message(chat_id, "🎨 Рисую для тебя... Подожди немного ✨")
    try:
        img_data = generate_image(prompt)
        bot.delete_message(chat_id, wait_msg.message_id)
        if img_data:
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🔄 Ещё раз", callback_data=f"imagine_again_{quote(prompt[:50])}"),
                types.InlineKeyboardButton("📋 Меню",    callback_data="btn_menu"),
            )
            if reply_to_msg:
                bot.reply_to(reply_to_msg, f"🎨 Готово! Запрос: _{prompt}_", parse_mode="Markdown")
            bot.send_photo(chat_id, img_data, reply_markup=kb)
        else:
            bot.send_message(chat_id, "😔 Не смогла нарисовать. Попробуй другое описание.")
    except Exception:
        try: bot.delete_message(chat_id, wait_msg.message_id)
        except: pass
        bot.send_message(chat_id, "😔 Ошибка при генерации. Попробуй позже.")

# ══════════════════════════════════════════════
#  ПЕРЕСКАЗ ТЕКСТА
# ══════════════════════════════════════════════

def _do_summarize(chat_id, uid, text, reply_to_msg=None):
    bot.send_chat_action(chat_id, "typing")
    try:
        prompt = (
            f"Сделай краткий пересказ следующего текста. "
            f"Выдели главную мысль, ключевые факты, и напиши вывод. "
            f"Отвечай по-русски, структурированно с эмодзи:\n\n{text}"
        )
        answer = ask_ai(uid, prompt, custom_system="Ты эксперт по анализу текста. Делай чёткие краткие пересказы.")
        last_answer[uid] = answer
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("📖 Ещё пересказ", callback_data="btn_summarize"),
            types.InlineKeyboardButton("📋 Меню",         callback_data="btn_menu"),
        )
        if reply_to_msg:
            bot.reply_to(reply_to_msg, answer, reply_markup=kb)
        else:
            bot.send_message(chat_id, answer, reply_markup=kb)
    except Exception:
        bot.send_message(chat_id, "😔 Не смогла пересказать. Попробуй ещё раз.")

# ══════════════════════════════════════════════
#  CALLBACKS
# ══════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    uid  = call.from_user.id
    data = call.data

    # ── Режимы ──
    if data.startswith("mode_"):
        m = data.replace("mode_", "")
        modes[uid] = m
        histories[uid] = []
        names = {"normal":"💬 Просто общаемся!","study":"📚 Помогу с учёбой!",
                 "support":"🤗 Я здесь 💕","creative":"🎨 Придумаем что-нибудь! ✨"}
        bot.answer_callback_query(call.id, "Режим изменён!")
        bot.send_message(uid, f"*{names.get(m)}*\n\nПиши, я слушаю 🌸", parse_mode="Markdown")

    elif data == "btn_menu":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, f"Главное меню 🌸\nРежим: {mode_name(uid)}", reply_markup=main_menu_keyboard())

    elif data == "btn_new":
        histories[uid] = []
        bot.answer_callback_query(call.id, "Очищено!")
        bot.send_message(uid, "🔄 Начнём с чистого листа! 🌸")

    elif data == "btn_compliment":
        bot.answer_callback_query(call.id, "💕")
        bot.send_message(uid, random.choice(COMPLIMENTS))

    elif data == "btn_affirmation":
        bot.answer_callback_query(call.id, "🌟")
        bot.send_message(uid, f"🌟 *Аффирмация дня:*\n\n_{random.choice(AFFIRMATIONS)}_\n\nПовтори 3 раза 💕",
                        parse_mode="Markdown")

    elif data == "btn_currency":
        bot.answer_callback_query(call.id, "Загружаю...")
        bot.send_chat_action(uid, "typing")
        bot.send_message(uid, get_currency(), parse_mode="Markdown")

    # ── Картинка ──
    elif data == "btn_imagine":
        bot.answer_callback_query(call.id)
        modes[uid] = "imagine_mode"
        bot.send_message(uid,
            "🎨 *Генератор картинок*\n\nОпиши что нарисовать:\n\n"
            "• _красивый закат над морем_\n"
            "• _уютная кофейня осенью_\n"
            "• _котик в шапке астронавта_\n\n"
            "Пиши на русском — переведу сама! 🌸",
            parse_mode="Markdown")

    elif data.startswith("imagine_again_"):
        prompt = data.replace("imagine_again_", "")
        bot.answer_callback_query(call.id, "Рисую...")
        _generate_and_send(call.message.chat.id, uid, prompt)

    # ── ВИКТОРИНА ──
    elif data == "btn_quiz":
        bot.answer_callback_query(call.id)
        state = quiz_state.get(uid, {})
        score = state.get("score", 0)
        total = state.get("total", 0)
        header = f"🏆 Счёт: {score}/{total}\n\n" if total > 0 else ""
        bot.send_message(uid,
            f"{header}🧠 *Викторина!*\n\nВыбери тему:",
            parse_mode="Markdown",
            reply_markup=quiz_topic_keyboard()
        )

    elif data.startswith("quiz_start_"):
        topic = data.replace("quiz_start_", "")
        bot.answer_callback_query(call.id, "Готовлю вопрос...")
        bot.send_chat_action(uid, "typing")
        wait = bot.send_message(uid, "🎲 Придумываю вопрос...")
        try:
            q = make_quiz_question(uid, topic)
            if uid not in quiz_state:
                quiz_state[uid] = {"score": 0, "total": 0}
            quiz_state[uid]["current"] = q
            quiz_state[uid]["topic"]   = topic
            quiz_state[uid]["total"]  += 1
            bot.delete_message(uid, wait.message_id)

            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("💡 Подсказка",    callback_data="quiz_hint"),
                types.InlineKeyboardButton("✅ Показать ответ", callback_data="quiz_answer"),
            )
            kb.add(types.InlineKeyboardButton("➡️ Другой вопрос", callback_data=f"quiz_start_{topic}"))
            kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))

            score = quiz_state[uid]["score"]
            total = quiz_state[uid]["total"]
            bot.send_message(uid,
                f"🧠 *Вопрос #{total}* | Счёт: {score}/{total-1}\n\n"
                f"*{q['question']}*\n\n"
                f"Напиши ответ или воспользуйся кнопками 👇",
                parse_mode="Markdown",
                reply_markup=kb
            )
            modes[uid] = "quiz_answer_mode"
        except Exception as e:
            try: bot.delete_message(uid, wait.message_id)
            except: pass
            bot.send_message(uid, "😔 Не смогла придумать вопрос. Попробуй другую тему!")

    elif data == "quiz_hint":
        bot.answer_callback_query(call.id)
        q = quiz_state.get(uid, {}).get("current", {})
        hint = q.get("hint", "Подсказок нет 🤷")
        bot.send_message(uid, f"💡 *Подсказка:*\n\n{hint}", parse_mode="Markdown")

    elif data == "quiz_answer":
        bot.answer_callback_query(call.id)
        q = quiz_state.get(uid, {}).get("current", {})
        answer = q.get("answer", "?")
        fact   = q.get("fun_fact", "")
        topic  = quiz_state.get(uid, {}).get("topic", "random")

        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✅ Я знала!",    callback_data="quiz_correct"),
            types.InlineKeyboardButton("❌ Не знала",   callback_data="quiz_wrong"),
        )
        kb.add(types.InlineKeyboardButton("➡️ Следующий вопрос", callback_data=f"quiz_start_{topic}"))

        bot.send_message(uid,
            f"✅ *Правильный ответ:*\n\n*{answer}*\n\n"
            f"🌟 *Интересный факт:*\n{fact}",
            parse_mode="Markdown",
            reply_markup=kb
        )
        modes[uid] = "normal"

    elif data in ("quiz_correct", "quiz_wrong"):
        if data == "quiz_correct":
            quiz_state.setdefault(uid, {})["score"] = quiz_state.get(uid, {}).get("score", 0) + 1
            bot.answer_callback_query(call.id, "🎉 Отлично!")
            bot.send_message(uid, f"🎉 Засчитано! Счёт: {quiz_state[uid]['score']}/{quiz_state[uid]['total']}")
        else:
            bot.answer_callback_query(call.id, "😔 Ничего, в следующий раз!")
            bot.send_message(uid, f"💪 Ничего, ты узнала что-то новое! Счёт: {quiz_state.get(uid,{}).get('score',0)}/{quiz_state.get(uid,{}).get('total',0)}")

    # ── Пересказ текста ──
    elif data == "btn_summarize":
        bot.answer_callback_query(call.id)
        modes[uid] = "summarize_mode"
        bot.send_message(uid,
            "📖 *Пересказ текста*\n\n"
            "Отправь любой текст — статью, сообщение, задание — "
            "я сделаю краткий пересказ с главными мыслями! ✨\n\n"
            "_Просто вставь текст следующим сообщением_",
            parse_mode="Markdown"
        )

    # ── Гороскоп ──
    elif data == "btn_horoscope":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "🌙 Выбери знак зодиака:", reply_markup=zodiac_keyboard())

    elif data.startswith("zodiac_"):
        sign = data.replace("zodiac_", "")
        bot.answer_callback_query(call.id, "Читаю звёзды...")
        bot.send_chat_action(uid, "typing")
        try:
            today = datetime.now().strftime("%d.%m.%Y")
            prompt = (f"Составь гороскоп на {today} для знака {sign}. "
                     f"Включи: общий прогноз, любовь, работа/учёба, здоровье, совет дня. "
                     f"Пиши красиво с эмодзи, по-русски, оптимистично.")
            answer = ask_ai(uid, prompt, custom_system="Ты астролог. Составляй красивые гороскопы.")
            last_answer[uid] = answer
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🔄 Другой знак", callback_data="btn_horoscope"),
                types.InlineKeyboardButton("📋 Меню",        callback_data="btn_menu"),
            )
            bot.send_message(uid, answer, reply_markup=kb)
        except Exception:
            bot.send_message(uid, "😔 Не смогла прочитать звёзды. Попробуй позже.")

    # ── Уход ──
    elif data == "btn_beauty":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("💡 Лайфхак дня",  callback_data="beauty_tip"),
            types.InlineKeyboardButton("🧴 Рутина ухода", callback_data="beauty_routine"),
            types.InlineKeyboardButton("💇 Волосы",       callback_data="beauty_hair"),
            types.InlineKeyboardButton("💅 Ногти",        callback_data="beauty_nails"),
            types.InlineKeyboardButton("🏋️ Упражнения",   callback_data="beauty_fitness"),
            types.InlineKeyboardButton("📋 Меню",         callback_data="btn_menu"),
        )
        bot.send_message(uid, "💄 *Уход за собой*\n\nЧто тебя интересует?",
                        parse_mode="Markdown", reply_markup=kb)

    elif data.startswith("beauty_"):
        sub = data.replace("beauty_", "")
        bot.answer_callback_query(call.id, "Загружаю...")
        bot.send_chat_action(uid, "typing")
        prompts = {
            "tip":     "Дай один крутой бьюти-лайфхак. Коротко и практично.",
            "routine": "Составь простую утреннюю и вечернюю рутину ухода за лицом.",
            "hair":    "Дай советы по уходу за волосами дома. Маски, лайфхаки, ошибки.",
            "nails":   "Расскажи как делать маникюр дома пошагово.",
            "fitness": "Предложи 5 упражнений для красивой фигуры дома без оборудования.",
        }
        try:
            answer = ask_ai(uid, prompts.get(sub, "Дай совет по уходу за собой."),
                          custom_system="Ты эксперт по красоте. Давай практичные советы.")
            last_answer[uid] = answer
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("💄 Ещё советы", callback_data="btn_beauty"),
                types.InlineKeyboardButton("📋 Меню",       callback_data="btn_menu"),
            )
            bot.send_message(uid, answer, reply_markup=kb)
        except Exception:
            bot.send_message(uid, "😔 Ошибка. Попробуй позже.")

    # ── Песня ──
    elif data == "btn_song":
        bot.answer_callback_query(call.id)
        modes[uid] = "song_mode"
        bot.send_message(uid,
            "🎵 *Угадай песню!*\n\nОпиши песню — угадаю!\n\n"
            "• По словам из текста\n• По описанию клипа\n• По настроению",
            parse_mode="Markdown")

    # ── Любовные письма ──
    elif data == "btn_love":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("💌 Письмо",         callback_data="love_letter"),
            types.InlineKeyboardButton("💐 Признание",      callback_data="love_confession"),
            types.InlineKeyboardButton("🌹 Доброе утро",    callback_data="love_morning"),
            types.InlineKeyboardButton("🌙 Спокойной ночи", callback_data="love_night"),
            types.InlineKeyboardButton("💔 Извинение",      callback_data="love_sorry"),
            types.InlineKeyboardButton("📋 Меню",           callback_data="btn_menu"),
        )
        bot.send_message(uid, "💌 *Что написать?*", parse_mode="Markdown", reply_markup=kb)

    elif data.startswith("love_"):
        sub = data.replace("love_", "")
        bot.answer_callback_query(call.id, "Пишу...")
        bot.send_chat_action(uid, "typing")
        prompts = {
            "letter":     "Напиши красивое романтическое письмо любимому человеку.",
            "confession": "Напиши красивое признание в любви.",
            "morning":    "Напиши романтическое доброе утро для любимого.",
            "night":      "Напиши нежное спокойной ночи для любимого.",
            "sorry":      "Напиши искреннее извинение для любимого.",
        }
        try:
            answer = ask_ai(uid, prompts.get(sub, "Напиши романтическое сообщение."),
                          custom_system="Ты романтичный поэт. Пишешь красивые нежные тексты.")
            last_answer[uid] = answer
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🔄 Другой вариант", callback_data=data),
                types.InlineKeyboardButton("💌 Ещё",            callback_data="btn_love"),
                types.InlineKeyboardButton("🎵 Голос",          callback_data="btn_voice_last"),
                types.InlineKeyboardButton("📋 Меню",           callback_data="btn_menu"),
            )
            bot.send_message(uid, answer, reply_markup=kb)
        except Exception:
            bot.send_message(uid, "😔 Ошибка. Попробуй позже.")

    # ── Список дел ──
    elif data == "btn_todo":
        bot.answer_callback_query(call.id)
        todos = todo_list.get(uid, [])
        done  = sum(1 for t in todos if t["done"])
        text  = f"📝 *Список дел*" + (f" ({done}/{len(todos)} ✅)" if todos else " — пустой")
        bot.send_message(uid, text, parse_mode="Markdown", reply_markup=todo_keyboard(uid))

    elif data == "todo_add":
        bot.answer_callback_query(call.id)
        modes[uid] = "todo_add_mode"
        bot.send_message(uid, "📝 Что добавить в список?")

    elif data == "todo_clear":
        todo_list[uid] = []
        bot.answer_callback_query(call.id, "Список очищен!")
        bot.send_message(uid, "🗑 Список очищен!", reply_markup=todo_keyboard(uid))

    elif data.startswith("todo_toggle_"):
        idx = int(data.replace("todo_toggle_", ""))
        todos = todo_list.get(uid, [])
        if idx < len(todos):
            todos[idx]["done"] = not todos[idx]["done"]
            status = "✅" if todos[idx]["done"] else "⬜"
            bot.answer_callback_query(call.id, status)
            try:
                bot.edit_message_reply_markup(uid, call.message.message_id, reply_markup=todo_keyboard(uid))
            except: pass

    # ── Рецепт ──
    elif data == "btn_recipe":
        bot.answer_callback_query(call.id)
        modes[uid] = "recipe_mode"
        bot.send_message(uid,
            "🍽 *Рецепт по продуктам*\n\nНапиши что есть в холодильнике:\n\n"
            "_'яйца, сыр, помидоры'_\n_'курица, картошка, лук'_",
            parse_mode="Markdown")

    # ── Медитация ──
    elif data == "btn_meditation":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "🧘 Выбери практику:", reply_markup=meditation_keyboard())

    elif data.startswith("med_"):
        idx = int(data.replace("med_", ""))
        med = MEDITATIONS[idx]
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🧘 Ещё практики", callback_data="btn_meditation"),
            types.InlineKeyboardButton("📋 Меню",         callback_data="btn_menu"),
        )
        bot.send_message(uid, f"*{med['name']}*\n\n{med['text']}",
                        parse_mode="Markdown", reply_markup=kb)

    # ── Настроение ──
    elif data == "btn_mood":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "Как ты себя чувствуешь? 🌈", reply_markup=mood_keyboard())

    elif data.startswith("mood_") and data != "mood_history":
        emoji = data.replace("mood_", "")
        now = datetime.now().strftime("%d.%m %H:%M")
        if uid not in mood_log: mood_log[uid] = []
        mood_log[uid].append({"time": now, "mood": emoji, "name": MOOD_EMOJIS.get(emoji, "")})
        bot.answer_callback_query(call.id, f"Записала {emoji}")
        try:
            response = ask_ai(uid, f"Настроение пользователя: {emoji} {MOOD_EMOJIS.get(emoji)}. Отреагируй тепло.")
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("📈 История", callback_data="mood_history"),
                types.InlineKeyboardButton("📋 Меню",   callback_data="btn_menu"),
            )
            bot.send_message(uid, response, reply_markup=kb)
        except Exception:
            bot.send_message(uid, f"Записала {emoji} в дневник! 📊")

    elif data == "mood_history":
        bot.answer_callback_query(call.id)
        log = mood_log.get(uid, [])
        if not log:
            bot.send_message(uid, "📊 Дневник пустой!")
        else:
            lines = [f"{e['time']} — {e['mood']} {e['name']}" for e in log[-10:]]
            bot.send_message(uid, "📊 *Последние записи:*\n\n" + "\n".join(lines), parse_mode="Markdown")

    # ── Голос ──
    elif data == "btn_voice_last":
        text = last_answer.get(uid, "")
        if not text:
            bot.answer_callback_query(call.id, "Нет текста!")
        elif not VOICE_ENABLED:
            bot.answer_callback_query(call.id, "Установи gtts!")
            bot.send_message(uid, "❌ Установи: `pip install gtts`", parse_mode="Markdown")
        else:
            bot.answer_callback_query(call.id, "🎵 Озвучиваю...")
            bot.send_chat_action(uid, "record_voice")
            send_voice(uid, text)

    elif data == "btn_photo_hint":
        bot.answer_callback_query(call.id)
        bot.send_message(uid,
            "📸 *Анализ фото:*\n\n"
            "1. Нажми скрепку 📎\n2. Выбери фото\n"
            "3. В подписи напиши что нужно\n\n"
            "_Без подписи — сама разберусь!_ 😊",
            parse_mode="Markdown")

# ══════════════════════════════════════════════
#  ГОЛОСОВЫЕ СООБЩЕНИЯ 🎤
# ══════════════════════════════════════════════

@bot.message_handler(content_types=["voice"])
def handle_voice(msg):
    uid = msg.from_user.id
    bot.send_chat_action(msg.chat.id, "typing")
    wait = bot.reply_to(msg, "🎤 Слушаю и расшифровываю...")
    try:
        file_info = bot.get_file(msg.voice.file_id)
        file_url  = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        audio     = requests.get(file_url, timeout=20).content

        text = transcribe_voice(audio)
        bot.delete_message(uid, wait.message_id)

        bot.send_message(uid, f"🎤 *Ты сказала:*\n_{text}_", parse_mode="Markdown")

        # Отвечаем на распознанный текст как на обычное сообщение
        bot.send_chat_action(uid, "typing")
        answer = ask_ai(uid, text)
        last_answer[uid] = answer
        bot.send_message(uid, answer, reply_markup=after_message_kb())

    except Exception as e:
        try: bot.delete_message(uid, wait.message_id)
        except: pass
        bot.reply_to(msg, "😔 Не смогла расшифровать голосовое. Попробуй написать текстом.")

# ══════════════════════════════════════════════
#  ФОТО 📸
# ══════════════════════════════════════════════

@bot.message_handler(content_types=["photo"])
def handle_photo(msg):
    uid = msg.from_user.id
    bot.send_chat_action(msg.chat.id, "typing")
    try:
        photo     = msg.photo[-1]
        file_info = bot.get_file(photo.file_id)
        file_url  = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        image_b64 = base64.b64encode(requests.get(file_url, timeout=20).content).decode("utf-8")
        bot.send_message(uid, "📸 Смотрю на фото...")
        answer = ask_ai(uid, msg.caption or "", image_b64=image_b64)
        last_answer[uid] = answer
        for i in range(0, len(answer), 4096):
            if i + 4096 >= len(answer):
                bot.reply_to(msg, answer[i:i+4096], reply_markup=after_message_kb())
            else:
                bot.reply_to(msg, answer[i:i+4096])
    except Exception:
        bot.reply_to(msg, "😔 Не смогла обработать фото. Попробуй ещё раз.")

# ══════════════════════════════════════════════
#  ТЕКСТ 💬
# ══════════════════════════════════════════════

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(msg):
    uid  = msg.from_user.id
    text = msg.text.strip()
    current_mode = modes.get(uid, "normal")
    bot.send_chat_action(msg.chat.id, "typing")

    # ── Специальные режимы ──
    if current_mode == "imagine_mode":
        modes[uid] = "normal"
        _generate_and_send(msg.chat.id, uid, text, msg)
        return

    if current_mode == "summarize_mode":
        modes[uid] = "normal"
        _do_summarize(msg.chat.id, uid, text, msg)
        return

    if current_mode == "quiz_answer_mode":
        q = quiz_state.get(uid, {}).get("current", {})
        correct = q.get("answer", "").lower().strip()
        user_ans = text.lower().strip()
        topic = quiz_state.get(uid, {}).get("topic", "random")
        modes[uid] = "normal"

        if correct and (correct in user_ans or user_ans in correct):
            quiz_state[uid]["score"] = quiz_state.get(uid, {}).get("score", 0) + 1
            score = quiz_state[uid]["score"]
            total = quiz_state[uid]["total"]
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("➡️ Следующий", callback_data=f"quiz_start_{topic}"),
                types.InlineKeyboardButton("📋 Меню",      callback_data="btn_menu"),
            )
            fact = q.get("fun_fact", "")
            bot.reply_to(msg,
                f"🎉 *Правильно!* Счёт: {score}/{total}\n\n🌟 {fact}",
                parse_mode="Markdown", reply_markup=kb)
        else:
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("✅ Показать ответ",  callback_data="quiz_answer"),
                types.InlineKeyboardButton("➡️ Следующий",      callback_data=f"quiz_start_{topic}"),
            )
            bot.reply_to(msg, "🤔 Не совсем... Попробуй ещё раз или посмотри ответ 👇",
                        reply_markup=kb)
            modes[uid] = "quiz_answer_mode"
        return

    if current_mode == "song_mode":
        modes[uid] = "normal"
        text = f"Пользователь описывает песню: '{text}'. Угадай название и исполнителя. Если не уверена — предложи варианты."

    if current_mode == "recipe_mode":
        modes[uid] = "normal"
        text = f"У пользователя есть продукты: {text}. Придумай вкусный рецепт с пошаговой инструкцией."

    if current_mode == "todo_add_mode":
        modes[uid] = "normal"
        if uid not in todo_list: todo_list[uid] = []
        todo_list[uid].append({"text": text, "done": False})
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("➕ Ещё", callback_data="todo_add"),
            types.InlineKeyboardButton("📝 Список", callback_data="btn_todo"),
        )
        bot.reply_to(msg, f"✅ Добавила: *{text}*", parse_mode="Markdown", reply_markup=kb)
        return

    # ── Обычный чат ──
    try:
        answer = ask_ai(uid, text)
        last_answer[uid] = answer
        for i in range(0, len(answer), 4096):
            if i + 4096 >= len(answer):
                bot.reply_to(msg, answer[i:i+4096], reply_markup=after_message_kb())
            else:
                bot.reply_to(msg, answer[i:i+4096])
    except requests.exceptions.Timeout:
        bot.reply_to(msg, "⏳ Долго думаю... Попробуй ещё раз 💤")
    except requests.exceptions.ConnectionError:
        bot.reply_to(msg, "📵 Нет интернета.")
    except Exception as e:
        if "429" in str(e).lower() or "limit" in str(e).lower():
            bot.reply_to(msg, "⏳ Слишком много сообщений! Подожди минуту 💤")
        else:
            bot.reply_to(msg, "😔 Что-то пошло не так... Попробуй /new")

# ══════════════════════════════════════════════
voice_status = "✅ ГОЛОС ВКЛ" if VOICE_ENABLED else "❌ ГОЛОС ВЫКЛ (pip install gtts)"
print(f"👑 Бот '{BOT_NAME}' LEGENDARY VERSION запущен!")
print(f"🧠 Викторина: ВКЛ | 🎤 Расшифровка голоса: ВКЛ | 📖 Пересказ: ВКЛ")
print(f"🎨 Генерация фото: ВКЛ | 🌙 Гороскоп: ВКЛ | 💌 Письма: ВКЛ")
print(f"🎵 {voice_status}")
bot.infinity_polling(timeout=30, long_polling_timeout=20)
