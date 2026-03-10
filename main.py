"""
👑 AI Подруга Лия — ULTRA LEGENDARY VERSION 2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Хранение данных в Upstash Redis (вечное, переживает деплой)
✅ Все 3 фикса применены
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
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote
from http.server import HTTPServer, BaseHTTPRequestHandler

# ══════════════════════════════════════════════
#  ТОКЕНЫ
# ══════════════════════════════════════════════
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GROQ_KEY       = os.environ.get("GROQ_KEY", "")

# Upstash Redis — берём из переменных окружения Render
UPSTASH_URL   = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# ══════════════════════════════════════════════
#  КОНФИГ
# ══════════════════════════════════════════════
BOT_NAME        = "Лия"
USER_NAME       = "Солнышко"
PRICE_STARS     = 100
PRICE_RUB       = "99 рублей"
PAYMENT_LINK    = "https://t.me/tronqx"
TRIAL_DAYS      = 3
FREE_MSG_LIMIT  = 10
DAILY_MSG_LIMIT = 999
FREE_DAILY_LIMIT= 20
LOG_FILE        = "/tmp/liya_log.txt"

VIP_USERNAMES   = {"tronqx", "dhl1929"}
ADMIN_USERNAMES = {"tronqx"}

MODEL_TEXT    = "llama-3.3-70b-versatile"
MODEL_VISION  = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_WHISPER = "whisper-large-v3-turbo"

SYSTEM_PROMPT = f"""Ты — дружелюбная и умная AI-подруга по имени {BOT_NAME}.
Ты общаешься с девушкой по имени {USER_NAME}.
Характер: тёплая, заботливая, умная, с лёгким юмором.
Эмодзи используешь уместно. Отвечаешь по-русски как настоящая подруга.
ВАЖНО: Пиши просто и понятно, без лишних технических терминов.
Когда присылают фото с задачей — решай подробно и по шагам."""


# ══════════════════════════════════════════════
#  REDIS КЛИЕНТ (Upstash REST API)
# ══════════════════════════════════════════════

class RedisClient:
    """Простой клиент к Upstash Redis через REST API. Не нужен redis-py."""

    def __init__(self, url, token):
        self.url     = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    def _cmd(self, *args):
        """Выполняет Redis команду."""
        try:
            r = requests.post(
                self.url,
                headers=self.headers,
                json=list(args),
                timeout=10
            )
            data = r.json()
            return data.get("result")
        except Exception as e:
            log_event(f"Redis error: {e}")
            return None

    def get(self, key):
        raw = self._cmd("GET", key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw

    def set(self, key, value):
        self._cmd("SET", key, json.dumps(value, ensure_ascii=False, default=str))

    def delete(self, key):
        self._cmd("DEL", key)

    def sadd(self, key, *members):
        self._cmd("SADD", key, *members)

    def srem(self, key, member):
        self._cmd("SREM", key, member)

    def smembers(self, key):
        result = self._cmd("SMEMBERS", key)
        return set(result) if result else set()

    def exists(self, key):
        return self._cmd("EXISTS", key) == 1


def log_event(text):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {text}\n")
    except Exception:
        pass


# ══════════════════════════════════════════════
#  ХРАНИЛИЩЕ ДАННЫХ (Redis)
# ══════════════════════════════════════════════

class DataStore:
    """
    Все данные хранятся в Upstash Redis.
    Ключи:
      user:{uid}        — данные подписки  {"expires": ..., "plan": ...}
      stats:{uid}       — статистика       {"total_msgs": ..., "daily": {...}, ...}
      msgcount:{uid}    — дневной счётчик  {"date": ..., "count": ...}
      referral:{uid}    — реферальный код  {"code": ..., "invited": [...], "bonus_days": ...}
      notes:{uid}       — заметки          [{"text":..., "date":...}, ...]
      reminders:{uid}   — напоминания      [{"text":..., "time":..., "daily":...}, ...]
      blocked           — SET заблокированных uid
      paid_uids         — SET uid с подпиской
    """

    def __init__(self):
        self.r = RedisClient(UPSTASH_URL, UPSTASH_TOKEN)
        # Локальный кэш для часто читаемых данных (ускоряет работу)
        self._cache = {}

    # ── Пользователи ──
    def get_user(self, uid):
        return self.r.get(f"user:{uid}")

    def set_user(self, uid, expires, plan="paid"):
        data = {
            "expires": expires.isoformat() if isinstance(expires, datetime) else expires,
            "plan": plan
        }
        self.r.set(f"user:{uid}", data)
        self.r.sadd("paid_uids", str(uid))

    def remove_user(self, uid):
        self.r.delete(f"user:{uid}")
        self.r.srem("paid_uids", str(uid))

    def has_access(self, uid, username=""):
        uid = str(uid)
        if username and username.lower().lstrip("@") in VIP_USERNAMES:
            return True
        if self.is_blocked(uid):
            return False
        u = self.get_user(uid)
        if not u:
            return False
        expires = u.get("expires")
        if expires is None:
            return True
        try:
            exp_dt = datetime.fromisoformat(expires)
            if datetime.now() < exp_dt:
                return True
            else:
                self.remove_user(uid)
                return False
        except Exception:
            return False

    def sub_status(self, uid):
        uid = str(uid)
        if self.is_blocked(uid):
            return "🚫 Заблокирован"
        u = self.get_user(uid)
        if not u:
            return "❌ Нет подписки"
        expires = u.get("expires")
        plan    = u.get("plan", "paid")
        if expires is None:
            return f"♾ Бессрочная ({plan})"
        try:
            exp_dt = datetime.fromisoformat(expires)
            if datetime.now() < exp_dt:
                left = exp_dt - datetime.now()
                return f"✅ {plan} до {exp_dt.strftime('%d.%m.%Y')} ({left.days}д)"
            return "❌ Истекла"
        except Exception:
            return "❓ Неизвестно"

    # ── Блокировки ──
    def block(self, uid):
        self.r.sadd("blocked", str(uid))

    def unblock(self, uid):
        self.r.srem("blocked", str(uid))

    def is_blocked(self, uid):
        return str(uid) in self.r.smembers("blocked")

    # ── Счётчик сообщений ──
    def count_message(self, uid):
        uid   = str(uid)
        today = datetime.now().strftime("%Y-%m-%d")

        mc = self.r.get(f"msgcount:{uid}") or {"date": today, "count": 0}
        if mc["date"] != today:
            mc = {"date": today, "count": 0}
        mc["count"] += 1
        self.r.set(f"msgcount:{uid}", mc)

        st = self.r.get(f"stats:{uid}") or {}
        st["total_msgs"] = st.get("total_msgs", 0) + 1
        daily = st.get("daily", {})
        daily[today] = daily.get(today, 0) + 1
        st["daily"] = daily
        self.r.set(f"stats:{uid}", st)

        return mc["count"], st["total_msgs"]

    def get_daily_count(self, uid):
        uid   = str(uid)
        today = datetime.now().strftime("%Y-%m-%d")
        mc    = self.r.get(f"msgcount:{uid}") or {"date": today, "count": 0}
        if mc["date"] != today:
            return 0
        return mc["count"]

    # ── Статистика ──
    def register_user(self, uid, username, name):
        uid = str(uid)
        st  = self.r.get(f"stats:{uid}")
        if not st:
            self.r.set(f"stats:{uid}", {
                "total_msgs": 0,
                "daily": {},
                "joined": datetime.now().isoformat(),
                "username": username,
                "name": name,
            })
            self.r.sadd("all_uids", uid)
            return True
        else:
            st["username"] = username
            st["name"]     = name
            self.r.set(f"stats:{uid}", st)
            return False

    def get_analytics(self):
        all_uids   = self.r.smembers("all_uids")
        paid_uids  = self.r.smembers("paid_uids")
        blocked    = self.r.smembers("blocked")
        today      = datetime.now().strftime("%Y-%m-%d")
        yesterday  = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        week_ago   = (datetime.now() - timedelta(days=7)).isoformat()

        total_msgs = 0
        dau_today  = 0
        dau_yest   = 0
        new_week   = 0
        top_list   = []

        for uid in all_uids:
            st = self.r.get(f"stats:{uid}") or {}
            msgs = st.get("total_msgs", 0)
            total_msgs += msgs
            if today in st.get("daily", {}):
                dau_today += 1
            if yesterday in st.get("daily", {}):
                dau_yest += 1
            if st.get("joined", "0") > week_ago:
                new_week += 1
            top_list.append((uid, st))

        top_list.sort(key=lambda x: x[1].get("total_msgs", 0), reverse=True)

        return {
            "total_users": len(all_uids),
            "paid_users":  len(paid_uids),
            "blocked":     len(blocked),
            "dau_today":   dau_today,
            "dau_yest":    dau_yest,
            "total_msgs":  total_msgs,
            "new_week":    new_week,
            "top_users":   top_list[:5],
        }

    # ── Рефералы ──
    def get_ref_code(self, uid):
        uid  = str(uid)
        data = self.r.get(f"referral:{uid}")
        if not data:
            data = {"code": f"ref{uid}", "invited": [], "bonus_days": 0}
            self.r.set(f"referral:{uid}", data)
        return data["code"]

    def apply_referral(self, new_uid, ref_code):
        new_uid = str(new_uid)
        all_uids = self.r.smembers("all_uids")
        for owner_uid in all_uids:
            ref_data = self.r.get(f"referral:{owner_uid}")
            if not ref_data:
                continue
            if (ref_data["code"] == ref_code
                    and new_uid not in ref_data["invited"]
                    and owner_uid != new_uid):
                ref_data["invited"].append(new_uid)
                ref_data["bonus_days"] = ref_data.get("bonus_days", 0) + 7
                self.r.set(f"referral:{owner_uid}", ref_data)

                # +7 дней к подписке пригласившего
                owner_int = int(owner_uid)
                current = self.get_user(owner_int)
                if current and current.get("expires"):
                    try:
                        exp = datetime.fromisoformat(current["expires"])
                        new_exp = exp + timedelta(days=7)
                        self.set_user(owner_int, new_exp, plan=current.get("plan", "paid"))
                    except Exception:
                        new_exp = datetime.now() + timedelta(days=7)
                        self.set_user(owner_int, new_exp, plan="referral_bonus")
                elif not (current and current.get("expires") is None):
                    new_exp = datetime.now() + timedelta(days=7)
                    self.set_user(owner_int, new_exp, plan="referral_bonus")
                return owner_int
        return None

    # ── Заметки ──
    def add_note(self, uid, text):
        uid   = str(uid)
        notes = self.r.get(f"notes:{uid}") or []
        notes.append({"text": text, "date": datetime.now().isoformat()})
        if len(notes) > 50:
            notes = notes[-50:]
        self.r.set(f"notes:{uid}", notes)

    def get_notes(self, uid):
        return self.r.get(f"notes:{uid}") or []

    # ── Напоминания ──
    def add_reminder(self, uid, text, time_str, daily=False):
        uid       = str(uid)
        reminders = self.r.get(f"reminders:{uid}") or []
        reminders.append({
            "text": text, "time": time_str, "daily": daily,
            "created": datetime.now().isoformat()
        })
        self.r.set(f"reminders:{uid}", reminders)

    def get_reminders(self, uid):
        return self.r.get(f"reminders:{uid}") or []

    def remove_reminder(self, uid, idx):
        uid       = str(uid)
        reminders = self.r.get(f"reminders:{uid}") or []
        if 0 <= idx < len(reminders):
            reminders.pop(idx)
            self.r.set(f"reminders:{uid}", reminders)

    def all_reminders(self):
        """Возвращает все напоминания всех пользователей."""
        all_uids = self.r.smembers("all_uids")
        result   = {}
        for uid in all_uids:
            rems = self.r.get(f"reminders:{uid}")
            if rems:
                result[uid] = rems
        return result

    # ── Совместимость с экспортом ──
    @property
    def data(self):
        """Для совместимости с adm_export и analytics."""
        all_uids  = self.r.smembers("all_uids")
        paid_uids = self.r.smembers("paid_uids")
        blocked   = list(self.r.smembers("blocked"))

        paid_users = {}
        for uid in paid_uids:
            u = self.r.get(f"user:{uid}")
            if u:
                paid_users[uid] = u

        stats = {}
        for uid in all_uids:
            st = self.r.get(f"stats:{uid}")
            if st:
                stats[uid] = st

        referrals = {}
        for uid in all_uids:
            ref = self.r.get(f"referral:{uid}")
            if ref:
                referrals[uid] = ref

        return {
            "paid_users": paid_users,
            "blocked":    blocked,
            "stats":      stats,
            "referrals":  referrals,
        }


# ══════════════════════════════════════════════
#  ИНИЦИАЛИЗАЦИЯ
# ══════════════════════════════════════════════
db  = DataStore()
bot = telebot.TeleBot(TELEGRAM_TOKEN)

histories   = {}
modes       = {}
mood_log    = {}
todo_list   = {}
last_answer = {}
quiz_state  = {}
MAX_HISTORY = 20

try:
    from gtts import gTTS
    VOICE_ENABLED = True
except ImportError:
    VOICE_ENABLED = False


# ══════════════════════════════════════════════
#  ВЕБ-СЕРВЕР
# ══════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        try:
            a    = db.get_analytics()
            resp = (f"Liya Bot v2.0 Redis | Users: {a['total_users']} | "
                    f"Paid: {a['paid_users']} | DAU: {a['dau_today']}").encode()
        except Exception:
            resp = b"Liya Bot v2.0 Redis | OK"
        self.wfile.write(resp)
    def log_message(self, *args): pass

def run_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()


# ══════════════════════════════════════════════
#  ПЛАНИРОВЩИК НАПОМИНАНИЙ
# ══════════════════════════════════════════════
def reminder_scheduler():
    while True:
        try:
            now_str = datetime.now().strftime("%H:%M")
            for uid_str, reminders in list(db.all_reminders().items()):
                to_remove = []
                for i, r in enumerate(reminders):
                    if r.get("time") == now_str:
                        try:
                            bot.send_message(int(uid_str),
                                f"⏰ Напоминание!\n\n{r['text']}\n\n"
                                f"{'🔁 Ежедневное' if r.get('daily') else ''}",
                                reply_markup=types.InlineKeyboardMarkup().add(
                                    types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")
                                ))
                        except Exception:
                            pass
                        if not r.get("daily"):
                            to_remove.append(i)
                for idx in reversed(to_remove):
                    db.remove_reminder(uid_str, idx)
        except Exception as e:
            log_event(f"Reminder scheduler error: {e}")
        time.sleep(60)

threading.Thread(target=reminder_scheduler, daemon=True).start()


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
    if m == "study":    return base + "\nРежим УЧЁБЫ: объясняй по шагам, просто и понятно."
    if m == "support":  return base + "\nРежим ПОДДЕРЖКИ: будь особенно нежной и заботливой."
    if m == "creative": return base + "\nРежим ТВОРЧЕСТВА: предлагай необычные идеи."
    return base

def mode_name(uid):
    return {"normal":"💬 Обычный","study":"📚 Учёба",
            "support":"🤗 Поддержка","creative":"🎨 Творчество"}.get(get_mode(uid),"💬 Обычный")

def is_admin(username):
    return username and username.lower().lstrip("@") in ADMIN_USERNAMES

def is_vip(username):
    return username and username.lower().lstrip("@") in VIP_USERNAMES

def check_daily_limit(uid, username=""):
    if is_vip(username): return True, 0, 999
    user = db.get_user(uid)
    if not user:
        count = db.get_daily_count(uid)
        return count < FREE_MSG_LIMIT, count, FREE_MSG_LIMIT
    plan  = user.get("plan", "paid")
    count = db.get_daily_count(uid)
    if plan == "trial":
        return count < FREE_DAILY_LIMIT, count, FREE_DAILY_LIMIT
    return True, count, DAILY_MSG_LIMIT

def ask_ai(uid, user_text, image_b64=None, custom_system=None):
    history = get_history(uid)
    sys = custom_system or mode_system(uid)

    if image_b64:
        content  = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": user_text or "Что на фото? Если задача — реши подробно."}
        ]
        messages = [{"role": "system", "content": sys}, {"role": "user", "content": content}]
        model    = MODEL_VISION
    else:
        history.append({"role": "user", "content": user_text})
        if len(history) > MAX_HISTORY:
            histories[uid] = history[-MAX_HISTORY:]
            history = histories[uid]
        messages = [{"role": "system", "content": sys}] + history
        model    = MODEL_TEXT

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
    r = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_KEY}"},
        files={"file": (filename, file_bytes, "audio/ogg")},
        data={"model": MODEL_WHISPER, "language": "ru", "response_format": "text"},
        timeout=30
    )
    if r.status_code == 200:
        return r.text.strip()
    raise Exception(f"Whisper error {r.status_code}")

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
        r     = requests.get("https://api.exchangerate-api.com/v4/latest/RUB", timeout=10)
        rates = r.json().get("rates", {})
        usd   = round(1 / rates.get("USD", 0.011), 2)
        eur   = round(1 / rates.get("EUR", 0.010), 2)
        kzt   = round(rates.get("KZT", 5.5), 2)
        uah   = round(rates.get("UAH", 0.4), 2)
        return (f"💰 Курс валют:\n\n🇺🇸 1 USD = {usd} ₽\n🇪🇺 1 EUR = {eur} ₽\n"
                f"🇰🇿 1 ₽ = {kzt} ₸\n🇺🇦 1 ₽ = {uah} ₴\n\nОбновлено сейчас ⏱")
    except Exception:
        return "😔 Не могу получить курс валют."

def get_weather(city):
    try:
        r       = requests.get(f"https://wttr.in/{quote(city)}?format=j1&lang=ru", timeout=10)
        data    = r.json()
        current = data["current_condition"][0]
        temp    = current["temp_C"]
        feels   = current["FeelsLikeC"]
        desc    = current["lang_ru"][0]["value"]
        humid   = current["humidity"]
        wind    = current["windspeedKmph"]
        return (f"🌤 Погода в {city}:\n\n🌡 {temp}°C (ощущается как {feels}°C)\n"
                f"☁️ {desc}\n💧 Влажность: {humid}%\n💨 Ветер: {wind} км/ч")
    except Exception:
        return f"😔 Не нашла погоду для '{city}'."

def send_voice(chat_id, text):
    if not VOICE_ENABLED: return False
    try:
        clean = re.sub(r'[*_`]', '', text)[:500]
        tts   = gTTS(text=clean, lang="ru", slow=False)
        tmp   = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tts.save(tmp.name)
        with open(tmp.name, "rb") as f:
            bot.send_voice(chat_id, f)
        os.unlink(tmp.name)
        return True
    except Exception:
        return False

def make_quiz_question(uid, topic):
    topic_names = {v: k for k, v in QUIZ_TOPICS.items()}
    topic_label = topic_names.get(topic, "случайная тема")
    prompt = (f"Придумай интересный вопрос для викторины на тему: {topic_label}.\n"
             f"Формат СТРОГО JSON:\n"
             f'{{"question": "текст вопроса", "answer": "правильный ответ", "hint": "подсказка", "fun_fact": "интересный факт"}}\n'
             f"Только JSON!")
    r    = requests.post(
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

def notify_admin(text):
    for admin_name in ADMIN_USERNAMES:
        all_uids = db.r.smembers("all_uids")
        for uid_str in all_uids:
            st = db.r.get(f"stats:{uid_str}") or {}
            if st.get("username", "").lower() == admin_name:
                try:
                    bot.send_message(int(uid_str), f"🔔 {text}")
                except Exception:
                    pass


# ══════════════════════════════════════════════
#  ДАННЫЕ
# ══════════════════════════════════════════════
ZODIAC_SIGNS = [
    "♈ Овен", "♉ Телец", "♊ Близнецы", "♋ Рак",
    "♌ Лев",  "♍ Дева",  "♎ Весы",     "♏ Скорпион",
    "♐ Стрелец", "♑ Козерог", "♒ Водолей", "♓ Рыбы"
]

QUIZ_TOPICS = {
    "🌍 География":  "geography",
    "🎬 Кино":       "movies",
    "🎵 Музыка":     "music",
    "🧪 Наука":      "science",
    "📚 Литература": "literature",
    "🏆 Спорт":      "sport",
    "🍕 Еда":        "food",
    "💄 Красота":    "beauty",
    "🐾 Животные":   "animals",
    "🌟 Случайное":  "random",
}

COMPLIMENTS = [
    "Просто напоминаю — ты замечательная! ✨",
    "Ты умница и красавица, не забывай об этом 💕",
    "С тобой всегда так интересно! 🌸",
    "Ты справишься со всем, я в тебя верю 💪",
    "Твоя улыбка — лучшее что есть на свете 🌺",
    "Ты особенная, и это не просто слова 🦋",
    "Восхищаюсь тобой каждый день! 🌟",
    "Ты создана для великих дел! 🚀",
    "Твоя энергия заряжает всех вокруг ⚡",
    "Ты лучшее что могло случиться с этим миром 🌍",
]

AFFIRMATIONS = [
    "Я достойна любви и счастья 💕",
    "Я справляюсь со всем что встречается на пути 💪",
    "Каждый день я становлюсь лучше ✨",
    "Я окружена людьми которые меня любят 🌸",
    "У меня есть всё необходимое для счастья 🌟",
    "Я верю в себя и свои силы 🦋",
    "Мои мечты реальны и достижимы 🎯",
    "Я притягиваю только хорошее 🌈",
    "Я сильная, умная и красивая 👑",
    "Сегодня будет отличный день ☀️",
]

MEDITATIONS = [
    {"name": "🌬 Дыхание 4-7-8", "text":
        "Снимает тревогу за 2 минуты:\n\n1️⃣ Вдох — 4 секунды\n2️⃣ Задержка — 7 секунд\n3️⃣ Выдох — 8 секунд\n\nПовтори 4 раза 🌿"},
    {"name": "🌊 Сканирование тела", "text":
        "Закрой глаза и расслабь:\n\n👣 Ступни → 🦵 Ноги → 🫁 Живот → 💪 Руки → 😌 Лицо\n\nПобудь так 5 минут 🌸"},
    {"name": "☀️ Утренняя настройка", "text":
        "Прямо сейчас:\n\n✨ 3 вещи за которые благодарна\n💫 Скажи: Сегодня я справлюсь со всем\n🌅 Представь идеальный день\n\nДелай каждое утро 🌟"},
    {"name": "🧘 Сброс стресса 5-4-3-2-1", "text":
        "Назови:\n\n5️⃣ вещей которые видишь\n4️⃣ которые потрогаешь\n3️⃣ звука которые слышишь\n2️⃣ запаха\n1️⃣ вкус\n\nВозвращает в момент здесь и сейчас 💙"},
    {"name": "💤 Техника для сна", "text":
        "Перед сном:\n\n🌙 Напряги всё тело на 5 сек\n😌 Резко расслабь\n🌬 Медленно дыши\n💭 Думай о чём-то приятном\n\nЗасыпаешь за 10 минут 😴"},
    {"name": "⚡ Быстрая перезарядка", "text":
        "Когда нет сил:\n\n💧 Выпей стакан воды\n🚶 Пройдись 5 минут\n☀️ Посмотри в окно\n😮‍💨 Сделай 3 глубоких вдоха\n\nЭнергия вернётся! ⚡"},
]

MOOD_EMOJIS = {
    "😊": "Хорошо", "🤩": "Отлично", "😔": "Грустно",
    "😤": "Злюсь",  "😰": "Тревожно", "😴": "Устала",
    "🥰": "Влюблена", "😐": "Нейтрально", "🤒": "Болею", "🤯": "Перегружена",
}

LANGUAGES = {
    "🇬🇧 Английский":  "English",
    "🇩🇪 Немецкий":    "German",
    "🇫🇷 Французский": "French",
    "🇪🇸 Испанский":   "Spanish",
    "🇨🇳 Китайский":   "Chinese",
    "🇯🇵 Японский":    "Japanese",
    "🇰🇷 Корейский":   "Korean",
    "🇹🇷 Турецкий":    "Turkish",
    "🇮🇹 Итальянский": "Italian",
    "🇦🇪 Арабский":    "Arabic",
}

PERSONALITY_TESTS = [
    "Тест: совместимость с парнем 💑",
    "Тест: какая ты подруга? 👯",
    "Тест: твой тип личности 🧠",
    "Тест: твоя суперсила ⚡",
    "Тест: в каком городе тебе жить? 🌆",
    "Тест: какой ты знак зодиака по характеру? ✨",
    "Тест: твой стиль жизни 💅",
    "Тест: насколько ты стрессоустойчива? 🧘",
]


# ══════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════
def main_menu_keyboard(username=""):
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
        types.InlineKeyboardButton("🌤 Погода",          callback_data="btn_weather"),
        types.InlineKeyboardButton("🍽 Рецепт",          callback_data="btn_recipe"),
        types.InlineKeyboardButton("🧘 Медитация",       callback_data="btn_meditation"),
        types.InlineKeyboardButton("📊 Настроение",      callback_data="btn_mood"),
        types.InlineKeyboardButton("🌍 Переводчик",      callback_data="btn_translate"),
        types.InlineKeyboardButton("🗓 Планировщик",     callback_data="btn_planner"),
        types.InlineKeyboardButton("🎭 Тесты на тебя",   callback_data="btn_test"),
        types.InlineKeyboardButton("🤣 Шутка дня",       callback_data="btn_joke"),
        types.InlineKeyboardButton("🌟 Факт дня",        callback_data="btn_fact"),
        types.InlineKeyboardButton("💪 Мотивация",       callback_data="btn_motivation"),
        types.InlineKeyboardButton("✨ Комплимент",      callback_data="btn_compliment"),
        types.InlineKeyboardButton("🌟 Аффирмация",      callback_data="btn_affirmation"),
        types.InlineKeyboardButton("📸 Анализ фото",     callback_data="btn_photo_hint"),
        types.InlineKeyboardButton("⏰ Напоминания",     callback_data="btn_reminders"),
        types.InlineKeyboardButton("📓 Дневник",         callback_data="btn_notes"),
        types.InlineKeyboardButton("🔗 Пригласить друга",callback_data="btn_referral"),
        types.InlineKeyboardButton("📱 Мой аккаунт",    callback_data="btn_account"),
        types.InlineKeyboardButton("🔄 Новый диалог",    callback_data="btn_new"),
        types.InlineKeyboardButton("ℹ️ Помощь",          callback_data="btn_help"),
        types.InlineKeyboardButton("👨‍💻 Разработчик",     url="https://t.me/tronqx"),
    )
    if is_admin(username):
        kb.add(types.InlineKeyboardButton("👑 Админ-панель", callback_data="adm_panel"))
    return kb

def after_message_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Меню",          callback_data="btn_menu"),
        types.InlineKeyboardButton("🎵 Голос",         callback_data="btn_voice_last"),
        types.InlineKeyboardButton("🧠 Объясни проще", callback_data="btn_explain_simple"),
        types.InlineKeyboardButton("📓 В дневник",     callback_data="btn_save_note"),
        types.InlineKeyboardButton("🔄 Новый диалог",  callback_data="btn_new"),
    )
    return kb

def access_denied_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🎁 Пробный период (3 дня бесплатно)", callback_data="btn_trial"),
        types.InlineKeyboardButton("⭐ Оплатить Stars",                    callback_data="btn_pay_stars"),
        types.InlineKeyboardButton("💳 Оплатить через @tronqx",           url=PAYMENT_LINK),
    )
    return kb

def admin_menu_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📊 Аналитика",         callback_data="adm_stats"),
        types.InlineKeyboardButton("👥 Все пользователи",  callback_data="adm_users"),
        types.InlineKeyboardButton("➕ Выдать бессрочно",  callback_data="adm_grant"),
        types.InlineKeyboardButton("🕐 Выдать на время",   callback_data="adm_grant_time"),
        types.InlineKeyboardButton("❌ Отозвать",          callback_data="adm_revoke"),
        types.InlineKeyboardButton("🚫 Заблокировать",     callback_data="adm_block"),
        types.InlineKeyboardButton("✅ Разблокировать",    callback_data="adm_unblock"),
        types.InlineKeyboardButton("📢 Рассылка",          callback_data="adm_broadcast"),
        types.InlineKeyboardButton("📤 Экспорт данных",    callback_data="adm_export"),
        types.InlineKeyboardButton("🔙 Главное меню",      callback_data="btn_menu"),
    )
    return kb

def admin_time_keyboard(target_uid=""):
    kb     = types.InlineKeyboardMarkup(row_width=3)
    prefix = f"adm_time_{target_uid}_" if target_uid else "adm_time_"
    kb.add(
        types.InlineKeyboardButton("1 день",   callback_data=f"{prefix}1"),
        types.InlineKeyboardButton("3 дня",    callback_data=f"{prefix}3"),
        types.InlineKeyboardButton("7 дней",   callback_data=f"{prefix}7"),
        types.InlineKeyboardButton("14 дней",  callback_data=f"{prefix}14"),
        types.InlineKeyboardButton("30 дней",  callback_data=f"{prefix}30"),
        types.InlineKeyboardButton("90 дней",  callback_data=f"{prefix}90"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="adm_back"),
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
    kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
    return kb

def meditation_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, m in enumerate(MEDITATIONS):
        kb.add(types.InlineKeyboardButton(m["name"], callback_data=f"med_{i}"))
    kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
    return kb

def translate_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    for name, val in LANGUAGES.items():
        kb.add(types.InlineKeyboardButton(name, callback_data=f"translate_{val}"))
    kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
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

def reminders_keyboard(uid):
    kb        = types.InlineKeyboardMarkup(row_width=1)
    reminders = db.get_reminders(uid)
    for i, r in enumerate(reminders):
        daily = "🔁" if r.get("daily") else "1️⃣"
        kb.add(types.InlineKeyboardButton(
            f"{daily} {r['time']} — {r['text'][:30]}",
            callback_data=f"rem_del_{i}"
        ))
    kb.add(
        types.InlineKeyboardButton("➕ Добавить напоминание", callback_data="rem_add"),
        types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"),
    )
    return kb


# ══════════════════════════════════════════════
#  ПРОВЕРКА ДОСТУПА
# ══════════════════════════════════════════════
def check_access(msg):
    uid      = msg.from_user.id
    username = msg.from_user.username or ""
    if is_vip(username): return True
    if db.is_blocked(uid):
        bot.reply_to(msg, "🚫 Ты заблокирована в боте.")
        return False
    if db.has_access(uid, username): return True
    bot.reply_to(msg,
        "🔒 Доступ к боту платный\n\n"
        f"⭐ Стоимость: {PRICE_STARS} Telegram Stars\n"
        f"💳 Или {PRICE_RUB} через @tronqx\n\n"
        "🎁 Или активируй пробный период — 3 дня бесплатно!",
        reply_markup=access_denied_kb())
    return False

def check_access_cb(call):
    uid      = call.from_user.id
    username = call.from_user.username or ""
    if is_vip(username): return True
    if db.is_blocked(uid):
        bot.answer_callback_query(call.id, "🚫 Заблокирована!")
        return False
    if db.has_access(uid, username): return True
    bot.answer_callback_query(call.id, "🔒 Нет доступа!")
    bot.send_message(uid,
        "🔒 Для использования нужна подписка!\n\n"
        f"⭐ {PRICE_STARS} Telegram Stars или {PRICE_RUB}",
        reply_markup=access_denied_kb())
    return False

def check_and_count(msg):
    uid      = msg.from_user.id
    username = msg.from_user.username or ""
    if not check_access(msg): return False
    ok, count, limit = check_daily_limit(uid, username)
    if not ok:
        user = db.get_user(uid)
        plan = user.get("plan", "") if user else "trial"
        if plan == "trial":
            bot.reply_to(msg,
                f"⚠️ Лимит пробного периода: {limit} сообщений/день\n\n"
                "Активируй полную подписку для безлимита! 🌸",
                reply_markup=access_denied_kb())
        else:
            bot.reply_to(msg, f"⚠️ Дневной лимит {limit} сообщений исчерпан. Приходи завтра!")
        return False
    db.count_message(uid)
    return True


# ══════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ОБРАБОТЧИКИ
# ══════════════════════════════════════════════
def _generate_and_send(chat_id, uid, prompt, reply_to_msg=None):
    bot.send_chat_action(chat_id, "upload_photo")
    wait_msg = bot.send_message(chat_id, "🎨 Рисую для тебя... Подожди ✨")
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
                bot.reply_to(reply_to_msg, f"🎨 Готово! Запрос: {prompt}")
            bot.send_photo(chat_id, img_data, reply_markup=kb)
        else:
            bot.send_message(chat_id, "😔 Не смогла нарисовать. Попробуй другое описание.")
    except Exception:
        try: bot.delete_message(chat_id, wait_msg.message_id)
        except: pass
        bot.send_message(chat_id, "😔 Ошибка при генерации. Попробуй позже.")

def _do_summarize(chat_id, uid, text, reply_to_msg=None):
    bot.send_chat_action(chat_id, "typing")
    try:
        prompt = (f"Сделай краткий пересказ текста. "
                 f"Выдели главную мысль, ключевые факты, вывод. "
                 f"Пиши просто и понятно:\n\n{text}")
        answer = ask_ai(uid, prompt, custom_system="Ты эксперт по анализу текста. Пиши кратко и понятно.")
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
        bot.send_message(chat_id, "😔 Не смогла пересказать.")


# ══════════════════════════════════════════════
#  КОМАНДЫ
# ══════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid      = msg.from_user.id
    username = msg.from_user.username or ""
    name     = msg.from_user.first_name or USER_NAME

    is_new = db.register_user(uid, username, name)
    histories[uid] = []
    modes[uid]     = "normal"

    parts     = msg.text.split()
    ref_bonus = ""
    if len(parts) > 1 and parts[1].startswith("ref"):
        owner = db.apply_referral(uid, parts[1])
        if owner:
            ref_bonus = "\n🎁 Реферальный бонус применён! +3 дня к пробному периоду"
            try:
                bot.send_message(owner,
                    "🎉 По твоей ссылке зарегистрировался новый пользователь!\n"
                    "💫 Тебе начислено +7 дней к подписке!")
            except Exception:
                pass

    if is_new:
        log_event(f"New user: {uid} @{username} {name}")
        notify_admin(f"👤 Новый пользователь!\nID: {uid}\n@{username}\nИмя: {name}")

    greeting_text = (
        f"{get_greeting()}, {name}! 🌸\n\n"
        f"Я {BOT_NAME} — твоя AI-подруга 👑{ref_bonus}\n\n"
        f"🎨 Рисую картинки\n"
        f"🧠 Викторина и тесты\n"
        f"🎤 Расшифровка голосовых\n"
        f"📖 Пересказ и перевод\n"
        f"🌤 Погода и курс валют\n"
        f"📸 Решаю задачи с фото\n"
        f"⏰ Напоминания и дневник\n"
        f"🌙 Гороскоп, 💌 Письма, 💄 Уход\n\n"
        f"Выбери с чего начнём 👇"
    )
    bot.reply_to(msg, greeting_text, reply_markup=main_menu_keyboard(username))

@bot.message_handler(commands=["menu"])
def cmd_menu(msg):
    db.register_user(msg.from_user.id, msg.from_user.username or "", msg.from_user.first_name or "")
    username = msg.from_user.username or ""
    bot.reply_to(msg,
        f"Главное меню 🌸\nРежим: {mode_name(msg.from_user.id)}",
        reply_markup=main_menu_keyboard(username))

@bot.message_handler(commands=["new"])
def cmd_new(msg):
    histories[msg.from_user.id] = []
    bot.reply_to(msg, "🔄 Начнём с чистого листа! 🌸")

@bot.message_handler(commands=["myid"])
def cmd_myid(msg):
    bot.reply_to(msg, f"Твой ID: `{msg.from_user.id}`", parse_mode="Markdown")

@bot.message_handler(commands=["ref"])
def cmd_ref(msg):
    uid      = msg.from_user.id
    username = msg.from_user.username or ""
    if not check_access(msg): return
    code     = db.get_ref_code(uid)
    ref_data = db.r.get(f"referral:{uid}") or {}
    invited  = len(ref_data.get("invited", []))
    bonus    = ref_data.get("bonus_days", 0)
    link     = f"https://t.me/{bot.get_me().username}?start={code}"
    bot.reply_to(msg,
        f"🔗 Реферальная программа\n\n"
        f"Приглашай подруг — получай бонусные дни!\n\n"
        f"✅ За каждого друга: +7 дней к подписке\n"
        f"✅ Другу: +3 дня к пробному периоду\n\n"
        f"Твоя ссылка:\n{link}\n\n"
        f"📊 Приглашено: {invited}\n"
        f"🎁 Бонусных дней заработано: {bonus}")

@bot.message_handler(commands=["remind"])
def cmd_remind(msg):
    uid = msg.from_user.id
    if not check_access(msg): return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(msg,
            "⏰ Напоминания:\n\n"
            "/remind 18:00 выпить воду\n"
            "/remind 09:00 утренняя зарядка\n\n"
            "Добавь 'каждый день' в конец для ежедневного:\n"
            "/remind 08:00 доброе утро каждый день")
        return
    time_str = parts[1]
    text     = parts[2]
    daily    = text.endswith("каждый день")
    if daily:
        text = text[:-len("каждый день")].strip()
    if not re.match(r"^\d{2}:\d{2}$", time_str):
        bot.reply_to(msg, "❌ Неверный формат времени. Используй ЧЧ:ММ (например 18:00)")
        return
    db.add_reminder(uid, text, time_str, daily=daily)
    d = " (каждый день 🔁)" if daily else ""
    bot.reply_to(msg, f"✅ Напоминание установлено!\n\n⏰ {time_str}{d}\n📝 {text}")

@bot.message_handler(commands=["weather"])
def cmd_weather(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "🌤 Напиши город:\n/weather Москва")
        return
    bot.send_chat_action(msg.chat.id, "typing")
    bot.reply_to(msg, get_weather(parts[1]))

@bot.message_handler(commands=["summarize"])
def cmd_summarize(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "📖 Напиши текст:\n/summarize [текст]")
        return
    if not check_access(msg): return
    _do_summarize(msg.chat.id, msg.from_user.id, parts[1], msg)

@bot.message_handler(commands=["admin"])
def cmd_admin(msg):
    username = (msg.from_user.username or "").lower()
    if not is_admin(username):
        bot.reply_to(msg, "❌ Нет доступа!")
        return
    a = db.get_analytics()
    bot.reply_to(msg,
        f"👑 Админ-панель\n\n"
        f"👥 Всего пользователей: {a['total_users']}\n"
        f"💎 Платных: {a['paid_users']}\n"
        f"🚫 Заблокировано: {a['blocked']}\n"
        f"📊 DAU сегодня: {a['dau_today']}\n"
        f"📊 DAU вчера: {a['dau_yest']}\n"
        f"✉️ Сообщений всего: {a['total_msgs']}\n"
        f"🆕 Новых за 7 дней: {a['new_week']}\n\n"
        f"Выбери действие:",
        reply_markup=admin_menu_keyboard())

@bot.message_handler(commands=["grant"])
def cmd_grant(msg):
    if not is_admin(msg.from_user.username or ""):
        bot.reply_to(msg, "❌ Нет прав!"); return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "Использование: /grant [user_id]"); return
    try:
        target = int(parts[1])
        db.set_user(target, None, plan="forever")
        db.unblock(target)
        bot.reply_to(msg, f"✅ Бессрочный доступ выдан {target}")
        try: bot.send_message(target, "🎉 Тебе выдан бессрочный доступ!\n\nНажми /start 🌸")
        except: pass
    except ValueError:
        bot.reply_to(msg, "❌ Неверный ID!")

@bot.message_handler(commands=["revoke"])
def cmd_revoke(msg):
    if not is_admin(msg.from_user.username or ""):
        bot.reply_to(msg, "❌ Нет прав!"); return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "Использование: /revoke [user_id]"); return
    try:
        target = int(parts[1])
        db.remove_user(target)
        bot.reply_to(msg, f"✅ Доступ отозван у {target}")
    except ValueError:
        bot.reply_to(msg, "❌ Неверный ID!")


# ══════════════════════════════════════════════
#  ГОЛОСОВЫЕ
# ══════════════════════════════════════════════
@bot.message_handler(content_types=["voice"])
def handle_voice(msg):
    uid = msg.from_user.id
    if not check_and_count(msg): return
    bot.send_chat_action(msg.chat.id, "typing")
    wait = bot.reply_to(msg, "🎤 Слушаю и расшифровываю...")
    try:
        file_info = bot.get_file(msg.voice.file_id)
        file_url  = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        audio     = requests.get(file_url, timeout=20).content
        text      = transcribe_voice(audio)
        bot.delete_message(uid, wait.message_id)
        bot.send_message(uid, f"🎤 Ты сказала:\n{text}")

        m = modes.get(uid, "normal")
        if m == "note_voice_mode":
            modes[uid] = "normal"
            db.add_note(uid, text)
            bot.send_message(uid, f"📓 Заметка сохранена!\n\n{text}",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("📓 Дневник", callback_data="btn_notes"),
                    types.InlineKeyboardButton("📋 Меню",   callback_data="btn_menu"),
                ))
            return

        bot.send_chat_action(uid, "typing")
        answer = ask_ai(uid, text)
        last_answer[uid] = answer
        bot.send_message(uid, answer, reply_markup=after_message_kb())
    except Exception:
        try: bot.delete_message(uid, wait.message_id)
        except: pass
        bot.reply_to(msg, "😔 Не смогла расшифровать.")


# ══════════════════════════════════════════════
#  ФОТО
# ══════════════════════════════════════════════
@bot.message_handler(content_types=["photo"])
def handle_photo(msg):
    uid = msg.from_user.id
    if not check_and_count(msg): return
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
        bot.reply_to(msg, "😔 Не смогла обработать фото.")


# ══════════════════════════════════════════════
#  ПЛАТЕЖИ (Telegram Stars)
# ══════════════════════════════════════════════
@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(query):
    bot.answer_pre_checkout_query(query.id, ok=True)

@bot.message_handler(content_types=["successful_payment"])
def successful_payment(msg):
    uid      = msg.from_user.id
    username = msg.from_user.username or ""
    stars    = msg.successful_payment.total_amount

    if stars >= 500:
        exp  = None
        plan = "forever"
        days_text = "навсегда"
    elif stars >= 200:
        exp  = datetime.now() + timedelta(days=90)
        plan = "90days"
        days_text = "90 дней"
    else:
        exp  = datetime.now() + timedelta(days=30)
        plan = "30days"
        days_text = "30 дней"

    db.set_user(uid, exp, plan=plan)
    db.unblock(uid)
    bot.send_message(uid,
        f"🎉 Оплата прошла успешно!\n\n"
        f"⭐ Списано: {stars} Stars\n"
        f"✅ Подписка активна: {days_text}\n\n"
        f"Все функции открыты! Нажми /start 🌸")
    notify_admin(
        f"💳 Новая оплата!\nID: {uid} @{username}\nStars: {stars}\nПлан: {plan}")
    log_event(f"Payment: uid={uid} stars={stars} plan={plan}")


# ══════════════════════════════════════════════
#  CALLBACKS
# ══════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    uid      = call.from_user.id
    data     = call.data
    username = call.from_user.username or ""

    free_callbacks = {
        "btn_menu", "btn_help", "btn_trial",
        "btn_pay_stars", "pay_stars_30", "pay_stars_90", "pay_stars_forever",
        "adm_panel", "adm_back", "adm_stats", "adm_users",
        "adm_grant", "adm_grant_time", "adm_revoke", "adm_block",
        "adm_unblock", "adm_broadcast", "adm_export", "btn_account"
    }

    if data not in free_callbacks and not data.startswith("adm_") and not check_access_cb(call):
        return

    # ── ПРОБНЫЙ ПЕРИОД ──
    if data == "btn_trial":
        bot.answer_callback_query(call.id)
        user = db.get_user(uid)
        if user:
            bot.send_message(uid, f"У тебя уже есть подписка!\nСтатус: {db.sub_status(uid)}")
            return
        exp = datetime.now() + timedelta(days=TRIAL_DAYS)
        db.set_user(uid, exp, plan="trial")
        bot.send_message(uid,
            f"🎁 Пробный период активирован!\n\n"
            f"✅ {TRIAL_DAYS} дня бесплатно\n"
            f"📊 Лимит: {FREE_DAILY_LIMIT} сообщений/день\n"
            f"⏰ Действует до: {exp.strftime('%d.%m.%Y')}\n\n"
            f"Нажми /start чтобы начать! 🌸",
            reply_markup=main_menu_keyboard(username))
        notify_admin(f"🎁 Пробный период: {uid} @{username}")
        return

    # ── ОПЛАТА STARS ──
    if data == "btn_pay_stars":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(f"⭐ {PRICE_STARS} Stars — 30 дней", callback_data="pay_stars_30"),
            types.InlineKeyboardButton("⭐ 200 Stars — 90 дней",            callback_data="pay_stars_90"),
            types.InlineKeyboardButton("⭐ 500 Stars — Навсегда",           callback_data="pay_stars_forever"),
            types.InlineKeyboardButton("🔙 Назад",                          callback_data="btn_menu"),
        )
        bot.send_message(uid, "⭐ Выбери план:", reply_markup=kb)
        return

    if data.startswith("pay_stars_"):
        plan_key = data.replace("pay_stars_", "")
        plans = {
            "30":      (PRICE_STARS, "Подписка на 30 дней"),
            "90":      (200,         "Подписка на 90 дней"),
            "forever": (500,         "Бессрочная подписка"),
        }
        if plan_key not in plans:
            bot.answer_callback_query(call.id); return
        amount, title = plans[plan_key]
        bot.answer_callback_query(call.id)
        try:
            bot.send_invoice(
                uid,
                title=title,
                description=f"Доступ к боту {BOT_NAME} — {title}",
                payload=f"sub_{plan_key}",
                provider_token="",
                currency="XTR",
                prices=[types.LabeledPrice(label=title, amount=amount)],
            )
        except Exception as e:
            bot.send_message(uid, f"😔 Ошибка при создании счёта. Напиши @tronqx\n\n{e}")
        return

    # ── МОЙ АККАУНТ ──
    if data == "btn_account":
        bot.answer_callback_query(call.id)
        status    = db.sub_status(uid)
        daily     = db.get_daily_count(uid)
        stat      = db.r.get(f"stats:{uid}") or {}
        total_msg = stat.get("total_msgs", 0)
        joined    = stat.get("joined", "")[:10]
        ref_data  = db.r.get(f"referral:{uid}") or {}
        invited   = len(ref_data.get("invited", []))
        code      = db.get_ref_code(uid)
        ref_link  = f"https://t.me/{bot.get_me().username}?start={code}"
        bot.send_message(uid,
            f"📱 Мой аккаунт\n\n"
            f"🆔 ID: {uid}\n"
            f"📅 В боте с: {joined}\n"
            f"💎 Статус: {status}\n"
            f"✉️ Сообщений сегодня: {daily}\n"
            f"✉️ Всего сообщений: {total_msg}\n\n"
            f"🔗 Реф. ссылка:\n{ref_link}\n"
            f"👥 Приглашено: {invited}",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("💳 Продлить подписку", callback_data="btn_pay_stars"),
                types.InlineKeyboardButton("📋 Меню",              callback_data="btn_menu"),
            ))
        return

    # ── НАПОМИНАНИЯ ──
    if data == "btn_reminders":
        bot.answer_callback_query(call.id)
        reminders = db.get_reminders(uid)
        text = f"⏰ Твои напоминания ({len(reminders)}):\n\nНажми чтобы удалить 👇" if reminders else "⏰ Напоминаний нет.\n\nДобавь первое!"
        bot.send_message(uid, text, reply_markup=reminders_keyboard(uid))
        return

    if data == "rem_add":
        bot.answer_callback_query(call.id)
        modes[uid] = "rem_add_mode"
        bot.send_message(uid,
            "⏰ Напиши напоминание в формате:\n\n"
            "18:00 выпить воду\n"
            "09:00 утренняя зарядка\n\n"
            "Для ежедневного добавь 'каждый день':\n"
            "08:00 доброе утро каждый день")
        return

    if data.startswith("rem_del_"):
        idx = int(data.replace("rem_del_", ""))
        db.remove_reminder(uid, idx)
        bot.answer_callback_query(call.id, "✅ Удалено!")
        try:
            bot.edit_message_reply_markup(uid, call.message.message_id, reply_markup=reminders_keyboard(uid))
        except Exception:
            pass
        return

    # ── ДНЕВНИК ──
    if data == "btn_notes":
        bot.answer_callback_query(call.id)
        notes = db.get_notes(uid)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✍️ Написать заметку",  callback_data="note_add_text"),
            types.InlineKeyboardButton("🎤 Голосовая заметка", callback_data="note_add_voice"),
            types.InlineKeyboardButton("📋 Меню",              callback_data="btn_menu"),
        )
        if not notes:
            bot.send_message(uid, "📓 Дневник пустой\n\nДобавь первую заметку!", reply_markup=kb)
        else:
            last_5 = notes[-5:]
            lines  = [f"📅 {n['date'][:10]}\n{n['text'][:200]}\n" for n in reversed(last_5)]
            bot.send_message(uid, "📓 Последние заметки:\n\n" + "\n".join(lines), reply_markup=kb)
        return

    if data == "note_add_text":
        bot.answer_callback_query(call.id)
        modes[uid] = "note_add_mode"
        bot.send_message(uid, "✍️ Напиши заметку:")
        return

    if data == "note_add_voice":
        bot.answer_callback_query(call.id)
        modes[uid] = "note_voice_mode"
        bot.send_message(uid, "🎤 Запиши голосовое — сохраню как заметку!")
        return

    if data == "btn_save_note":
        text = last_answer.get(uid, "")
        if not text:
            bot.answer_callback_query(call.id, "Нет текста!")
            return
        db.add_note(uid, text[:500])
        bot.answer_callback_query(call.id, "📓 Сохранено в дневник!")
        return

    # ── РЕФЕРАЛЬНАЯ ПРОГРАММА ──
    if data == "btn_referral":
        bot.answer_callback_query(call.id)
        code     = db.get_ref_code(uid)
        ref_data = db.r.get(f"referral:{uid}") or {}
        invited  = len(ref_data.get("invited", []))
        bonus    = ref_data.get("bonus_days", 0)
        link     = f"https://t.me/{bot.get_me().username}?start={code}"
        bot.send_message(uid,
            f"🔗 Реферальная программа\n\n"
            f"За каждую подругу — +7 дней к подписке!\n"
            f"Подруга получает: +3 дня бесплатно\n\n"
            f"🔗 Твоя ссылка:\n{link}\n\n"
            f"👥 Приглашено: {invited} чел.\n"
            f"🎁 Заработано бонусов: {bonus} дней",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")
            ))
        return

    # ── АДМИН-ПАНЕЛЬ ──
    if data == "adm_panel":
        if not is_admin(username):
            bot.answer_callback_query(call.id, "❌ Нет доступа!"); return
        bot.answer_callback_query(call.id)
        a = db.get_analytics()
        bot.send_message(uid,
            f"👑 Админ-панель\n\n"
            f"👥 Пользователей: {a['total_users']}\n"
            f"💎 Платных: {a['paid_users']}\n"
            f"📊 DAU: {a['dau_today']}\n"
            f"✉️ Сообщений: {a['total_msgs']}\n"
            f"🆕 За 7 дней: {a['new_week']}",
            reply_markup=admin_menu_keyboard())
        return

    if data == "adm_back":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "👑 Админ-панель:", reply_markup=admin_menu_keyboard())
        return

    if data.startswith("adm_") and not is_admin(username):
        bot.answer_callback_query(call.id, "❌ Нет доступа!"); return

    if data == "adm_stats":
        bot.answer_callback_query(call.id)
        a   = db.get_analytics()
        top = "\n".join(
            f"  {i+1}. ID{uid_str}: {st.get('total_msgs',0)} сообщений (@{st.get('username','')})"
            for i, (uid_str, st) in enumerate(a["top_users"])
        )
        bot.send_message(uid,
            f"📊 Полная аналитика\n\n"
            f"👥 Всего пользователей: {a['total_users']}\n"
            f"💎 Платных/активных: {a['paid_users']}\n"
            f"🚫 Заблокировано: {a['blocked']}\n"
            f"📈 DAU сегодня: {a['dau_today']}\n"
            f"📈 DAU вчера: {a['dau_yest']}\n"
            f"✉️ Сообщений всего: {a['total_msgs']}\n"
            f"🆕 Новых за 7 дней: {a['new_week']}\n\n"
            f"🏆 Топ-5 активных:\n{top}",
            reply_markup=admin_menu_keyboard())

    elif data == "adm_users":
        bot.answer_callback_query(call.id)
        paid_uids = list(db.r.smembers("paid_uids"))
        if not paid_uids:
            bot.send_message(uid, "👥 Пользователей нет.", reply_markup=admin_menu_keyboard())
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for u in paid_uids[:15]:
            status    = db.sub_status(int(u))
            st        = db.r.get(f"stats:{u}") or {}
            uname     = st.get("username", "")
            uname_str = f"@{uname}" if uname else ""
            kb.add(types.InlineKeyboardButton(
                f"👤 {u} {uname_str} | {status}", callback_data=f"adm_user_{u}"))
        kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="adm_back"))
        bot.send_message(uid, f"👥 Пользователи ({len(paid_uids)}):", reply_markup=kb)

    elif data.startswith("adm_user_"):
        target = int(data.replace("adm_user_", ""))
        bot.answer_callback_query(call.id)
        status = db.sub_status(target)
        st     = db.r.get(f"stats:{target}") or {}
        uname  = st.get("username", "")
        total  = st.get("total_msgs", 0)
        joined = st.get("joined", "")[:10]
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("♾ Бессрочная", callback_data=f"adm_give_forever_{target}"),
            types.InlineKeyboardButton("🕐 На время",  callback_data=f"adm_give_time_{target}"),
            types.InlineKeyboardButton("❌ Отозвать",  callback_data=f"adm_do_revoke_{target}"),
            types.InlineKeyboardButton("🚫 Блок",      callback_data=f"adm_do_block_{target}"),
            types.InlineKeyboardButton("✅ Разблок",   callback_data=f"adm_do_unblock_{target}"),
            types.InlineKeyboardButton("🔙 Назад",     callback_data="adm_users"),
        )
        bot.send_message(uid,
            f"👤 Пользователь: {target}\n@{uname}\n"
            f"Статус: {status}\nСообщений: {total}\nРегистрация: {joined}",
            reply_markup=kb)

    elif data.startswith("adm_give_forever_"):
        target = int(data.replace("adm_give_forever_", ""))
        db.set_user(target, None, plan="forever")
        db.unblock(target)
        bot.answer_callback_query(call.id, "✅ Выдана бессрочная!")
        bot.send_message(uid, f"✅ Пользователю {target} выдана бессрочная подписка!")
        try: bot.send_message(target, "🎉 Тебе выдан бессрочный доступ!\n\nНажми /start 🌸")
        except: pass

    elif data.startswith("adm_give_time_"):
        target = int(data.replace("adm_give_time_", ""))
        bot.answer_callback_query(call.id)
        bot.send_message(uid, f"⏱ Выбери срок для {target}:", reply_markup=admin_time_keyboard(target))

    elif re.match(r"adm_time_\d+_\d+", data):
        parts  = data.split("_")
        target = int(parts[2])
        days   = int(parts[3])
        exp    = datetime.now() + timedelta(days=days)
        db.set_user(target, exp, plan=f"{days}days")
        db.unblock(target)
        bot.answer_callback_query(call.id, f"✅ {days} дней выдано!")
        bot.send_message(uid, f"✅ Пользователю {target} выдана подписка на {days} дней до {exp.strftime('%d.%m.%Y')}")
        try: bot.send_message(target, f"🎉 Тебе выдана подписка на {days} дней!\nДо {exp.strftime('%d.%m.%Y')}\n\nНажми /start 🌸")
        except: pass

    elif data.startswith("adm_do_revoke_"):
        target = int(data.replace("adm_do_revoke_", ""))
        db.remove_user(target)
        bot.answer_callback_query(call.id, "✅ Отозвано!")
        bot.send_message(uid, f"✅ Подписка у {target} отозвана.")

    elif data.startswith("adm_do_block_"):
        target = int(data.replace("adm_do_block_", ""))
        db.block(target)
        bot.answer_callback_query(call.id, "🚫 Заблокирован!")
        bot.send_message(uid, f"🚫 Пользователь {target} заблокирован.")

    elif data.startswith("adm_do_unblock_"):
        target = int(data.replace("adm_do_unblock_", ""))
        db.unblock(target)
        bot.answer_callback_query(call.id, "✅ Разблокирован!")
        bot.send_message(uid, f"✅ Пользователь {target} разблокирован.")

    elif data == "adm_grant":
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_grant_mode"
        bot.send_message(uid, "➕ Напиши ID пользователя для бессрочной подписки:")

    elif data == "adm_grant_time":
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_grant_time_mode"
        bot.send_message(uid, "🕐 Напиши ID пользователя для подписки на время:")

    elif data == "adm_revoke":
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_revoke_mode"
        bot.send_message(uid, "❌ Напиши ID пользователя чтобы отозвать подписку:")

    elif data == "adm_block":
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_block_mode"
        bot.send_message(uid, "🚫 Напиши ID пользователя для блокировки:")

    elif data == "adm_unblock":
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_unblock_mode"
        bot.send_message(uid, "✅ Напиши ID пользователя для разблокировки:")

    elif data == "adm_broadcast":
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_broadcast_mode"
        bot.send_message(uid, "📢 Напиши сообщение для рассылки всем пользователям:")

    elif data == "adm_export":
        bot.answer_callback_query(call.id, "Экспортирую...")
        try:
            d = db.data
            export = {
                "generated": datetime.now().isoformat(),
                "analytics": db.get_analytics(),
                "users":     d["paid_users"],
                "referrals": d["referrals"],
            }
            tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
            json.dump(export, tmp, ensure_ascii=False, indent=2, default=str)
            tmp.close()
            with open(tmp.name, "rb") as f:
                bot.send_document(uid, f, caption="📤 Экспорт данных бота")
            os.unlink(tmp.name)
        except Exception as e:
            bot.send_message(uid, f"😔 Ошибка экспорта: {e}")

    # ── Режимы ──
    elif data.startswith("mode_"):
        m = data.replace("mode_", "")
        modes[uid]     = m
        histories[uid] = []
        names = {"normal": "💬 Просто общаемся!", "study": "📚 Помогу с учёбой!",
                 "support": "🤗 Я здесь 💕",     "creative": "🎨 Придумаем что-нибудь! ✨"}
        bot.answer_callback_query(call.id, "Режим изменён!")
        bot.send_message(uid, f"{names.get(m)}\n\nПиши, я слушаю 🌸")

    elif data == "btn_menu":
        bot.answer_callback_query(call.id)
        bot.send_message(uid,
            f"Главное меню 🌸\nРежим: {mode_name(uid)}",
            reply_markup=main_menu_keyboard(username))

    elif data == "btn_new":
        histories[uid] = []
        bot.answer_callback_query(call.id, "Очищено!")
        bot.send_message(uid, "🔄 Начнём с чистого листа! 🌸")

    elif data == "btn_help":
        bot.answer_callback_query(call.id)
        bot.send_message(uid,
            "ℹ️ Как пользоваться Лией:\n\n"
            "💬 Просто напиши любое сообщение\n"
            "📸 Отправь фото с вопросом в подписи\n"
            "🎤 Запиши голосовое — расшифрую\n"
            "📋 Нажми меню для всех функций\n\n"
            "Команды:\n"
            "/start — перезапуск\n"
            "/menu — главное меню\n"
            "/new — новый диалог\n"
            "/remind 18:00 выпить воду — напоминание\n"
            "/ref — реферальная ссылка\n"
            "/weather [город] — погода\n"
            "/myid — твой Telegram ID",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")
            ))

    elif data == "btn_compliment":
        bot.answer_callback_query(call.id, "💕")
        bot.send_message(uid, random.choice(COMPLIMENTS))

    elif data == "btn_affirmation":
        bot.answer_callback_query(call.id, "🌟")
        bot.send_message(uid, f"🌟 Аффирмация дня:\n\n{random.choice(AFFIRMATIONS)}\n\nПовтори 3 раза 💕")

    elif data == "btn_joke":
        bot.answer_callback_query(call.id, "😂")
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, "Расскажи смешную короткую шутку на русском языке.",
                custom_system="Ты юморист. Рассказываешь смешные безобидные шутки.")
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("😂 Ещё шутку", callback_data="btn_joke"),
                types.InlineKeyboardButton("📋 Меню",      callback_data="btn_menu"),
            )
            bot.send_message(uid, answer, reply_markup=kb)
        except Exception:
            bot.send_message(uid, "😔 Не смогла придумать шутку!")

    elif data == "btn_fact":
        bot.answer_callback_query(call.id, "🌟")
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, "Расскажи один удивительный факт который мало кто знает.",
                custom_system="Ты рассказываешь удивительные факты. Коротко и интересно.")
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🌟 Ещё факт", callback_data="btn_fact"),
                types.InlineKeyboardButton("📋 Меню",     callback_data="btn_menu"),
            )
            bot.send_message(uid, f"🌟 Факт дня:\n\n{answer}", reply_markup=kb)
        except Exception:
            bot.send_message(uid, "😔 Ошибка!")

    elif data == "btn_motivation":
        bot.answer_callback_query(call.id, "💪")
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid,
                "Дай мощную мотивационную речь для девушки которой нужна поддержка. Искренне и с душой.",
                custom_system="Ты мотивационный коуч. Вдохновляешь и поддерживаешь.")
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("💪 Ещё мотивацию", callback_data="btn_motivation"),
                types.InlineKeyboardButton("📋 Меню",          callback_data="btn_menu"),
            )
            bot.send_message(uid, answer, reply_markup=kb)
        except Exception:
            bot.send_message(uid, "😔 Ошибка!")

    elif data == "btn_test":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=1)
        for i, t in enumerate(PERSONALITY_TESTS):
            kb.add(types.InlineKeyboardButton(t, callback_data=f"run_test_{i}"))
        kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid, "🎭 Выбери тест:", reply_markup=kb)

    elif data.startswith("run_test_"):
        idx       = int(data.replace("run_test_", ""))
        test_name = PERSONALITY_TESTS[idx]
        bot.answer_callback_query(call.id, "Запускаю...")
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid,
                f"Проведи тест: '{test_name}'. Задай 5 интересных вопросов по одному. Начни прямо сейчас.",
                custom_system="Ты ведёшь интересный тест. Задавай вопросы по одному и в конце дай результат.")
            last_answer[uid] = answer
            modes[uid] = "test_mode"
            bot.send_message(uid, answer, reply_markup=after_message_kb())
        except Exception:
            bot.send_message(uid, "😔 Ошибка!")

    elif data == "btn_currency":
        bot.answer_callback_query(call.id, "Загружаю...")
        bot.send_message(uid, get_currency())

    elif data == "btn_weather":
        bot.answer_callback_query(call.id)
        modes[uid] = "weather_mode"
        bot.send_message(uid, "🌤 Напиши название города:")

    elif data == "btn_translate":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "🌍 На какой язык перевести?", reply_markup=translate_keyboard())

    elif data.startswith("translate_"):
        lang = data.replace("translate_", "")
        bot.answer_callback_query(call.id)
        modes[uid] = f"translate_mode_{lang}"
        lang_name  = {v: k for k, v in LANGUAGES.items()}.get(lang, lang)
        bot.send_message(uid, f"✍️ Напиши текст для перевода на {lang_name}:")

    elif data == "btn_imagine":
        bot.answer_callback_query(call.id)
        modes[uid] = "imagine_mode"
        bot.send_message(uid,
            "🎨 Генератор картинок\n\nОпиши что нарисовать:\n\n"
            "• красивый закат над морем\n"
            "• уютная кофейня осенью\n"
            "• котик в шапке астронавта\n\n"
            "Пиши на русском — переведу сама! 🌸")

    elif data.startswith("imagine_again_"):
        prompt = data.replace("imagine_again_", "")
        bot.answer_callback_query(call.id, "Рисую...")
        _generate_and_send(call.message.chat.id, uid, prompt)

    elif data == "btn_quiz":
        bot.answer_callback_query(call.id)
        state  = quiz_state.get(uid, {})
        header = f"🏆 Счёт: {state.get('score',0)}/{state.get('total',0)}\n\n" if state.get("total") else ""
        bot.send_message(uid, f"{header}🧠 Викторина!\n\nВыбери тему:", reply_markup=quiz_topic_keyboard())

    elif data.startswith("quiz_start_"):
        topic = data.replace("quiz_start_", "")
        bot.answer_callback_query(call.id, "Готовлю вопрос...")
        bot.send_chat_action(uid, "typing")
        wait = bot.send_message(uid, "🎲 Придумываю вопрос...")
        try:
            q = make_quiz_question(uid, topic)
            if uid not in quiz_state: quiz_state[uid] = {"score": 0, "total": 0}
            quiz_state[uid]["current"] = q
            quiz_state[uid]["topic"]   = topic
            quiz_state[uid]["total"]  += 1
            bot.delete_message(uid, wait.message_id)
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("💡 Подсказка",      callback_data="quiz_hint"),
                types.InlineKeyboardButton("✅ Показать ответ", callback_data="quiz_answer"),
            )
            kb.add(types.InlineKeyboardButton("➡️ Другой вопрос", callback_data=f"quiz_start_{topic}"))
            kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
            score = quiz_state[uid]["score"]
            total = quiz_state[uid]["total"]
            bot.send_message(uid,
                f"🧠 Вопрос #{total} | Счёт: {score}/{total-1}\n\n{q['question']}\n\nНапиши ответ или нажми кнопку 👇",
                reply_markup=kb)
            modes[uid] = "quiz_answer_mode"
        except Exception:
            try: bot.delete_message(uid, wait.message_id)
            except: pass
            bot.send_message(uid, "😔 Не смогла придумать вопрос. Попробуй другую тему!")

    elif data == "quiz_hint":
        bot.answer_callback_query(call.id)
        q = quiz_state.get(uid, {}).get("current", {})
        bot.send_message(uid, f"💡 Подсказка:\n\n{q.get('hint', 'Подсказок нет 🤷')}")

    elif data == "quiz_answer":
        bot.answer_callback_query(call.id)
        q      = quiz_state.get(uid, {}).get("current", {})
        topic  = quiz_state.get(uid, {}).get("topic", "random")
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✅ Я знала!", callback_data="quiz_correct"),
            types.InlineKeyboardButton("❌ Не знала", callback_data="quiz_wrong"),
        )
        kb.add(types.InlineKeyboardButton("➡️ Следующий вопрос", callback_data=f"quiz_start_{topic}"))
        bot.send_message(uid,
            f"✅ Правильный ответ:\n\n{q.get('answer','?')}\n\n🌟 Факт:\n{q.get('fun_fact','')}",
            reply_markup=kb)
        modes[uid] = "normal"

    elif data in ("quiz_correct", "quiz_wrong"):
        if data == "quiz_correct":
            quiz_state.setdefault(uid, {})["score"] = quiz_state.get(uid, {}).get("score", 0) + 1
            bot.answer_callback_query(call.id, "🎉 Отлично!")
            bot.send_message(uid, f"🎉 Засчитано! Счёт: {quiz_state[uid]['score']}/{quiz_state[uid]['total']}")
        else:
            bot.answer_callback_query(call.id, "😔 Ничего!")
            bot.send_message(uid, f"💪 Теперь знаешь! Счёт: {quiz_state.get(uid,{}).get('score',0)}/{quiz_state.get(uid,{}).get('total',0)}")

    elif data == "btn_summarize":
        bot.answer_callback_query(call.id)
        modes[uid] = "summarize_mode"
        bot.send_message(uid, "📖 Пересказ текста\n\nОтправь любой текст — сделаю краткий пересказ! ✨")

    elif data == "btn_horoscope":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "🌙 Выбери знак зодиака:", reply_markup=zodiac_keyboard())

    elif data.startswith("zodiac_"):
        sign = data.replace("zodiac_", "")
        bot.answer_callback_query(call.id, "Читаю звёзды...")
        bot.send_chat_action(uid, "typing")
        try:
            today  = datetime.now().strftime("%d.%m.%Y")
            answer = ask_ai(uid,
                f"Составь гороскоп на {today} для знака {sign}. "
                f"Включи: общий прогноз, любовь, работа/учёба, здоровье, совет дня. "
                f"Пиши красиво с эмодзи, оптимистично.",
                custom_system="Ты астролог. Составляй красивые гороскопы.")
            last_answer[uid] = answer
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🔄 Другой знак", callback_data="btn_horoscope"),
                types.InlineKeyboardButton("📋 Меню",        callback_data="btn_menu"),
            )
            bot.send_message(uid, answer, reply_markup=kb)
        except Exception:
            bot.send_message(uid, "😔 Не смогла прочитать звёзды.")

    elif data == "btn_beauty":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("💡 Лайфхак дня",        callback_data="beauty_tip"),
            types.InlineKeyboardButton("🧴 Рутина ухода",       callback_data="beauty_routine"),
            types.InlineKeyboardButton("💇 Волосы",             callback_data="beauty_hair"),
            types.InlineKeyboardButton("💅 Ногти",              callback_data="beauty_nails"),
            types.InlineKeyboardButton("🏋️ Упражнения",         callback_data="beauty_fitness"),
            types.InlineKeyboardButton("🥗 Правильное питание", callback_data="beauty_nutrition"),
            types.InlineKeyboardButton("😴 Уход во сне",        callback_data="beauty_sleep"),
            types.InlineKeyboardButton("📋 Меню",               callback_data="btn_menu"),
        )
        bot.send_message(uid, "💄 Уход за собой\n\nЧто тебя интересует?", reply_markup=kb)

    elif data.startswith("beauty_"):
        sub = data.replace("beauty_", "")
        bot.answer_callback_query(call.id, "Загружаю...")
        bot.send_chat_action(uid, "typing")
        prompts = {
            "tip":       "Дай один крутой бьюти-лайфхак. Коротко и практично.",
            "routine":   "Составь простую утреннюю и вечернюю рутину ухода за лицом.",
            "hair":      "Дай советы по уходу за волосами дома. Маски, лайфхаки, ошибки.",
            "nails":     "Расскажи как делать маникюр дома пошагово.",
            "fitness":   "Предложи 5 упражнений для красивой фигуры дома без оборудования.",
            "nutrition": "Расскажи о правильном питании для красоты кожи и волос.",
            "sleep":     "Расскажи как ухаживать за кожей перед сном.",
        }
        try:
            answer = ask_ai(uid, prompts.get(sub, "Дай совет по уходу за собой."),
                custom_system="Ты эксперт по красоте. Давай практичные простые советы.")
            last_answer[uid] = answer
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("💄 Ещё советы", callback_data="btn_beauty"),
                types.InlineKeyboardButton("📋 Меню",       callback_data="btn_menu"),
            )
            bot.send_message(uid, answer, reply_markup=kb)
        except Exception:
            bot.send_message(uid, "😔 Ошибка.")

    elif data == "btn_song":
        bot.answer_callback_query(call.id)
        modes[uid] = "song_mode"
        bot.send_message(uid, "🎵 Угадай песню!\n\nОпиши песню — угадаю!\n\n• По словам\n• По описанию клипа\n• По настроению")

    elif data == "btn_love":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("💌 Письмо",         callback_data="love_letter"),
            types.InlineKeyboardButton("💐 Признание",      callback_data="love_confession"),
            types.InlineKeyboardButton("🌹 Доброе утро",    callback_data="love_morning"),
            types.InlineKeyboardButton("🌙 Спокойной ночи", callback_data="love_night"),
            types.InlineKeyboardButton("💔 Извинение",      callback_data="love_sorry"),
            types.InlineKeyboardButton("💝 Поздравление",   callback_data="love_congrats"),
            types.InlineKeyboardButton("📋 Меню",           callback_data="btn_menu"),
        )
        bot.send_message(uid, "💌 Что написать?", reply_markup=kb)

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
            "congrats":   "Напиши красивое поздравление с праздником для любимого.",
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
            bot.send_message(uid, "😔 Ошибка.")

    elif data == "btn_todo":
        bot.answer_callback_query(call.id)
        todos = todo_list.get(uid, [])
        done  = sum(1 for t in todos if t["done"])
        text  = f"📝 Список дел" + (f" ({done}/{len(todos)} ✅)" if todos else " — пустой")
        bot.send_message(uid, text, reply_markup=todo_keyboard(uid))

    elif data == "todo_add":
        bot.answer_callback_query(call.id)
        modes[uid] = "todo_add_mode"
        bot.send_message(uid, "📝 Что добавить в список?")

    elif data == "todo_clear":
        todo_list[uid] = []
        bot.answer_callback_query(call.id, "Список очищен!")
        bot.send_message(uid, "🗑 Список очищен!", reply_markup=todo_keyboard(uid))

    elif data.startswith("todo_toggle_"):
        idx   = int(data.replace("todo_toggle_", ""))
        todos = todo_list.get(uid, [])
        if idx < len(todos):
            todos[idx]["done"] = not todos[idx]["done"]
            bot.answer_callback_query(call.id, "✅" if todos[idx]["done"] else "⬜")
            try:
                bot.edit_message_reply_markup(uid, call.message.message_id, reply_markup=todo_keyboard(uid))
            except: pass

    elif data == "btn_planner":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🗓 План дня",        callback_data="planner_create"),
            types.InlineKeyboardButton("⏰ Утренняя рутина", callback_data="planner_morning"),
            types.InlineKeyboardButton("🌙 Вечерняя рутина", callback_data="planner_evening"),
            types.InlineKeyboardButton("📚 План для учёбы",  callback_data="planner_study"),
            types.InlineKeyboardButton("💪 План тренировок", callback_data="planner_workout"),
            types.InlineKeyboardButton("📋 Меню",            callback_data="btn_menu"),
        )
        bot.send_message(uid, "🗓 Планировщик\n\nЧто составим?", reply_markup=kb)

    elif data.startswith("planner_"):
        sub = data.replace("planner_", "")
        bot.answer_callback_query(call.id, "Составляю...")
        bot.send_chat_action(uid, "typing")
        prompts = {
            "create":  "Составь идеальный план дня для продуктивной девушки. По часам с 7:00 до 23:00.",
            "morning": "Составь идеальную утреннюю рутину на 1 час после пробуждения.",
            "evening": "Составь идеальную вечернюю рутину для расслабления и подготовки ко сну.",
            "study":   "Составь эффективный план для учёбы на день с перерывами.",
            "workout": "Составь план тренировок на неделю дома без оборудования.",
        }
        try:
            answer = ask_ai(uid, prompts.get(sub, "Составь план дня."),
                custom_system="Ты коуч по продуктивности. Составляй реалистичные планы.")
            last_answer[uid] = answer
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🗓 Другой план", callback_data="btn_planner"),
                types.InlineKeyboardButton("📋 Меню",        callback_data="btn_menu"),
            )
            bot.send_message(uid, answer, reply_markup=kb)
        except Exception:
            bot.send_message(uid, "😔 Ошибка!")

    elif data == "btn_recipe":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🥘 По продуктам",    callback_data="recipe_by_ingredients"),
            types.InlineKeyboardButton("⚡ Быстрый рецепт",  callback_data="recipe_quick"),
            types.InlineKeyboardButton("🥗 Полезный рецепт", callback_data="recipe_healthy"),
            types.InlineKeyboardButton("🎂 Десерт",          callback_data="recipe_dessert"),
            types.InlineKeyboardButton("🍳 Завтрак",         callback_data="recipe_breakfast"),
            types.InlineKeyboardButton("📋 Меню",            callback_data="btn_menu"),
        )
        bot.send_message(uid, "🍽 Рецепты\n\nЧто приготовим?", reply_markup=kb)

    elif data == "recipe_by_ingredients":
        bot.answer_callback_query(call.id)
        modes[uid] = "recipe_mode"
        bot.send_message(uid, "🥘 Напиши что есть в холодильнике:\n\nНапример: яйца, сыр, помидоры")

    elif data.startswith("recipe_"):
        sub = data.replace("recipe_", "")
        prompts_r = {
            "quick":     "Дай рецепт вкусного блюда которое готовится за 15 минут.",
            "healthy":   "Дай рецепт полезного и вкусного блюда для здорового питания.",
            "dessert":   "Дай рецепт простого десерта который можно приготовить дома.",
            "breakfast": "Дай рецепт вкусного питательного завтрака.",
        }
        if sub in prompts_r:
            bot.answer_callback_query(call.id, "Ищу рецепт...")
            bot.send_chat_action(uid, "typing")
            try:
                answer = ask_ai(uid, prompts_r[sub],
                    custom_system="Ты шеф-повар. Даёшь простые и вкусные рецепты.")
                last_answer[uid] = answer
                kb = types.InlineKeyboardMarkup(row_width=2)
                kb.add(
                    types.InlineKeyboardButton("🍽 Ещё рецепт", callback_data="btn_recipe"),
                    types.InlineKeyboardButton("📋 Меню",       callback_data="btn_menu"),
                )
                bot.send_message(uid, answer, reply_markup=kb)
            except Exception:
                bot.send_message(uid, "😔 Ошибка!")

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
        bot.send_message(uid, f"{med['name']}\n\n{med['text']}", reply_markup=kb)

    elif data == "btn_mood":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "Как ты себя чувствуешь? 🌈", reply_markup=mood_keyboard())

    elif data.startswith("mood_") and data != "mood_history":
        emoji = data.replace("mood_", "")
        now   = datetime.now().strftime("%d.%m %H:%M")
        if uid not in mood_log: mood_log[uid] = []
        mood_log[uid].append({"time": now, "mood": emoji, "name": MOOD_EMOJIS.get(emoji, "")})
        bot.answer_callback_query(call.id, f"Записала {emoji}")
        try:
            response = ask_ai(uid,
                f"Настроение пользователя: {emoji} {MOOD_EMOJIS.get(emoji)}. Отреагируй тепло.",
                custom_system="Ты заботливая подруга. Реагируй на настроение с теплом.")
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
            bot.send_message(uid, "📊 Последние записи:\n\n" + "\n".join(lines))

    elif data == "btn_explain_simple":
        text = last_answer.get(uid, "")
        if not text:
            bot.answer_callback_query(call.id, "Нет текста!"); return
        bot.answer_callback_query(call.id, "Объясняю проще...")
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid,
                f"Вот текст:\n{text}\n\nОбъясни это максимально просто — как подруга подруге. "
                f"Без сложных слов, коротко, с примерами из жизни.",
                custom_system="Объясняй очень просто, как подруга. Никаких сложных слов.")
            last_answer[uid] = answer
            bot.send_message(uid, answer, reply_markup=after_message_kb())
        except Exception:
            bot.send_message(uid, "😔 Ошибка!")

    elif data == "btn_voice_last":
        text = last_answer.get(uid, "")
        if not text:
            bot.answer_callback_query(call.id, "Нет текста!")
        elif not VOICE_ENABLED:
            bot.answer_callback_query(call.id, "Голос недоступен!")
        else:
            bot.answer_callback_query(call.id, "🎵 Озвучиваю...")
            bot.send_chat_action(uid, "record_voice")
            send_voice(uid, text)

    elif data == "btn_photo_hint":
        bot.answer_callback_query(call.id)
        bot.send_message(uid,
            "📸 Анализ фото:\n\n"
            "1. Нажми скрепку 📎\n2. Выбери фото\n"
            "3. В подписи напиши что нужно\n\n"
            "Без подписи — сама разберусь! 😊")


# ══════════════════════════════════════════════
#  ТЕКСТ (основной обработчик)
# ══════════════════════════════════════════════
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(msg):
    uid  = msg.from_user.id
    text = msg.text.strip()

    db.register_user(uid, msg.from_user.username or "", msg.from_user.first_name or "")

    if text.startswith("/start") or text.startswith("/menu") or text.startswith("/myid"):
        return

    current_mode = modes.get(uid, "normal")

    if not check_and_count(msg): return

    bot.send_chat_action(msg.chat.id, "typing")

    # ── Спецрежимы ──
    if current_mode == "imagine_mode":
        modes[uid] = "normal"
        _generate_and_send(msg.chat.id, uid, text, msg)
        return

    if current_mode == "note_add_mode":
        modes[uid] = "normal"
        db.add_note(uid, text)
        bot.reply_to(msg, "📓 Заметка сохранена!",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("📓 Дневник", callback_data="btn_notes"),
                types.InlineKeyboardButton("📋 Меню",   callback_data="btn_menu"),
            ))
        return

    if current_mode == "rem_add_mode":
        modes[uid] = "normal"
        parts_rem = text.split(maxsplit=1)
        if len(parts_rem) < 2:
            bot.reply_to(msg, "❌ Формат: ЧЧ:ММ текст напоминания\nНапример: 18:00 выпить воду")
            return
        time_str_rem = parts_rem[0]
        text_rem     = parts_rem[1]
        daily_rem    = text_rem.endswith("каждый день")
        if daily_rem: text_rem = text_rem[:-len("каждый день")].strip()
        if not re.match(r"^\d{2}:\d{2}$", time_str_rem):
            bot.reply_to(msg, "❌ Неверный формат времени. Используй ЧЧ:ММ")
            return
        db.add_reminder(uid, text_rem, time_str_rem, daily=daily_rem)
        d = " (каждый день 🔁)" if daily_rem else ""
        bot.reply_to(msg, f"✅ Напоминание установлено!\n\n⏰ {time_str_rem}{d}\n📝 {text_rem}",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⏰ Все напоминания", callback_data="btn_reminders")
            ))
        return

    # ── Админ-режимы ──
    if current_mode == "adm_grant_mode":
        modes[uid] = "normal"
        if not is_admin(msg.from_user.username or ""): return
        try:
            target = int(text)
            db.set_user(target, None, plan="forever")
            db.unblock(target)
            bot.reply_to(msg, f"✅ Бессрочная подписка выдана {target}", reply_markup=admin_menu_keyboard())
            try: bot.send_message(target, "🎉 Тебе выдан бессрочный доступ!\n\nНажми /start 🌸")
            except: pass
        except ValueError:
            bot.reply_to(msg, "❌ Неверный ID!", reply_markup=admin_menu_keyboard())
        return

    if current_mode == "adm_grant_time_mode":
        if not is_admin(msg.from_user.username or ""): return
        try:
            int(text)
            modes[uid] = f"adm_pick_time_{text}"
            bot.reply_to(msg, f"⏱ Выбери срок для {text}:", reply_markup=admin_time_keyboard(text))
        except ValueError:
            modes[uid] = "normal"
            bot.reply_to(msg, "❌ Неверный ID!", reply_markup=admin_menu_keyboard())
        return

    if current_mode == "adm_revoke_mode":
        modes[uid] = "normal"
        if not is_admin(msg.from_user.username or ""): return
        try:
            target = int(text)
            db.remove_user(target)
            bot.reply_to(msg, f"✅ Подписка у {target} отозвана.", reply_markup=admin_menu_keyboard())
        except ValueError:
            bot.reply_to(msg, "❌ Неверный ID!", reply_markup=admin_menu_keyboard())
        return

    if current_mode == "adm_block_mode":
        modes[uid] = "normal"
        if not is_admin(msg.from_user.username or ""): return
        try:
            target = int(text)
            db.block(target)
            bot.reply_to(msg, f"🚫 Пользователь {target} заблокирован.", reply_markup=admin_menu_keyboard())
            try: bot.send_message(target, "🚫 Твой доступ к боту заблокирован.")
            except: pass
        except ValueError:
            bot.reply_to(msg, "❌ Неверный ID!", reply_markup=admin_menu_keyboard())
        return

    if current_mode == "adm_unblock_mode":
        modes[uid] = "normal"
        if not is_admin(msg.from_user.username or ""): return
        try:
            target = int(text)
            db.unblock(target)
            bot.reply_to(msg, f"✅ Пользователь {target} разблокирован.", reply_markup=admin_menu_keyboard())
            try: bot.send_message(target, "✅ Доступ восстановлен! Нажми /start 🌸")
            except: pass
        except ValueError:
            bot.reply_to(msg, "❌ Неверный ID!", reply_markup=admin_menu_keyboard())
        return

    if current_mode == "adm_broadcast_mode":
        modes[uid] = "normal"
        if not is_admin(msg.from_user.username or ""): return
        all_ids    = db.r.smembers("paid_uids")
        sent, failed = 0, 0
        for target_str in all_ids:
            try:
                bot.send_message(int(target_str), f"📢 Сообщение от администратора:\n\n{text}")
                sent += 1
            except:
                failed += 1
        bot.reply_to(msg,
            f"📢 Рассылка завершена!\n✅ Доставлено: {sent}\n❌ Ошибок: {failed}",
            reply_markup=admin_menu_keyboard())
        return

    # ── Обычные режимы ──
    if current_mode == "summarize_mode":
        modes[uid] = "normal"
        _do_summarize(msg.chat.id, uid, text, msg)
        return

    if current_mode == "weather_mode":
        modes[uid] = "normal"
        bot.reply_to(msg, get_weather(text))
        return

    if current_mode.startswith("translate_mode_"):
        lang = current_mode.replace("translate_mode_", "")
        modes[uid] = "normal"
        try:
            answer = ask_ai(uid, f"Переведи на {lang}: {text}",
                custom_system=f"Ты переводчик. Переводи точно и естественно на {lang}. Только перевод.")
            last_answer[uid] = answer
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🌍 Ещё перевод", callback_data="btn_translate"),
                types.InlineKeyboardButton("📋 Меню",        callback_data="btn_menu"),
            )
            bot.reply_to(msg, answer, reply_markup=kb)
        except Exception:
            bot.reply_to(msg, "😔 Ошибка перевода!")
        return

    if current_mode == "quiz_answer_mode":
        q        = quiz_state.get(uid, {}).get("current", {})
        correct  = q.get("answer", "").lower().strip()
        user_ans = text.lower().strip()
        topic    = quiz_state.get(uid, {}).get("topic", "random")
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
            bot.reply_to(msg, f"🎉 Правильно! Счёт: {score}/{total}\n\n🌟 {q.get('fun_fact','')}", reply_markup=kb)
        else:
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("✅ Показать ответ", callback_data="quiz_answer"),
                types.InlineKeyboardButton("➡️ Следующий",     callback_data=f"quiz_start_{topic}"),
            )
            bot.reply_to(msg, "🤔 Не совсем... Попробуй ещё или посмотри ответ 👇", reply_markup=kb)
            modes[uid] = "quiz_answer_mode"
        return

    if current_mode == "song_mode":
        modes[uid] = "normal"
        text = f"Пользователь описывает песню: '{text}'. Угадай название и исполнителя."

    if current_mode == "recipe_mode":
        modes[uid] = "normal"
        text = f"У пользователя есть продукты: {text}. Придумай вкусный рецепт с пошаговой инструкцией."

    if current_mode == "todo_add_mode":
        modes[uid] = "normal"
        if uid not in todo_list: todo_list[uid] = []
        todo_list[uid].append({"text": text, "done": False})
        bot.reply_to(msg, f"✅ Добавила: {text}",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("➕ Ещё",     callback_data="todo_add"),
                types.InlineKeyboardButton("📝 Список", callback_data="btn_todo"),
            ))
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
voice_status = "✅ ГОЛОС ВКЛ" if VOICE_ENABLED else "❌ ГОЛОС ВЫКЛ"
print(f"👑 Бот '{BOT_NAME}' ULTRA LEGENDARY v2.0 Redis запущен!")
print(f"🗄 Хранилище: Upstash Redis (вечное)")
print(f"🎁 Пробный период: {TRIAL_DAYS} дня")
print(f"⭐ Стоимость: {PRICE_STARS} Telegram Stars")
print(f"⏰ Планировщик напоминаний: активен")
print(f"🎵 {voice_status}")
log_event("Bot started v2.0 Redis")
bot.infinity_polling(timeout=30, long_polling_timeout=20)
