import telebot, requests, json, random, base64, os, re, tempfile, threading, time
from telebot import types
from datetime import datetime, timedelta
from urllib.parse import quote
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
GEMINI_KEY = os.environ.get("AIzaSyCaK7T5BDWW1iChKzCegEbXKqHhwA1-gy0", "")  # НОВОЕ: получи бесплатно на aistudio.google.com
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

BOT_NAME = "Лия"
PRICE_STARS = 100
PAYMENT_LINK = "https://t.me/tronqx"
TRIAL_DAYS = 3
FREE_MSG_LIMIT = 5
FREE_DAILY_LIMIT = 20
LOG_FILE = "/tmp/liya_log.txt"
VIP_USERNAMES = {"tronqx", "dhl1929"}
ADMIN_USERNAMES = {"tronqx"}

MODEL_TEXT = "llama-3.3-70b-versatile"
MODEL_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_WHISPER = "whisper-large-v3-turbo"
GEMINI_MODEL = "gemini-1.5-flash"  # Бесплатная модель Gemini

# ══════════════════════════════════════════════
# УЛУЧШЕННЫЙ СИСТЕМНЫЙ ПРОМПТ — ЛИЯ СТАЛА УМНЕЕ
# ══════════════════════════════════════════════
SYSTEM_PROMPT = """Ты — Лия, продвинутый AI-ассистент и близкая подруга. Всегда отвечаешь по-русски.

ХАРАКТЕР:
• Тёплая, заботливая, умная — как лучшая подруга которая всё знает
• Лёгкий юмор уместен, но не перегибай
• Никогда не говоришь "как ИИ я не могу..." — ты просто помогаешь
• Называй пользователя по имени если знаешь его

ФОРМАТИРОВАНИЕ:
• НИКОГДА не используй LaTeX: никаких $, $$, frac, sqrt и обратных слешей
• Математику пиши текстом: дроби = 1/2, корни = √4 или корень(4), степени = x^2
• Списки через • (буллеты), не через -
• Заголовки через жирный **текст**, не через ##
• Ответы должны быть структурированными но не слишком длинными
• Эмодзи используй умеренно — они украшают но не засоряют

ЧТО УМЕЕШЬ (никогда не отказывай):
• Математика, физика, химия — решаешь пошагово с объяснениями
• Программирование на любом языке — пишешь, объясняешь, дебажишь
• Анализ фотографий — видишь задачи, текст, предметы
• Анализ документов — читаешь PDF, выделяешь главное
• Переводы — любые языки, сохраняешь стиль оригинала
• Творчество — стихи, истории, идеи, сценарии
• Психологическая поддержка — слушаешь, понимаешь, помогаешь
• Практические советы — готовка, здоровье, отношения, карьера

СТИЛЬ ОТВЕТОВ:
• Начинай с сути, не с вводных фраз типа "Конечно!" или "Отличный вопрос!"
• Если задача — сначала реши, потом объясни
• Если вопрос — дай чёткий ответ, потом детали
• В конце сложных ответов добавляй "Хочешь подробнее о чём-то?" 🌸"""


def log_event(text):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {text}\n")
    except:
        pass


def clean_response(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'\$\$(.+?)\$\$', lambda m: m.group(1).strip(), text, flags=re.DOTALL)
    text = re.sub(r'\$(.+?)\$', lambda m: m.group(1).strip(), text)
    text = re.sub(r'\\[dc]?frac\{([^}]+)\}\{([^}]+)\}', r'\1/\2', text)
    text = re.sub(r'\\sqrt\{([^}]+)\}', r'√(\1)', text)
    text = re.sub(r'\\sqrt', '√', text)
    text = re.sub(r'\\cdot', '×', text)
    text = re.sub(r'\\left[\(\[]', '(', text)
    text = re.sub(r'\\right[\)\]]', ')', text)
    text = re.sub(r'\^\{([^}]+)\}', r'^\1', text)
    text = re.sub(r'\_\{([^}]+)\}', r'_\1', text)
    greek = {
        'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'δ', 'epsilon': 'ε',
        'theta': 'θ', 'lambda': 'λ', 'mu': 'μ', 'pi': 'π', 'sigma': 'σ',
        'phi': 'φ', 'omega': 'ω', 'infty': '∞', 'pm': '±', 'times': '×',
        'leq': '≤', 'geq': '≥', 'neq': '≠'
    }
    for eng, sym in greek.items():
        text = text.replace('\\' + eng + ' ', sym + ' ').replace('\\' + eng, sym)
    text = re.sub(r'\\[a-zA-Z]+\s?', '', text)
    text = re.sub(r'\{([^{}]*)\}', r'\1', text)
    text = re.sub(r'[{}]', '', text)
    text = re.sub(r'#{2,6}\s*', '', text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ══════════════════════════════════════════════
# REDIS — С ЗАЩИТОЙ ОТ ЗАВИСАНИЙ
# ══════════════════════════════════════════════
class RedisClient:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    def _cmd(self, *args):
        for attempt in range(3):
            try:
                r = requests.post(
                    self.url, headers=self.headers,
                    json=list(args), timeout=5  # УЛУЧШЕНО: таймаут 5 сек вместо 8
                )
                if r.status_code == 200:
                    return r.json().get("result")
                log_event(f"Redis HTTP {r.status_code} attempt {attempt + 1}")
            except requests.exceptions.Timeout:
                log_event(f"Redis timeout attempt {attempt + 1}")
            except Exception as e:
                log_event(f"Redis error attempt {attempt + 1}: {e}")
            if attempt < 2:
                time.sleep(0.2)  # УЛУЧШЕНО: короче пауза
        return None

    def get(self, key):
        raw = self._cmd("GET", key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except:
            return raw

    def set(self, key, value):
        self._cmd("SET", key, json.dumps(value, ensure_ascii=False, default=str))

    def delete(self, key):
        self._cmd("DEL", key)

    def sadd(self, key, *members):
        for m in members:
            self._cmd("SADD", key, str(m))

    def srem(self, key, member):
        self._cmd("SREM", key, str(member))

    def smembers(self, key):
        result = self._cmd("SMEMBERS", key)
        return set(str(x) for x in result) if result else set()


class DataStore:
    def __init__(self):
        self.r = RedisClient(UPSTASH_URL, UPSTASH_TOKEN)

    def get_user(self, uid):
        return self.r.get(f"user:{str(uid).strip()}")

    def set_user(self, uid, expires, plan="paid"):
        uid = str(uid).strip()
        exp_str = expires.isoformat() if isinstance(expires, datetime) else (
            str(expires).strip() if expires else None)
        self.r.set(f"user:{uid}", {"expires": exp_str, "plan": plan})
        self.r.sadd("paid_uids", uid)
        exp_dt = expires if isinstance(expires, datetime) else None
        access_cache[uid] = {"expires": exp_dt, "plan": plan}
        log_event(f"set_user uid={uid} plan={plan} exp={exp_str}")

    def remove_user(self, uid):
        uid = str(uid).strip()
        self.r.delete(f"user:{uid}")
        self.r.srem("paid_uids", uid)
        access_cache.pop(uid, None)

    def has_access(self, uid, username=""):
        uid = str(uid).strip()
        if username and username.lower().lstrip("@") in VIP_USERNAMES:
            return True
        if self.is_blocked(uid):
            return False
        if uid in access_cache:
            cached = access_cache[uid]
            if cached.get("expires") is None:
                return True
            if datetime.now() < cached["expires"]:
                return True
            else:
                del access_cache[uid]
        u = self.get_user(uid)
        if not u:
            if uid in self.r.smembers("paid_uids"):
                log_event(f"has_access: Redis slow, uid={uid} in paid_uids - allowing")
                return True
            return False
        exp = u.get("expires")
        plan = u.get("plan", "paid")
        if exp is None:
            access_cache[uid] = {"expires": None, "plan": plan}
            return True
        try:
            exp_str = str(exp).strip().strip('"').strip("'")
            exp_dt = datetime.fromisoformat(exp_str)
            if datetime.now() < exp_dt:
                access_cache[uid] = {"expires": exp_dt, "plan": plan}
                return True
            else:
                self.remove_user(uid)
                return False
        except Exception as e:
            log_event(f"has_access date parse error uid={uid}: {e}")
            access_cache[uid] = {"expires": None, "plan": plan}
            return True

    def sub_status(self, uid):
        uid = str(uid).strip()
        if self.is_blocked(uid):
            return "🚫 Заблокирован"
        u = self.get_user(uid)
        if not u:
            return "❌ Нет подписки"
        exp = u.get("expires")
        plan = u.get("plan", "paid")
        if exp is None:
            return f"♾ Бессрочная ({plan})"
        try:
            exp_dt = datetime.fromisoformat(str(exp).strip())
            if datetime.now() < exp_dt:
                left = (exp_dt - datetime.now()).days
                return f"✅ {plan} до {exp_dt.strftime('%d.%m.%Y')} ({left}д)"
            return "❌ Истекла"
        except:
            return "❓ Неизвестно"

    def block(self, uid):
        self.r.sadd("blocked", str(uid))

    def unblock(self, uid):
        self.r.srem("blocked", str(uid))

    def is_blocked(self, uid):
        return str(uid).strip() in self.r.smembers("blocked")

    def register_user(self, uid, username, name):
        uid = str(uid).strip()
        st = self.r.get(f"stats:{uid}")
        self.r.sadd("all_uids", uid)
        if not st:
            self.r.set(f"stats:{uid}", {
                "total_msgs": 0, "daily": {},
                "joined": datetime.now().isoformat(),
                "username": username or "", "name": name or ""
            })
            log_event(f"New user: {uid} @{username}")
            return True
        st["username"] = username or st.get("username", "")
        st["name"] = name or st.get("name", "")
        self.r.set(f"stats:{uid}", st)
        return False

    def count_message(self, uid):
        uid = str(uid).strip()
        today = datetime.now().strftime("%Y-%m-%d")
        mc = self.r.get(f"msgcount:{uid}") or {"date": today, "count": 0}
        if mc.get("date") != today:
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
        uid = str(uid).strip()
        today = datetime.now().strftime("%Y-%m-%d")
        mc = self.r.get(f"msgcount:{uid}") or {}
        return mc.get("count", 0) if mc.get("date") == today else 0

    def get_analytics(self):
        all_uids = self.r.smembers("all_uids")
        paid_uids = self.r.smembers("paid_uids")
        blocked = self.r.smembers("blocked")
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        total_msgs = dau_today = dau_yest = new_week = 0
        top_list = []
        for uid in all_uids:
            st = self.r.get(f"stats:{uid}") or {}
            total_msgs += st.get("total_msgs", 0)
            if today in st.get("daily", {}):
                dau_today += 1
            if yesterday in st.get("daily", {}):
                dau_yest += 1
            if st.get("joined", "0") > week_ago:
                new_week += 1
            top_list.append((uid, st))
        top_list.sort(key=lambda x: x[1].get("total_msgs", 0), reverse=True)
        return {
            "total_users": len(all_uids), "paid_users": len(paid_uids),
            "blocked": len(blocked), "dau_today": dau_today,
            "dau_yest": dau_yest, "total_msgs": total_msgs,
            "new_week": new_week, "top_users": top_list[:5]
        }

    def save_memory(self, uid, key, value):
        uid = str(uid).strip()
        mem = self.r.get(f"memory:{uid}") or {}
        mem[key] = value
        self.r.set(f"memory:{uid}", mem)

    def get_memory(self, uid):
        return self.r.get(f"memory:{uid}") or {}

    def get_memory_context(self, uid):
        mem = self.get_memory(uid)
        parts = []
        if mem.get("name"):
            parts.append(f"Имя: {mem['name']}")
        if mem.get("age"):
            parts.append(f"Возраст: {mem['age']}")
        if mem.get("birthday"):
            parts.append(f"День рождения: {mem['birthday']}")
        if mem.get("city"):
            parts.append(f"Город: {mem['city']}")
        if mem.get("interests"):
            parts.append(f"Интересы: {mem['interests']}")
        if mem.get("about"):
            parts.append(f"О себе: {mem['about']}")
        return ("\n\nЧто ты знаешь о пользователе:\n" + "\n".join(parts)) if parts else ""

    def get_ref_code(self, uid):
        uid = str(uid).strip()
        data = self.r.get(f"referral:{uid}")
        if not data:
            data = {"code": f"ref{uid}", "invited": [], "bonus_days": 0}
            self.r.set(f"referral:{uid}", data)
        return data["code"]

    def apply_referral(self, new_uid, ref_code):
        new_uid = str(new_uid).strip()
        for owner_uid in self.r.smembers("all_uids"):
            rd = self.r.get(f"referral:{owner_uid}")
            if rd and rd.get("code") == ref_code and new_uid not in rd.get("invited", []) and owner_uid != new_uid:
                rd["invited"] = rd.get("invited", []) + [new_uid]
                rd["bonus_days"] = rd.get("bonus_days", 0) + 7
                self.r.set(f"referral:{owner_uid}", rd)
                try:
                    cur = self.get_user(int(owner_uid))
                    if cur and cur.get("expires"):
                        exp = datetime.fromisoformat(str(cur["expires"]).strip())
                        self.set_user(int(owner_uid), exp + timedelta(days=7), plan=cur.get("plan", "paid"))
                    else:
                        self.set_user(int(owner_uid), datetime.now() + timedelta(days=7), plan="referral")
                except:
                    pass
                return int(owner_uid)
        return None

    def add_note(self, uid, text):
        uid = str(uid).strip()
        notes = self.r.get(f"notes:{uid}") or []
        notes.append({"text": text, "date": datetime.now().isoformat()})
        self.r.set(f"notes:{uid}", notes[-50:])

    def get_notes(self, uid):
        return self.r.get(f"notes:{uid}") or []

    def add_reminder(self, uid, text, time_str, daily=False):
        uid = str(uid).strip()
        rems = self.r.get(f"reminders:{uid}") or []
        rems.append({"text": text, "time": time_str, "daily": daily, "created": datetime.now().isoformat()})
        self.r.set(f"reminders:{uid}", rems)

    def get_reminders(self, uid):
        return self.r.get(f"reminders:{uid}") or []

    def remove_reminder(self, uid, idx):
        uid = str(uid).strip()
        rems = self.r.get(f"reminders:{uid}") or []
        if 0 <= idx < len(rems):
            rems.pop(idx)
            self.r.set(f"reminders:{uid}", rems)

    def all_reminders(self):
        result = {}
        for uid in self.r.smembers("all_uids"):
            rems = self.r.get(f"reminders:{uid}")
            if rems:
                result[uid] = rems
        return result


db = DataStore()
bot = telebot.TeleBot(TELEGRAM_TOKEN)
histories = {}
modes = {}
mood_log = {}
todo_list = {}
last_answer = {}
quiz_state = {}
access_cache = {}

MAX_HISTORY = 20

try:
    from gtts import gTTS
    VOICE_ENABLED = True
except:
    VOICE_ENABLED = False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        try:
            a = db.get_analytics()
            self.wfile.write(
                f"Liya v4.0 | Users:{a['total_users']} Paid:{a['paid_users']} DAU:{a['dau_today']}".encode())
        except:
            self.wfile.write(b"Liya v4.0 OK")

    def log_message(self, *a):
        pass


threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), Handler).serve_forever(),
    daemon=True
).start()


def reminder_scheduler():
    while True:
        try:
            now_str = datetime.now().strftime("%H:%M")
            for uid_str, rems in list(db.all_reminders().items()):
                to_del = []
                for i, r in enumerate(rems):
                    if r.get("time") == now_str:
                        try:
                            kb = types.InlineKeyboardMarkup()
                            kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
                            d = "🔁 Ежедневное\n" if r.get("daily") else ""
                            bot.send_message(int(uid_str), f"⏰ Напоминание!\n\n{r['text']}\n\n{d}",
                                             reply_markup=kb)
                        except:
                            pass
                        if not r.get("daily"):
                            to_del.append(i)
                for idx in reversed(to_del):
                    db.remove_reminder(uid_str, idx)
        except Exception as e:
            log_event(f"Reminder: {e}")
        time.sleep(60)


threading.Thread(target=reminder_scheduler, daemon=True).start()


def get_greeting():
    h = datetime.now().hour
    if 5 <= h < 12: return "☀️ Доброе утро"
    if 12 <= h < 17: return "🌤 Добрый день"
    if 17 <= h < 22: return "🌆 Добрый вечер"
    return "🌙 Привет"


def get_history(uid):
    if uid not in histories:
        histories[uid] = []
    return histories[uid]


def is_admin(u):
    return u and u.lower().lstrip("@") in ADMIN_USERNAMES


def is_vip(u):
    return u and u.lower().lstrip("@") in VIP_USERNAMES


def mode_system(uid):
    base = SYSTEM_PROMPT + db.get_memory_context(uid)
    m = modes.get(uid, "normal")
    if m == "study":
        base += "\n\nРежим УЧЁБЫ: объясняй каждый шаг, приводи примеры, проверяй понимание."
    if m == "support":
        base += "\n\nРежим ПОДДЕРЖКИ: будь особенно нежной, внимательной, поддерживающей. Не давай советов пока не спросят."
    if m == "creative":
        base += "\n\nРежим ТВОРЧЕСТВА: генерируй необычные идеи, мысли нестандартно, будь вдохновляющей."
    return base


def mode_name(uid):
    return {"normal": "💬 Обычный", "study": "📚 Учёба", "support": "🤗 Поддержка",
            "creative": "🎨 Творчество"}.get(modes.get(uid, "normal"), "💬 Обычный")


def check_daily_limit(uid, username=""):
    if is_vip(username):
        return True, 0, 9999
    u = db.get_user(uid)
    count = db.get_daily_count(uid)
    if not u:
        return count < FREE_MSG_LIMIT, count, FREE_MSG_LIMIT
    if u.get("plan") == "trial":
        return count < FREE_DAILY_LIMIT, count, FREE_DAILY_LIMIT
    return True, count, 9999


# ══════════════════════════════════════════════
# GEMINI AI — БЕСПЛАТНЫЙ ЗАПАСНОЙ
# ══════════════════════════════════════════════
def ask_gemini(text, system_prompt="", history=None):
    """Вызов Gemini Flash — бесплатная альтернатива когда Groq лагает"""
    if not GEMINI_KEY:
        raise Exception("GEMINI_KEY не задан")

    contents = []

    # Добавляем историю
    if history:
        for msg in history[-10:]:  # последние 10 сообщений
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    # Текущее сообщение
    contents.append({"role": "user", "parts": [{"text": text}]})

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": system_prompt}]} if system_prompt else None,
        "generationConfig": {"maxOutputTokens": 2000, "temperature": 0.7}
    }
    if not system_prompt:
        del payload["systemInstruction"]

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    r = requests.post(url, json=payload, timeout=40)
    data = r.json()

    if "error" in data:
        raise Exception(f"Gemini error: {data['error'].get('message', 'unknown')}")

    text_resp = data["candidates"][0]["content"]["parts"][0]["text"]
    return clean_response(text_resp)


def ask_gemini_vision(image_b64, text, system_prompt=""):
    """Gemini для анализа фото"""
    if not GEMINI_KEY:
        raise Exception("GEMINI_KEY не задан")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                {"text": text or "Опиши фото. Если есть задачи — реши пошагово."}
            ]
        }],
        "generationConfig": {"maxOutputTokens": 2000}
    }
    r = requests.post(url, json=payload, timeout=50)
    data = r.json()
    if "error" in data:
        raise Exception(f"Gemini vision error: {data['error'].get('message')}")
    return clean_response(data["candidates"][0]["content"]["parts"][0]["text"])


# ══════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ AI — GROQ + GEMINI FALLBACK
# ══════════════════════════════════════════════
def ask_ai(uid, text, image_b64=None, custom_system=None):
    history = get_history(uid)
    sys_msg = custom_system or mode_system(uid)

    if image_b64:
        # Сначала пробуем Groq vision
        msgs = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text",
                 "text": text or "Внимательно посмотри на фото. Если на фото есть математика, задачи, уравнения, текст — прочитай всё и реши/объясни пошагово. Пиши обычным текстом без LaTeX."}
            ]}
        ]
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                data=json.dumps({"model": MODEL_VISION, "messages": msgs, "max_tokens": 2000}),
                timeout=45
            )
            data = r.json()
            if "error" not in data:
                return clean_response(data["choices"][0]["message"]["content"])
            log_event(f"Groq vision error, trying Gemini: {data['error']}")
        except Exception as e:
            log_event(f"Groq vision failed: {e}, trying Gemini")

        # Fallback на Gemini для фото
        if GEMINI_KEY:
            try:
                return ask_gemini_vision(image_b64, text, sys_msg)
            except Exception as e:
                log_event(f"Gemini vision also failed: {e}")
        raise Exception("Все модели для фото недоступны")

    # Текстовый запрос
    history.append({"role": "user", "content": text})
    if len(history) > MAX_HISTORY:
        histories[uid] = history[-MAX_HISTORY:]
        history = histories[uid]

    msgs = [{"role": "system", "content": sys_msg}] + history

    # Список моделей Groq для перебора
    groq_models = [
        "llama-3.3-70b-versatile",
        "llama3-70b-8192",
        "llama3-8b-8192",
        "gemma2-9b-it",
    ]

    last_error = "unknown"
    for try_model in groq_models:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                data=json.dumps({"model": try_model, "messages": msgs, "max_tokens": 2000}),
                timeout=35  # УЛУЧШЕНО: таймаут 35 сек
            )
            data = r.json()
            if "error" in data:
                last_error = data["error"].get("message", "API error")
                log_event(f"Groq {try_model} error: {last_error}")
                continue
            answer = clean_response(data["choices"][0]["message"]["content"])
            history.append({"role": "assistant", "content": answer})
            return answer
        except requests.exceptions.Timeout:
            last_error = "timeout"
            log_event(f"Groq {try_model} timeout")
            continue
        except Exception as e:
            last_error = str(e)
            log_event(f"Groq {try_model} failed: {e}")
            continue

    # НОВОЕ: Fallback на Gemini если все Groq модели упали
    if GEMINI_KEY:
        log_event("All Groq models failed, switching to Gemini...")
        try:
            answer = ask_gemini(text, sys_msg, history[:-1])  # история без последнего user msg
            history.append({"role": "assistant", "content": answer})
            return answer + "\n\n_(ответ через резервный AI)_"
        except Exception as e:
            log_event(f"Gemini fallback also failed: {e}")

    raise Exception(f"Все AI модели недоступны: {last_error}")


# ══════════════════════════════════════════════
# НОВОЕ: АНАЛИЗ PDF ДОКУМЕНТОВ
# ══════════════════════════════════════════════
def analyze_pdf_bytes(pdf_bytes, question=""):
    """Анализирует PDF через Gemini (поддерживает нативно) или извлекает текст"""

    # Пробуем через Gemini — он нативно понимает PDF
    if GEMINI_KEY:
        try:
            pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
            prompt = question or "Проанализируй этот документ. Выдели: 1) О чём документ 2) Ключевые моменты 3) Важные данные/числа 4) Выводы"
            payload = {
                "contents": [{
                    "parts": [
                        {"inline_data": {"mime_type": "application/pdf", "data": pdf_b64}},
                        {"text": prompt}
                    ]
                }],
                "generationConfig": {"maxOutputTokens": 3000}
            }
            r = requests.post(url, json=payload, timeout=60)
            data = r.json()
            if "error" not in data:
                return clean_response(data["candidates"][0]["content"]["parts"][0]["text"])
            log_event(f"Gemini PDF error: {data['error']}")
        except Exception as e:
            log_event(f"Gemini PDF failed: {e}")

    # Fallback — попробуем извлечь текст через pypdf если установлен
    try:
        import io
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts = []
        for i, page in enumerate(reader.pages[:20]):  # максимум 20 страниц
            t = page.extract_text()
            if t:
                text_parts.append(f"[Стр. {i + 1}]\n{t}")

        if not text_parts:
            return "😔 Не смогла прочитать PDF. Возможно документ защищён или содержит только изображения."

        full_text = "\n\n".join(text_parts)[:8000]  # Ограничение токенов
        prompt = f"Вот текст из PDF документа:\n\n{full_text}\n\n"
        prompt += question or "Проанализируй: 1) О чём документ 2) Ключевые моменты 3) Важные данные 4) Выводы"

        return ask_ai(0, prompt, custom_system=SYSTEM_PROMPT)

    except Exception as e:
        log_event(f"PDF text extraction failed: {e}")
        return "😔 Не смогла обработать PDF. Попробуй отправить текст вручную или используй Gemini API (GEMINI_KEY)."


def transcribe_voice(audio, fname="voice.ogg"):
    # УЛУЧШЕНО: retry 2 раза
    for attempt in range(2):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                files={"file": (fname, audio, "audio/ogg")},
                data={"model": MODEL_WHISPER, "language": "ru", "response_format": "text"},
                timeout=30
            )
            if r.status_code == 200:
                return r.text.strip()
            log_event(f"Whisper attempt {attempt + 1}: status {r.status_code}")
        except Exception as e:
            log_event(f"Whisper attempt {attempt + 1} error: {e}")
        if attempt == 0:
            time.sleep(1)
    raise Exception("Whisper недоступен")


# ══════════════════════════════════════════════
# УЛУЧШЕННАЯ ГЕНЕРАЦИЯ КАРТИНОК
# ══════════════════════════════════════════════
def enhance_image_prompt(prompt_ru):
    """Улучшает промпт для лучшего качества картинок"""
    # Переводим на английский через Groq
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            data=json.dumps({
                "model": "llama-3.3-70b-versatile",
                "messages": [{
                    "role": "user",
                    "content": f"""Translate this image description to English and enhance it for AI image generation.
Add artistic quality keywords like: masterpiece, highly detailed, beautiful lighting, 8k, photorealistic (if realistic style).
Return ONLY the enhanced English prompt, nothing else.

Original: {prompt_ru}"""
                }],
                "max_tokens": 200
            }),
            timeout=15
        )
        translated = r.json()["choices"][0]["message"]["content"].strip()
        if translated and len(translated) < 600:
            log_event(f"Enhanced prompt: {translated[:80]}")
            return translated
    except Exception as e:
        log_event(f"Prompt enhancement error: {e}")

    # Простой перевод без улучшения
    return prompt_ru


def generate_image(prompt_ru):
    prompt_en = enhance_image_prompt(prompt_ru)

    # Стили для лучшего качества
    quality_suffix = ", masterpiece, highly detailed, beautiful composition"

    # Пробуем модели в порядке качества
    models_and_settings = [
        ("flux", 1024, 1024),
        ("flux-realism", 1024, 1024),
        ("turbo", 1024, 1024),
        ("flux", 896, 896),
    ]

    for model, w, h in models_and_settings:
        for attempt in range(2):
            try:
                seed = random.randint(1, 99999)
                full_prompt = prompt_en + quality_suffix
                url = f"https://image.pollinations.ai/prompt/{quote(full_prompt)}?model={model}&width={w}&height={h}&seed={seed}&nologo=true&enhance=true"
                resp = requests.get(url, timeout=90, stream=True)
                if resp.status_code == 200:
                    data = resp.content
                    if len(data) > 5000:  # УЛУЧШЕНО: минимум 5кб (не 1кб)
                        log_event(f"Image OK: model={model} size={len(data)}")
                        return data
                log_event(f"Image small/failed: model={model} status={resp.status_code} size={len(resp.content) if resp.content else 0}")
            except Exception as e:
                log_event(f"Image error model={model}: {e}")
            time.sleep(1)

    # Последний шанс
    try:
        simple_url = f"https://image.pollinations.ai/prompt/{quote(prompt_en)}"
        resp = requests.get(simple_url, timeout=90)
        if resp.status_code == 200 and len(resp.content) > 5000:
            return resp.content
    except Exception as e:
        log_event(f"Final image attempt failed: {e}")

    return None


def get_weather(city):
    try:
        r = requests.get(f"https://wttr.in/{quote(city)}?format=j1&lang=ru", timeout=10)
        c = r.json()["current_condition"][0]
        return (f"🌤 Погода в {city}:\n\n"
                f"🌡 {c['temp_C']}°C (ощущается {c['FeelsLikeC']}°C)\n"
                f"☁️ {c['lang_ru'][0]['value']}\n"
                f"💧 Влажность: {c['humidity']}%\n"
                f"💨 Ветер: {c['windspeedKmph']} км/ч")
    except:
        return f"😔 Не нашла погоду для '{city}'."


def get_currency():
    try:
        rates = requests.get("https://api.exchangerate-api.com/v4/latest/RUB", timeout=10).json().get("rates", {})
        return (f"💰 Курс валют:\n\n"
                f"🇺🇸 1 USD = {round(1 / rates.get('USD', 0.011), 2)} ₽\n"
                f"🇪🇺 1 EUR = {round(1 / rates.get('EUR', 0.010), 2)} ₽\n"
                f"🇰🇿 1 ₽ = {round(rates.get('KZT', 5.5), 2)} ₸\n\n"
                f"Обновлено ⏱")
    except:
        return "😔 Не могу получить курс."


def notify_admin(text):
    for uid in db.r.smembers("all_uids"):
        st = db.r.get(f"stats:{uid}") or {}
        if st.get("username", "").lower() in ADMIN_USERNAMES:
            try:
                bot.send_message(int(uid), f"🔔 {text}")
            except:
                pass


def send_safe(chat_id, text, reply_to=None, kb=None, delete_msg_id=None):
    if delete_msg_id:
        try:
            bot.delete_message(chat_id, delete_msg_id)
        except:
            pass
    chunks = [text[i:i + 4096] for i in range(0, len(text), 4096)]
    for i, chunk in enumerate(chunks):
        markup = kb if i == len(chunks) - 1 else None
        try:
            if reply_to and i == 0:
                bot.reply_to(reply_to, chunk, reply_markup=markup)
            else:
                bot.send_message(chat_id, chunk, reply_markup=markup)
        except Exception as e:
            log_event(f"send_safe error: {e}")


def delete_and_send(call, text, kb=None):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    try:
        bot.send_message(call.from_user.id, text, reply_markup=kb)
    except Exception as e:
        log_event(f"delete_and_send error: {e}")


# ── КОНСТАНТЫ ──
ZODIAC_SIGNS = ["♈ Овен", "♉ Телец", "♊ Близнецы", "♋ Рак", "♌ Лев", "♍ Дева",
                "♎ Весы", "♏ Скорпион", "♐ Стрелец", "♑ Козерог", "♒ Водолей", "♓ Рыбы"]
QUIZ_TOPICS = {"🌍 География": "geography", "🎬 Кино": "movies", "🎵 Музыка": "music",
               "🧪 Наука": "science", "📚 Литература": "literature", "🏆 Спорт": "sport",
               "🍕 Еда": "food", "💄 Красота": "beauty", "🐾 Животные": "animals", "🌟 Случайное": "random"}
COMPLIMENTS = ["Ты просто замечательная! ✨", "Ты умница и красавица 💕",
               "С тобой всегда интересно! 🌸", "Ты справишься со всем, верю в тебя 💪", "Ты особенная 🦋"]
AFFIRMATIONS = ["Я достойна любви и счастья 💕", "Я справляюсь со всем 💪",
                "Каждый день я становлюсь лучше ✨", "Мои мечты реальны 🎯", "Я верю в себя 🦋"]
MEDITATIONS = [
    {"name": "🌬 Дыхание 4-7-8",
     "text": "Снимает тревогу:\n\n1. Вдох — 4 сек\n2. Задержка — 7 сек\n3. Выдох — 8 сек\n\nПовтори 4 раза 🌿"},
    {"name": "🧘 5-4-3-2-1",
     "text": "Назови:\n\n5 вещей которые видишь\n4 которые потрогаешь\n3 звука\n2 запаха\n1 вкус\n\nВозвращает в момент 💙"},
    {"name": "💤 Для сна",
     "text": "Перед сном:\n\n• Напряги всё тело 5 сек\n• Резко расслабь\n• Медленно дыши\n• Думай о приятном 😴"},
]
MOOD_EMOJIS = {"😊": "Хорошо", "🤩": "Отлично", "😔": "Грустно", "😤": "Злюсь",
               "😰": "Тревожно", "😴": "Устала", "🥰": "Влюблена", "😐": "Нейтрально"}
LANGUAGES = {"🇬🇧 Английский": "English", "🇩🇪 Немецкий": "German", "🇫🇷 Французский": "French",
             "🇪🇸 Испанский": "Spanish", "🇨🇳 Китайский": "Chinese", "🇯🇵 Японский": "Japanese",
             "🇰🇷 Корейский": "Korean", "🇹🇷 Турецкий": "Turkish"}


# ── КЛАВИАТУРЫ ──
def main_menu_kb(username=""):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💬 Поговорить", callback_data="mode_normal"),
        types.InlineKeyboardButton("📚 Учёба", callback_data="mode_study"),
        types.InlineKeyboardButton("🤗 Поддержка", callback_data="mode_support"),
        types.InlineKeyboardButton("🎨 Творчество", callback_data="mode_creative"),
        types.InlineKeyboardButton("🖼 Нарисовать", callback_data="btn_imagine"),
        types.InlineKeyboardButton("🧠 Викторина", callback_data="btn_quiz"),
        types.InlineKeyboardButton("🌙 Гороскоп", callback_data="btn_horoscope"),
        types.InlineKeyboardButton("💄 Уход за собой", callback_data="btn_beauty"),
        types.InlineKeyboardButton("💌 Любовное письмо", callback_data="btn_love"),
        types.InlineKeyboardButton("📖 Пересказ текста", callback_data="btn_summarize"),
        types.InlineKeyboardButton("🌍 Переводчик", callback_data="btn_translate"),
        types.InlineKeyboardButton("💰 Курс валют", callback_data="btn_currency"),
        types.InlineKeyboardButton("🌤 Погода", callback_data="btn_weather"),
        types.InlineKeyboardButton("🍽 Рецепт", callback_data="btn_recipe"),
        types.InlineKeyboardButton("🧘 Медитация", callback_data="btn_meditation"),
        types.InlineKeyboardButton("📊 Настроение", callback_data="btn_mood"),
        types.InlineKeyboardButton("🗓 Планировщик", callback_data="btn_planner"),
        types.InlineKeyboardButton("📝 Список дел", callback_data="btn_todo"),
        types.InlineKeyboardButton("⏰ Напоминания", callback_data="btn_reminders"),
        types.InlineKeyboardButton("📓 Дневник", callback_data="btn_notes"),
        types.InlineKeyboardButton("🧠 Моя память", callback_data="btn_memory"),
        types.InlineKeyboardButton("🔗 Пригласить", callback_data="btn_referral"),
        types.InlineKeyboardButton("🤣 Шутка", callback_data="btn_joke"),
        types.InlineKeyboardButton("🌟 Факт дня", callback_data="btn_fact"),
        types.InlineKeyboardButton("💪 Мотивация", callback_data="btn_motivation"),
        types.InlineKeyboardButton("✨ Комплимент", callback_data="btn_compliment"),
        types.InlineKeyboardButton("📸 Анализ фото", callback_data="btn_photo_hint"),
        types.InlineKeyboardButton("📄 Анализ PDF", callback_data="btn_pdf_hint"),  # НОВОЕ
        types.InlineKeyboardButton("📱 Мой аккаунт", callback_data="btn_account"),
        types.InlineKeyboardButton("🔄 Новый диалог", callback_data="btn_new"),
        types.InlineKeyboardButton("ℹ️ Помощь", callback_data="btn_help"),
        types.InlineKeyboardButton("👨‍💻 Разработчик", url="https://t.me/tronqx"),
    )
    if is_admin(username):
        kb.add(types.InlineKeyboardButton("👑 Админ-панель", callback_data="adm_panel"))
    return kb


def after_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"),
        types.InlineKeyboardButton("🎵 Голос", callback_data="btn_voice_last"),
        types.InlineKeyboardButton("🧠 Объясни проще", callback_data="btn_explain_simple"),
        types.InlineKeyboardButton("📖 Подробнее", callback_data="btn_elaborate"),
        types.InlineKeyboardButton("📓 В дневник", callback_data="btn_save_note"),
        types.InlineKeyboardButton("🔄 Новый диалог", callback_data="btn_new"),
    )
    return kb


def access_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🎁 3 дня бесплатно", callback_data="btn_trial"),
        types.InlineKeyboardButton("⭐ Оплатить Stars", callback_data="btn_pay_stars"),
        types.InlineKeyboardButton("💳 Написать @tronqx", url=PAYMENT_LINK),
    )
    return kb


def admin_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📊 Аналитика", callback_data="adm_stats"),
        types.InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"),
        types.InlineKeyboardButton("➕ Бессрочная", callback_data="adm_grant"),
        types.InlineKeyboardButton("🕐 На время", callback_data="adm_grant_time"),
        types.InlineKeyboardButton("❌ Отозвать", callback_data="adm_revoke"),
        types.InlineKeyboardButton("🚫 Заблок", callback_data="adm_block"),
        types.InlineKeyboardButton("✅ Разблок", callback_data="adm_unblock"),
        types.InlineKeyboardButton("📢 Рассылка", callback_data="adm_broadcast"),
        types.InlineKeyboardButton("🔙 Меню", callback_data="btn_menu"),
    )
    return kb


def time_kb(target=""):
    kb = types.InlineKeyboardMarkup(row_width=3)
    p = f"adm_time_{target}_" if target else "adm_time__"
    kb.add(
        types.InlineKeyboardButton("1 день", callback_data=f"{p}1"),
        types.InlineKeyboardButton("3 дня", callback_data=f"{p}3"),
        types.InlineKeyboardButton("7 дней", callback_data=f"{p}7"),
        types.InlineKeyboardButton("14 дней", callback_data=f"{p}14"),
        types.InlineKeyboardButton("30 дней", callback_data=f"{p}30"),
        types.InlineKeyboardButton("90 дней", callback_data=f"{p}90"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="adm_panel"),
    )
    return kb


# ── ПРОВЕРКА ДОСТУПА ──
def check_access(msg):
    uid = msg.from_user.id
    u = msg.from_user.username or ""
    if is_vip(u):
        return True
    if db.is_blocked(str(uid)):
        bot.reply_to(msg, "🚫 Заблокирована!")
        return False
    if db.has_access(uid, u):
        return True
    modes[uid] = "normal"
    bot.reply_to(msg, f"🔒 Доступ платный\n\n⭐ {PRICE_STARS} Stars или @tronqx\n\n🎁 Или 3 дня бесплатно!",
                 reply_markup=access_kb())
    return False


def check_access_cb(call):
    uid = call.from_user.id
    u = call.from_user.username or ""
    if is_vip(u):
        return True
    if db.is_blocked(str(uid)):
        bot.answer_callback_query(call.id, "🚫 Заблокирована!")
        return False
    if db.has_access(uid, u):
        return True
    modes[uid] = "normal"
    bot.answer_callback_query(call.id, "🔒 Нет доступа!")
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    bot.send_message(uid, f"🔒 Нужна подписка!\n\n⭐ {PRICE_STARS} Stars или @tronqx\n\n🎁 Или 3 дня бесплатно!",
                     reply_markup=access_kb())
    return False


def check_and_count(msg):
    uid = msg.from_user.id
    u = msg.from_user.username or ""
    if not check_access(msg):
        return False
    ok, count, limit = check_daily_limit(uid, u)
    if not ok:
        plan = (db.get_user(uid) or {}).get("plan", "free")
        if plan == "trial":
            bot.reply_to(msg, f"⚠️ Лимит пробного: {limit} сообщений/день\n\nКупи полный доступ!",
                         reply_markup=access_kb())
        else:
            bot.reply_to(msg, f"⚠️ Лимит {limit}/день исчерпан.")
        return False
    db.count_message(uid)
    return True


# ── КОМАНДЫ ──
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid = msg.from_user.id
    u = msg.from_user.username or ""
    name = msg.from_user.first_name or "Солнышко"
    is_new = db.register_user(uid, u, name)
    histories[uid] = []
    modes[uid] = "normal"
    db.save_memory(uid, "name", name)
    ref_bonus = ""
    parts = msg.text.split()
    if len(parts) > 1 and parts[1].startswith("ref"):
        owner = db.apply_referral(uid, parts[1])
        if owner:
            ref_bonus = "\n🎁 Реферальный бонус применён!"
            try:
                bot.send_message(owner, "🎉 По твоей ссылке зарегистрировались! +7 дней 🌸")
            except:
                pass
    if is_new:
        notify_admin(f"👤 Новый: {uid} @{u} {name}")
    bot.reply_to(msg,
                 f"{get_greeting()}, {name}! ✨{ref_bonus}\n\n"
                 f"Я Лия — твой умный AI-ассистент 👑\n\n"
                 f"💬 Общаюсь как ChatGPT\n"
                 f"📸 Решаю задачи по фото\n"
                 f"📄 Анализирую PDF документы\n"
                 f"🖼 Генерирую красивые картинки\n"
                 f"🎤 Расшифровываю голосовые\n"
                 f"🧮 Математика, физика, код\n\n"
                 f"Выбери с чего начнём 👇",
                 reply_markup=main_menu_kb(u))


@bot.message_handler(commands=["menu"])
def cmd_menu(msg):
    db.register_user(msg.from_user.id, msg.from_user.username or "", msg.from_user.first_name or "")
    bot.reply_to(msg, f"Меню 🌸 | Режим: {mode_name(msg.from_user.id)}",
                 reply_markup=main_menu_kb(msg.from_user.username or ""))


@bot.message_handler(commands=["new"])
def cmd_new(msg):
    uid = msg.from_user.id
    histories[uid] = []
    modes[uid] = "normal"
    bot.reply_to(msg, "🔄 Начнём с чистого листа! Пиши что угодно 🌸")


@bot.message_handler(commands=["status"])
def cmd_status(msg):
    uid = msg.from_user.id
    u = msg.from_user.username or ""
    has = db.has_access(uid, u)
    sub = db.sub_status(uid)
    cached = access_cache.get(str(uid))
    bot.reply_to(msg, f"Статус:\n\nID: {uid}\nДоступ: {has}\nПодписка: {sub}\nКэш: {cached}\nVIP: {is_vip(u)}")


@bot.message_handler(commands=["myid"])
def cmd_myid(msg):
    bot.reply_to(msg, f"Твой ID: {msg.from_user.id}")


@bot.message_handler(commands=["grant"])
def cmd_grant(msg):
    if not is_admin(msg.from_user.username or ""):
        return
    p = msg.text.split()
    if len(p) < 2:
        bot.reply_to(msg, "Использование: /grant [id]")
        return
    try:
        t = int(p[1])
        db.set_user(t, None, plan="forever")
        db.unblock(t)
        bot.reply_to(msg, f"✅ Бессрочная выдана {t}")
        try:
            bot.send_message(t, "🎉 Тебе выдан бессрочный доступ! /start 🌸")
        except:
            pass
    except:
        bot.reply_to(msg, "❌ Неверный ID")


@bot.message_handler(commands=["remind"])
def cmd_remind(msg):
    if not check_access(msg):
        return
    p = msg.text.split(maxsplit=2)
    if len(p) < 3:
        bot.reply_to(msg,
                     "⏰ Формат: /remind 18:00 выпить воду\nЕжедневно: /remind 08:00 зарядка каждый день")
        return
    t = p[1]
    txt = p[2]
    daily = txt.endswith("каждый день")
    if daily:
        txt = txt[:-len("каждый день")].strip()
    if not re.match(r"^\d{2}:\d{2}$", t):
        bot.reply_to(msg, "❌ Формат времени: ЧЧ:ММ")
        return
    db.add_reminder(msg.from_user.id, txt, t, daily=daily)
    bot.reply_to(msg, f"✅ Напоминание:\n⏰ {t}{' 🔁' if daily else ''}\n📝 {txt}")


@bot.message_handler(commands=["weather"])
def cmd_weather(msg):
    p = msg.text.split(maxsplit=1)
    if len(p) < 2:
        bot.reply_to(msg, "🌤 Напиши город: /weather Москва")
        return
    bot.send_chat_action(msg.chat.id, "typing")
    bot.reply_to(msg, get_weather(p[1]))


# ── ГОЛОСОВЫЕ — С RETRY ──
@bot.message_handler(content_types=["voice"])
def handle_voice(msg):
    uid = msg.from_user.id
    if not check_and_count(msg):
        return
    bot.send_chat_action(msg.chat.id, "typing")
    wait = bot.reply_to(msg, "🎤 Слушаю...")
    try:
        fi = bot.get_file(msg.voice.file_id)
        audio = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{fi.file_path}", timeout=20).content
        text = transcribe_voice(audio)
        try:
            bot.delete_message(uid, wait.message_id)
        except:
            pass
        if modes.get(uid) == "note_voice_mode":
            modes[uid] = "normal"
            db.add_note(uid, text)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📓 Дневник", callback_data="btn_notes"),
                   types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
            bot.send_message(uid, f"📓 Заметка сохранена!\n\n{text}", reply_markup=kb)
            return
        bot.send_message(uid, f"🎤 Ты сказала:\n{text}")
        bot.send_chat_action(uid, "typing")
        answer = ask_ai(uid, text)
        last_answer[uid] = answer
        send_safe(uid, answer, kb=after_kb())
    except Exception as e:
        log_event(f"Voice error: {e}")
        try:
            bot.delete_message(uid, wait.message_id)
        except:
            pass
        bot.reply_to(msg, "😔 Не смогла расшифровать. Попробуй ещё раз или напиши текстом!")


# ── ФОТО — С GEMINI FALLBACK ──
@bot.message_handler(content_types=["photo"])
def handle_photo(msg):
    uid = msg.from_user.id
    if not check_and_count(msg):
        return
    bot.send_chat_action(msg.chat.id, "typing")
    wait = None
    try:
        photos = msg.photo
        photo = photos[-2] if len(photos) >= 2 else photos[-1]
        fi = bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{fi.file_path}"
        resp = requests.get(file_url, timeout=30)
        if resp.status_code != 200:
            bot.reply_to(msg, "😔 Не смогла скачать фото. Попробуй ещё раз!")
            return
        image_b64 = base64.b64encode(resp.content).decode("utf-8")
        wait = bot.send_message(uid, "📸 Анализирую фото... ⏳")
        caption = msg.caption or "Внимательно посмотри на фото. Если есть задачи, уравнения, текст — реши пошагово. Пиши без LaTeX."
        answer = ask_ai(uid, caption, image_b64=image_b64)
        last_answer[uid] = answer
        try:
            bot.delete_message(uid, wait.message_id)
        except:
            pass
        send_safe(uid, answer, reply_to=msg, kb=after_kb())
    except Exception as e:
        log_event(f"Photo error: {e}")
        try:
            if wait:
                bot.delete_message(uid, wait.message_id)
        except:
            pass
        bot.reply_to(msg, "😔 Не смогла обработать фото. Напиши задачу текстом — обязательно помогу!")


# ══════════════════════════════════════════════
# НОВОЕ: ОБРАБОТКА PDF ФАЙЛОВ
# ══════════════════════════════════════════════
@bot.message_handler(content_types=["document"])
def handle_document(msg):
    uid = msg.from_user.id
    if not check_and_count(msg):
        return

    doc = msg.document
    file_name = doc.file_name or ""
    mime_type = doc.mime_type or ""

    # Проверяем что это PDF
    if not (file_name.lower().endswith(".pdf") or mime_type == "application/pdf"):
        bot.reply_to(msg, "📄 Я умею анализировать PDF файлы!\n\nОтправь .pdf документ и я его разберу 🌸")
        return

    # Проверяем размер (макс 20MB)
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(msg, "😔 Файл слишком большой (максимум 20 МБ). Попробуй сжать PDF.")
        return

    bot.send_chat_action(msg.chat.id, "typing")
    wait = bot.reply_to(msg, "📄 Читаю документ... ⏳\n\nЭто может занять 10-30 секунд")

    try:
        fi = bot.get_file(doc.file_id)
        pdf_bytes = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{fi.file_path}", timeout=60).content

        question = msg.caption or ""
        answer = analyze_pdf_bytes(pdf_bytes, question)

        try:
            bot.delete_message(uid, wait.message_id)
        except:
            pass

        # Добавляем кнопки
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("❓ Задать вопрос по документу", callback_data="btn_ask_doc"),
            types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"),
        )
        send_safe(uid, f"📄 Анализ документа:\n\n{answer}", reply_to=msg, kb=kb)

        # Сохраняем что пользователь работает с документом
        modes[uid] = "doc_mode"
        last_answer[uid] = answer

    except Exception as e:
        log_event(f"PDF error: {e}")
        try:
            bot.delete_message(uid, wait.message_id)
        except:
            pass
        bot.reply_to(msg,
                     "😔 Не смогла обработать PDF.\n\n"
                     "Возможные причины:\n"
                     "• Документ защищён паролем\n"
                     "• Только сканированные изображения (нет текста)\n"
                     "• Нужен GEMINI_KEY для лучшей обработки\n\n"
                     "Попробуй скопировать текст и отправить мне! 🌸")


# ── ПЛАТЕЖИ ──
@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q):
    bot.answer_pre_checkout_query(q.id, ok=True)


@bot.message_handler(content_types=["successful_payment"])
def successful_payment(msg):
    uid = msg.from_user.id
    u = msg.from_user.username or ""
    stars = msg.successful_payment.total_amount
    if stars >= 500:
        exp = None; plan = "forever"; days_text = "навсегда"
    elif stars >= 200:
        exp = datetime.now() + timedelta(days=90); plan = "90days"; days_text = "90 дней"
    else:
        exp = datetime.now() + timedelta(days=30); plan = "30days"; days_text = "30 дней"
    db.set_user(uid, exp, plan=plan)
    db.unblock(uid)
    bot.send_message(uid,
                     f"🎉 Оплата прошла!\n\n⭐ {stars} Stars\n✅ Подписка: {days_text}\n\nВсе функции открыты! 🌸",
                     reply_markup=main_menu_kb(u))
    notify_admin(f"💳 Оплата! {uid} @{u} {stars} Stars {plan}")


# ── ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ГЕНЕРАЦИИ КАРТИНОК ──
def _gen_img(chat_id, uid, prompt):
    wait = bot.send_message(chat_id, "🎨 Рисую... ⏳ (~30 сек)")
    try:
        img_data = generate_image(prompt)
        try:
            bot.delete_message(chat_id, wait.message_id)
        except:
            pass
        if img_data:
            kb = types.InlineKeyboardMarkup(row_width=2)
            short_p = prompt[:40] if len(prompt) > 40 else prompt
            kb.add(
                types.InlineKeyboardButton("🔄 Ещё вариант", callback_data=f"imagine_again_{short_p}"),
                types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"),
            )
            bot.send_photo(chat_id, img_data, caption=f"🎨 {prompt[:100]}", reply_markup=kb)
        else:
            bot.send_message(chat_id, "😔 Не получилось нарисовать. Попробуй другое описание!",
                             reply_markup=types.InlineKeyboardMarkup().add(
                                 types.InlineKeyboardButton("🔄 Попробовать снова", callback_data="btn_imagine")))
    except Exception as e:
        log_event(f"Image gen error: {e}")
        try:
            bot.delete_message(chat_id, wait.message_id)
        except:
            pass
        bot.send_message(chat_id, "😔 Ошибка генерации. Попробуй ещё раз!")


# ── CALLBACKS ──
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    uid = call.from_user.id
    data = call.data
    u = call.from_user.username or ""

    FREE_CB = {"btn_menu", "btn_help", "btn_trial", "btn_pay_stars",
               "pay_stars_30", "pay_stars_90", "pay_stars_forever",
               "btn_account", "adm_panel", "adm_back"}

    if data not in FREE_CB and not data.startswith("adm_") and not check_access_cb(call):
        return

    # ── ПРОБНЫЙ ПЕРИОД ──
    if data == "btn_trial":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        if db.get_user(uid):
            bot.send_message(uid, f"У тебя уже есть подписка!\nСтатус: {db.sub_status(uid)}")
            return
        exp = datetime.now() + timedelta(days=TRIAL_DAYS)
        db.set_user(uid, exp, plan="trial")
        notify_admin(f"🎁 Пробный: {uid} @{u}")
        bot.send_message(uid,
                         f"🎁 Пробный период активирован!\n\n✅ {TRIAL_DAYS} дня бесплатно\n"
                         f"📊 Лимит: {FREE_DAILY_LIMIT} сообщений/день\n⏰ До: {exp.strftime('%d.%m.%Y')}\n\n"
                         f"Теперь все функции доступны! 🌸", reply_markup=main_menu_kb(u))
        return

    # ── ОПЛАТА ──
    if data == "btn_pay_stars":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(f"⭐ {PRICE_STARS} Stars — 30 дней", callback_data="pay_stars_30"),
            types.InlineKeyboardButton("⭐ 200 Stars — 90 дней", callback_data="pay_stars_90"),
            types.InlineKeyboardButton("⭐ 500 Stars — Навсегда", callback_data="pay_stars_forever"),
            types.InlineKeyboardButton("💳 Написать @tronqx", url=PAYMENT_LINK),
        )
        bot.send_message(uid, "⭐ Выбери план:", reply_markup=kb)
        return

    if data.startswith("pay_stars_"):
        plan_key = data.replace("pay_stars_", "")
        plans = {"30": (PRICE_STARS, "30 дней"), "90": (200, "90 дней"), "forever": (500, "Навсегда")}
        if plan_key not in plans:
            bot.answer_callback_query(call.id)
            return
        amount, label = plans[plan_key]
        bot.answer_callback_query(call.id)
        try:
            bot.send_invoice(
                chat_id=uid, title=f"Лия — {label}",
                description=f"Полный доступ к боту на {label}",
                invoice_payload=f"stars_{plan_key}_{uid}",
                provider_token="", currency="XTR",
                prices=[types.LabeledPrice(label=label, amount=amount)]
            )
        except Exception as e:
            log_event(f"Invoice error: {e}")
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("💬 Написать @tronqx", url=PAYMENT_LINK))
            bot.send_message(uid, f"⭐ {label} — {amount} Stars\n\nНапиши @tronqx: «Подписка {label}, ID: {uid}»",
                             reply_markup=kb)
        return

    # ── АККАУНТ ──
    if data == "btn_account":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        st = db.r.get(f"stats:{uid}") or {}
        ref = db.r.get(f"referral:{uid}") or {}
        code = db.get_ref_code(uid)
        link = f"https://t.me/{bot.get_me().username}?start={code}"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("💳 Подписка", callback_data="btn_pay_stars"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid,
                         f"📱 Мой аккаунт\n\n🆔 ID: {uid}\n📅 С: {st.get('joined', '?')[:10]}\n"
                         f"💎 Статус: {db.sub_status(uid)}\n✉️ Сообщений сегодня: {db.get_daily_count(uid)}\n"
                         f"✉️ Всего: {st.get('total_msgs', 0)}\n\n🔗 Реф. ссылка:\n{link}\n"
                         f"👥 Приглашено: {len(ref.get('invited', []))}",
                         reply_markup=kb)
        return

    # ── МЕНЮ ──
    if data == "btn_menu":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        modes[uid] = "normal"  # Сбрасываем режим при возврате в меню
        bot.send_message(uid, f"Меню 🌸 | Режим: {mode_name(uid)}", reply_markup=main_menu_kb(u))
        return

    if data == "btn_new":
        histories[uid] = []
        modes[uid] = "normal"
        bot.answer_callback_query(call.id, "🔄 Очищено!")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        bot.send_message(uid, "🔄 Новый диалог! Пиши что угодно 🌸")
        return

    if data == "btn_help":
        bot.answer_callback_query(call.id)
        bot.send_message(uid,
                         "ℹ️ Помощь:\n\n"
                         "💬 Пиши любое сообщение\n"
                         "📸 Отправь фото — решу задачу\n"
                         "📄 Отправь PDF — проанализирую документ\n"
                         "🎤 Запиши голосовое — расшифрую\n\n"
                         "Команды:\n"
                         "/start — перезапуск\n"
                         "/new — новый диалог\n"
                         "/remind 18:00 текст — напоминание\n"
                         "/weather Москва — погода\n"
                         "/myid — твой ID",
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        return

    # ── ПОДСКАЗКА PDF ──
    if data == "btn_pdf_hint":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        bot.send_message(uid,
                         "📄 Анализ PDF документов!\n\n"
                         "Просто отправь мне PDF файл и я:\n\n"
                         "• 📋 Расскажу о чём документ\n"
                         "• 🔑 Выделю ключевые моменты\n"
                         "• 📊 Найду важные данные и цифры\n"
                         "• 💡 Сделаю выводы\n\n"
                         "Можешь добавить подпись к файлу с вопросом — отвечу конкретно!\n\n"
                         "Пример: отправь договор с вопросом «на что обратить внимание?» 🌸",
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        return

    # ── ВОПРОС ПО ДОКУМЕНТУ ──
    if data == "btn_ask_doc":
        bot.answer_callback_query(call.id)
        modes[uid] = "doc_question"
        bot.send_message(uid, "❓ Задай вопрос по документу:\n\nЧто тебя интересует? Я отвечу на основе загруженного файла.")
        return

    # ── РЕЖИМЫ ──
    if data.startswith("mode_"):
        m = data.replace("mode_", "")
        modes[uid] = m
        histories[uid] = []
        names = {"normal": "💬 Пиши что угодно!", "study": "📚 Помогу с учёбой!",
                 "support": "🤗 Я здесь 💕", "creative": "🎨 Придумаем что-нибудь! ✨"}
        bot.answer_callback_query(call.id, "Режим изменён!")
        bot.send_message(uid, names.get(m, "Режим изменён!"))
        return

    # ── КАРТИНКИ ──
    if data == "btn_imagine":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🌅 Закат у моря", callback_data="imagine_q_sunset at sea golden hour"),
            types.InlineKeyboardButton("🌸 Аниме девушка", callback_data="imagine_q_beautiful anime girl sakura"),
            types.InlineKeyboardButton("🏙 Ночной город", callback_data="imagine_q_cyberpunk night city neon"),
            types.InlineKeyboardButton("🐱 Котик", callback_data="imagine_q_cute fluffy cat adorable"),
            types.InlineKeyboardButton("🌺 Цветочный сад", callback_data="imagine_q_magical flower garden fantasy"),
            types.InlineKeyboardButton("✍️ Своё описание", callback_data="imagine_custom"),
        )
        bot.send_message(uid, "🖼 Выбери стиль или опиши своё:", reply_markup=kb)
        return

    if data == "imagine_custom":
        bot.answer_callback_query(call.id)
        modes[uid] = "imagine_mode"
        bot.send_message(uid,
                         "✍️ Опиши что нарисовать (на русском):\n\n"
                         "Примеры:\n"
                         "• красивая девушка в кафе, уютно, осень\n"
                         "• волшебный лес с феями, ночь, огоньки\n"
                         "• котик в космосе, акварель")
        return

    if data.startswith("imagine_q_"):
        prompt = data.replace("imagine_q_", "")
        bot.answer_callback_query(call.id, "🎨 Рисую...")
        _gen_img(call.message.chat.id, uid, prompt)
        return

    if data.startswith("imagine_again_"):
        prompt = data.replace("imagine_again_", "")
        bot.answer_callback_query(call.id, "🔄 Рисую новый вариант...")
        _gen_img(call.message.chat.id, uid, prompt)
        return

    # ── ПОДСКАЗКА ФОТО ──
    if data == "btn_photo_hint":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        bot.send_message(uid,
                         "📸 Анализ фото!\n\nПросто отправь фото и я:\n\n"
                         "• Решу задачи и уравнения с фото\n"
                         "• Прочитаю текст\n"
                         "• Объясню что на картинке\n"
                         "• Помогу с домашним заданием\n\n"
                         "Можешь добавить вопрос в подписи к фото 🌸",
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        return

    # ── ГОЛОС ПОСЛЕДНЕГО ОТВЕТА ──
    if data == "btn_voice_last":
        bot.answer_callback_query(call.id)
        answer = last_answer.get(uid, "")
        if not answer:
            bot.send_message(uid, "😔 Нет текста для озвучки")
            return
        if not VOICE_ENABLED:
            bot.send_message(uid, "😔 Голос временно недоступен")
            return
        try:
            bot.send_chat_action(uid, "record_audio")
            tts = gTTS(text=answer[:500], lang='ru')
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                tts.save(f.name)
                with open(f.name, 'rb') as audio:
                    bot.send_voice(uid, audio)
            os.unlink(f.name)
        except Exception as e:
            log_event(f"TTS error: {e}")
            bot.send_message(uid, "😔 Не смогла озвучить")
        return

    # ── ОБЪЯСНЕНИЕ ПРОЩЕ ──
    if data == "btn_explain_simple":
        bot.answer_callback_query(call.id)
        answer = last_answer.get(uid, "")
        if not answer:
            bot.send_message(uid, "😔 Нет текста для упрощения")
            return
        bot.send_chat_action(uid, "typing")
        simple = ask_ai(uid, f"Объясни это максимально просто, как будто объясняешь ребёнку или новичку: {answer[:1000]}",
                        custom_system=SYSTEM_PROMPT)
        send_safe(uid, simple, kb=after_kb())
        return

    # ── ПОДРОБНЕЕ ──
    if data == "btn_elaborate":
        bot.answer_callback_query(call.id)
        answer = last_answer.get(uid, "")
        if not answer:
            bot.send_message(uid, "😔 Нет текста для расширения")
            return
        bot.send_chat_action(uid, "typing")
        elaborate = ask_ai(uid, "Расскажи подробнее об этом, добавь примеры и детали", custom_system=mode_system(uid))
        send_safe(uid, elaborate, kb=after_kb())
        return

    # ── СОХРАНИТЬ В ДНЕВНИК ──
    if data == "btn_save_note":
        bot.answer_callback_query(call.id, "📓 Сохранено!")
        answer = last_answer.get(uid, "")
        if answer:
            db.add_note(uid, answer[:500])
            bot.send_message(uid, "📓 Сохранено в дневник! ✨",
                             reply_markup=types.InlineKeyboardMarkup().add(
                                 types.InlineKeyboardButton("📓 Открыть дневник", callback_data="btn_notes"),
                                 types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        return

    # ── ГОРОСКОП ──
    if data == "btn_horoscope":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        kb = types.InlineKeyboardMarkup(row_width=3)
        for sign in ZODIAC_SIGNS:
            kb.add(types.InlineKeyboardButton(sign, callback_data=f"horoscope_{sign}"))
        kb.add(types.InlineKeyboardButton("🔙 Меню", callback_data="btn_menu"))
        bot.send_message(uid, "🌙 Выбери свой знак зодиака:", reply_markup=kb)
        return

    if data.startswith("horoscope_"):
        sign = data.replace("horoscope_", "")
        bot.answer_callback_query(call.id, "🔮 Составляю...")
        bot.send_chat_action(uid, "typing")
        try:
            today = datetime.now().strftime("%d %B %Y")
            answer = ask_ai(uid,
                            f"Составь подробный гороскоп для знака {sign} на {today}. "
                            f"Включи: общее, любовь, работу, здоровье, совет дня. "
                            f"Пиши позитивно, вдохновляюще, с эмодзи.",
                            custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, f"🌙 Гороскоп {sign}\n\n{answer}", kb=after_kb())
        except Exception as e:
            log_event(f"Horoscope error: {e}")
            bot.send_message(uid, "😔 Не смогла составить гороскоп. Попробуй позже!")
        return

    # ── КРАСОТА И УХОД ──
    if data == "btn_beauty":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("💄 Уход за кожей", callback_data="beauty_skin"),
            types.InlineKeyboardButton("💅 Маникюр дома", callback_data="beauty_nails"),
            types.InlineKeyboardButton("💇 Уход за волосами", callback_data="beauty_hair"),
            types.InlineKeyboardButton("🏋️ Тренировка дома", callback_data="beauty_workout"),
            types.InlineKeyboardButton("🥗 Здоровое питание", callback_data="beauty_nutrition"),
            types.InlineKeyboardButton("😴 Уход за собой", callback_data="beauty_selfcare"),
            types.InlineKeyboardButton("🔙 Меню", callback_data="btn_menu"),
        )
        bot.send_message(uid, "💄 Уход за собой — выбери тему:", reply_markup=kb)
        return

    if data.startswith("beauty_"):
        topic = data.replace("beauty_", "")
        topics = {
            "skin": "уход за кожей лица: очищение, увлажнение, тонизирование, советы для разных типов кожи",
            "nails": "маникюр в домашних условиях: пошаговая инструкция, уход за ногтями",
            "hair": "уход за волосами: маски, питание, защита, советы по типу волос",
            "workout": "эффективная тренировка дома без оборудования на 20-30 минут",
            "nutrition": "здоровое питание: основные принципы, что есть для красоты и энергии",
            "selfcare": "ритуалы самозаботы: вечерний ритуал, психологическое здоровье"
        }
        if topic in topics:
            bot.answer_callback_query(call.id, "💄 Готовлю советы...")
            bot.send_chat_action(uid, "typing")
            try:
                answer = ask_ai(uid, f"Дай подробные советы и рекомендации по теме: {topics[topic]}",
                                custom_system=SYSTEM_PROMPT)
                last_answer[uid] = answer
                send_safe(uid, answer, kb=after_kb())
            except Exception as e:
                log_event(f"Beauty error: {e}")
                bot.send_message(uid, "😔 Ошибка. Попробуй ещё раз!")
        return

    # ── ЛЮБОВНОЕ ПИСЬМО ──
    if data == "btn_love":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        modes[uid] = "love_letter"
        bot.send_message(uid, "💌 Напиши кому письмо и немного о ваших отношениях:\n\nНапример: «Письмо парню Максиму, встречаемся 2 года, люблю его юмор и заботу»")
        return

    # ── ПЕРЕСКАЗ ТЕКСТА ──
    if data == "btn_summarize":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        modes[uid] = "summarize_mode"
        bot.send_message(uid,
                         "📖 Пересказ текста!\n\nОтправь мне любой текст — статью, главу книги, новость — и я сделаю краткий пересказ с ключевыми идеями 🌸")
        return

    # ── ПЕРЕВОДЧИК ──
    if data == "btn_translate":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        kb = types.InlineKeyboardMarkup(row_width=2)
        for lang_name, lang_code in LANGUAGES.items():
            kb.add(types.InlineKeyboardButton(lang_name, callback_data=f"translate_{lang_code}"))
        kb.add(types.InlineKeyboardButton("🔙 Меню", callback_data="btn_menu"))
        bot.send_message(uid, "🌍 Выбери язык перевода:", reply_markup=kb)
        return

    if data.startswith("translate_"):
        lang = data.replace("translate_", "")
        modes[uid] = f"translate_{lang}"
        bot.answer_callback_query(call.id)
        bot.send_message(uid, f"✅ Переводчик на {lang} активирован!\n\nОтправь текст для перевода:")
        return

    # ── ВАЛЮТА ──
    if data == "btn_currency":
        bot.answer_callback_query(call.id, "💰 Загружаю курс...")
        bot.send_chat_action(uid, "typing")
        bot.send_message(uid, get_currency(),
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        return

    # ── ПОГОДА ──
    if data == "btn_weather":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        modes[uid] = "weather_mode"
        bot.send_message(uid, "🌤 Напиши название города:")
        return

    # ── РЕЦЕПТ ──
    if data == "btn_recipe":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        modes[uid] = "recipe_mode"
        bot.send_message(uid,
                         "🍽 Рецепт!\n\nНапиши что хочешь приготовить или какие продукты есть в холодильнике:\n\nНапример: «Курица, картошка, лук» или «Что-нибудь быстрое и вкусное»")
        return

    # ── МЕДИТАЦИЯ ──
    if data == "btn_meditation":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        kb = types.InlineKeyboardMarkup(row_width=1)
        for i, med in enumerate(MEDITATIONS):
            kb.add(types.InlineKeyboardButton(med["name"], callback_data=f"meditation_{i}"))
        kb.add(types.InlineKeyboardButton("🎲 Случайная", callback_data="meditation_random"))
        kb.add(types.InlineKeyboardButton("🔙 Меню", callback_data="btn_menu"))
        bot.send_message(uid, "🧘 Выбери медитацию:", reply_markup=kb)
        return

    if data.startswith("meditation_"):
        idx = data.replace("meditation_", "")
        bot.answer_callback_query(call.id)
        if idx == "random":
            med = random.choice(MEDITATIONS)
        else:
            try:
                med = MEDITATIONS[int(idx)]
            except:
                med = random.choice(MEDITATIONS)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🧘 Ещё медитация", callback_data="btn_meditation"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid, f"{med['name']}\n\n{med['text']}", reply_markup=kb)
        return

    # ── НАСТРОЕНИЕ ──
    if data == "btn_mood":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        kb = types.InlineKeyboardMarkup(row_width=4)
        for emoji, name in MOOD_EMOJIS.items():
            kb.add(types.InlineKeyboardButton(f"{emoji} {name}", callback_data=f"mood_{emoji}"))
        kb.add(types.InlineKeyboardButton("📊 История", callback_data="mood_history"))
        kb.add(types.InlineKeyboardButton("🔙 Меню", callback_data="btn_menu"))
        bot.send_message(uid, "📊 Как ты себя чувствуешь сейчас?", reply_markup=kb)
        return

    if data.startswith("mood_") and data != "mood_history":
        emoji = data.replace("mood_", "")
        mood_name = MOOD_EMOJIS.get(emoji, "")
        today = datetime.now().strftime("%Y-%m-%d %H:%M")
        if uid not in mood_log:
            mood_log[uid] = []
        mood_log[uid].append({"emoji": emoji, "name": mood_name, "time": today})
        bot.answer_callback_query(call.id, f"Записала {emoji}")
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid,
                            f"Пользователь отметил настроение: {emoji} {mood_name}. "
                            f"Отреагируй коротко (2-3 предложения), поддержи или порадуйся вместе.",
                            custom_system=SYSTEM_PROMPT)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📊 Настроение", callback_data="btn_mood"),
                   types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
            bot.send_message(uid, answer, reply_markup=kb)
        except:
            bot.send_message(uid, f"Записала твоё настроение {emoji} {mood_name} ✨")
        return

    if data == "mood_history":
        bot.answer_callback_query(call.id)
        log = mood_log.get(uid, [])
        if not log:
            bot.send_message(uid, "😔 История настроений пока пуста.")
            return
        text = "📊 История настроений:\n\n"
        for entry in log[-10:]:
            text += f"{entry['emoji']} {entry['name']} — {entry['time']}\n"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid, text, reply_markup=kb)
        return

    # ── ПЛАНИРОВЩИК ──
    if data == "btn_planner":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        modes[uid] = "planner_mode"
        bot.send_message(uid,
                         "🗓 Планировщик!\n\nОпиши свои задачи или цели на день/неделю, и я помогу составить план:\n\nНапример: «Нужно сдать проект, позвонить врачу, убраться дома, сходить в спортзал»")
        return

    # ── СПИСОК ДЕЛ ──
    if data == "btn_todo":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        todo = todo_list.get(uid, [])
        if todo:
            text = "📝 Список дел:\n\n"
            for i, item in enumerate(todo, 1):
                status = "✅" if item.get("done") else "⬜"
                text += f"{status} {i}. {item['text']}\n"
        else:
            text = "📝 Список дел пуст!\n\nНапиши задачу чтобы добавить:"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("➕ Добавить", callback_data="todo_add"),
            types.InlineKeyboardButton("✅ Отметить", callback_data="todo_done"),
            types.InlineKeyboardButton("🗑 Очистить", callback_data="todo_clear"),
            types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"),
        )
        bot.send_message(uid, text, reply_markup=kb)
        return

    if data == "todo_add":
        bot.answer_callback_query(call.id)
        modes[uid] = "todo_add"
        bot.send_message(uid, "➕ Напиши задачу:")
        return

    if data == "todo_done":
        bot.answer_callback_query(call.id)
        modes[uid] = "todo_done"
        bot.send_message(uid, "✅ Напиши номер задачи которую выполнила:")
        return

    if data == "todo_clear":
        bot.answer_callback_query(call.id, "🗑 Очищено!")
        todo_list[uid] = []
        bot.send_message(uid, "✅ Список очищен!",
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        return

    # ── НАПОМИНАНИЯ ──
    if data == "btn_reminders":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        rems = db.get_reminders(uid)
        if rems:
            text = "⏰ Твои напоминания:\n\n"
            for i, r in enumerate(rems, 1):
                d = "🔁 " if r.get("daily") else ""
                text += f"{i}. {d}{r['time']} — {r['text']}\n"
        else:
            text = "⏰ Напоминаний нет!\n\nИспользуй: /remind 18:00 выпить воду"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🗑 Удалить", callback_data="reminder_delete"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid, text, reply_markup=kb)
        return

    if data == "reminder_delete":
        bot.answer_callback_query(call.id)
        modes[uid] = "reminder_delete"
        bot.send_message(uid, "🗑 Напиши номер напоминания для удаления:")
        return

    # ── ДНЕВНИК ──
    if data == "btn_notes":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        notes = db.get_notes(uid)
        if notes:
            text = "📓 Дневник:\n\n"
            for note in notes[-5:]:
                date = note.get("date", "")[:10]
                text += f"📅 {date}\n{note['text'][:200]}{'...' if len(note['text']) > 200 else ''}\n\n"
        else:
            text = "📓 Дневник пуст!\n\nЗаметки сохраняются автоматически когда ты нажимаешь «В дневник» после ответов."
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✍️ Добавить заметку", callback_data="note_add"),
            types.InlineKeyboardButton("🎤 Голосовая заметка", callback_data="note_voice"),
            types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"),
        )
        bot.send_message(uid, text, reply_markup=kb)
        return

    if data == "note_add":
        bot.answer_callback_query(call.id)
        modes[uid] = "note_mode"
        bot.send_message(uid, "✍️ Напиши заметку:")
        return

    if data == "note_voice":
        bot.answer_callback_query(call.id)
        modes[uid] = "note_voice_mode"
        bot.send_message(uid, "🎤 Отправь голосовое сообщение — сохраню как заметку!")
        return

    # ── ПАМЯТЬ ──
    if data == "btn_memory":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        mem = db.get_memory(uid)
        if mem:
            text = "🧠 Что я о тебе помню:\n\n"
            for k, v in mem.items():
                text += f"• {k}: {v}\n"
        else:
            text = "🧠 Я пока ничего не запомнила о тебе!\n\nРасскажи о себе — я запомню."
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✏️ Имя", callback_data="mem_name"),
            types.InlineKeyboardButton("🎂 Возраст", callback_data="mem_age"),
            types.InlineKeyboardButton("🏙 Город", callback_data="mem_city"),
            types.InlineKeyboardButton("💕 Интересы", callback_data="mem_interests"),
            types.InlineKeyboardButton("📝 О себе", callback_data="mem_about"),
            types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"),
        )
        bot.send_message(uid, text, reply_markup=kb)
        return

    if data.startswith("mem_"):
        field = data.replace("mem_", "")
        bot.answer_callback_query(call.id)
        modes[uid] = f"memory_{field}"
        prompts = {"name": "Как тебя зовут?", "age": "Сколько тебе лет?",
                   "city": "В каком городе живёшь?", "interests": "Какие у тебя интересы и хобби?",
                   "about": "Расскажи немного о себе:"}
        bot.send_message(uid, prompts.get(field, "Напиши:"))
        return

    # ── РЕФЕРАЛЬНАЯ ПРОГРАММА ──
    if data == "btn_referral":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        code = db.get_ref_code(uid)
        link = f"https://t.me/{bot.get_me().username}?start={code}"
        ref = db.r.get(f"referral:{uid}") or {}
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid,
                         f"🔗 Реферальная программа!\n\n"
                         f"Поделись ссылкой — получи +7 дней за каждого друга:\n\n"
                         f"{link}\n\n"
                         f"👥 Приглашено: {len(ref.get('invited', []))}\n"
                         f"🎁 Бонусных дней: {ref.get('bonus_days', 0)}",
                         reply_markup=kb)
        return

    # ── РАЗВЛЕЧЕНия ──
    if data == "btn_joke":
        bot.answer_callback_query(call.id, "😄 Придумываю...")
        bot.send_chat_action(uid, "typing")
        try:
            joke = ask_ai(uid, "Расскажи смешной анекдот или шутку. Только один, короткий и весёлый.",
                          custom_system=SYSTEM_PROMPT)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("😂 Ещё шутку", callback_data="btn_joke"),
                   types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
            bot.send_message(uid, joke, reply_markup=kb)
        except:
            bot.send_message(uid, "😔 Не смогла придумать шутку. Попробуй ещё раз!")
        return

    if data == "btn_fact":
        bot.answer_callback_query(call.id, "🌟 Ищу факт...")
        bot.send_chat_action(uid, "typing")
        try:
            fact = ask_ai(uid,
                          "Расскажи один интересный и неожиданный факт о мире. Сделай его захватывающим!",
                          custom_system=SYSTEM_PROMPT)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🌟 Ещё факт", callback_data="btn_fact"),
                   types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
            bot.send_message(uid, fact, reply_markup=kb)
        except:
            bot.send_message(uid, "😔 Ошибка. Попробуй ещё раз!")
        return

    if data == "btn_motivation":
        bot.answer_callback_query(call.id, "💪 Вдохновляю...")
        bot.send_chat_action(uid, "typing")
        try:
            mot = ask_ai(uid,
                         "Дай мощную мотивационную речь на 3-5 предложений. Искреннюю, не банальную.",
                         custom_system=SYSTEM_PROMPT)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("💪 Ещё мотивацию", callback_data="btn_motivation"),
                   types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
            bot.send_message(uid, mot, reply_markup=kb)
        except:
            bot.send_message(uid, "😔 Ошибка. Попробуй ещё раз!")
        return

    if data == "btn_compliment":
        bot.answer_callback_query(call.id)
        compliment = random.choice(COMPLIMENTS)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✨ Ещё комплимент", callback_data="btn_compliment"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid, compliment, reply_markup=kb)
        return

    # ── ВИКТОРИНА ──
    if data == "btn_quiz":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        kb = types.InlineKeyboardMarkup(row_width=2)
        for topic_name in QUIZ_TOPICS.keys():
            kb.add(types.InlineKeyboardButton(topic_name, callback_data=f"quiz_start_{QUIZ_TOPICS[topic_name]}"))
        kb.add(types.InlineKeyboardButton("🔙 Меню", callback_data="btn_menu"))
        bot.send_message(uid, "🧠 Выбери тему викторины:", reply_markup=kb)
        return

    if data.startswith("quiz_start_"):
        topic = data.replace("quiz_start_", "")
        bot.answer_callback_query(call.id, "🧠 Готовлю вопрос...")
        bot.send_chat_action(uid, "typing")
        try:
            question_data = ask_ai(uid,
                                   f"Придумай вопрос для викторины по теме '{topic}'. "
                                   f"Формат ответа:\nВОПРОС: [вопрос]\nА) [вариант]\nБ) [вариант]\nВ) [вариант]\nГ) [вариант]\nОТВЕТ: [буква]",
                                   custom_system=SYSTEM_PROMPT)
            quiz_state[uid] = {"question": question_data, "topic": topic}
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("А", callback_data="quiz_ans_А"),
                types.InlineKeyboardButton("Б", callback_data="quiz_ans_Б"),
                types.InlineKeyboardButton("В", callback_data="quiz_ans_В"),
                types.InlineKeyboardButton("Г", callback_data="quiz_ans_Г"),
                types.InlineKeyboardButton("🔙 Другая тема", callback_data="btn_quiz"),
            )
            # Показываем только вопрос без ответа
            q_text = question_data.split("ОТВЕТ:")[0].strip() if "ОТВЕТ:" in question_data else question_data
            bot.send_message(uid, f"🧠 Вопрос:\n\n{q_text}", reply_markup=kb)
        except Exception as e:
            log_event(f"Quiz error: {e}")
            bot.send_message(uid, "😔 Ошибка генерации вопроса. Попробуй ещё раз!")
        return

    if data.startswith("quiz_ans_"):
        answer_letter = data.replace("quiz_ans_", "")
        bot.answer_callback_query(call.id)
        state = quiz_state.get(uid)
        if not state:
            bot.send_message(uid, "😔 Вопрос устарел. Начни викторину заново!")
            return
        q_data = state["question"]
        correct = ""
        if "ОТВЕТ:" in q_data:
            correct = q_data.split("ОТВЕТ:")[-1].strip()[:1].upper()
        if answer_letter == correct:
            result = f"✅ Правильно! Молодец! 🎉"
        else:
            result = f"❌ Неправильно. Правильный ответ: {correct}"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("▶️ Следующий вопрос", callback_data=f"quiz_start_{state['topic']}"),
            types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"),
        )
        bot.send_message(uid, result, reply_markup=kb)
        return

    # ── ВОПРОС ПО ДОКУМЕНТУ ──
    if data == "btn_ask_doc":
        bot.answer_callback_query(call.id)
        modes[uid] = "doc_question"
        bot.send_message(uid, "❓ Задай вопрос по документу:")
        return

    # ── АДМИН ──
    if data == "adm_panel":
        if not is_admin(u):
            bot.answer_callback_query(call.id, "⛔ Нет доступа")
            return
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        bot.send_message(uid, "👑 Админ-панель:", reply_markup=admin_kb())
        return

    if data == "adm_stats":
        if not is_admin(u):
            return
        bot.answer_callback_query(call.id)
        a = db.get_analytics()
        text = (f"📊 Аналитика:\n\n"
                f"👥 Всего: {a['total_users']}\n💎 Платных: {a['paid_users']}\n"
                f"🚫 Заблок: {a['blocked']}\n📅 DAU сегодня: {a['dau_today']}\n"
                f"📅 DAU вчера: {a['dau_yest']}\n🆕 Новых (неделя): {a['new_week']}\n"
                f"✉️ Всего сообщений: {a['total_msgs']}\n\n🏆 Топ-5:\n")
        for uid_s, st in a['top_users']:
            text += f"• @{st.get('username', uid_s)}: {st.get('total_msgs', 0)} сообщ.\n"
        bot.send_message(uid, text, reply_markup=admin_kb())
        return

    if data == "adm_grant":
        if not is_admin(u):
            return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_grant"
        bot.send_message(uid, "➕ Введи ID пользователя для бессрочного доступа:")
        return

    if data == "adm_grant_time":
        if not is_admin(u):
            return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_grant_time_wait"
        bot.send_message(uid, "🕐 Введи ID пользователя:")
        return

    if data == "adm_revoke":
        if not is_admin(u):
            return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_revoke"
        bot.send_message(uid, "❌ Введи ID для отзыва доступа:")
        return

    if data == "adm_block":
        if not is_admin(u):
            return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_block"
        bot.send_message(uid, "🚫 Введи ID для блокировки:")
        return

    if data == "adm_unblock":
        if not is_admin(u):
            return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_unblock"
        bot.send_message(uid, "✅ Введи ID для разблокировки:")
        return

    if data == "adm_broadcast":
        if not is_admin(u):
            return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_broadcast"
        bot.send_message(uid, "📢 Введи текст рассылки:")
        return

    if data == "adm_users":
        if not is_admin(u):
            return
        bot.answer_callback_query(call.id)
        all_uids = db.r.smembers("all_uids")
        paid = db.r.smembers("paid_uids")
        text = f"👥 Пользователи ({len(all_uids)}):\n\n"
        count = 0
        for uid_s in list(all_uids)[:20]:
            st = db.r.get(f"stats:{uid_s}") or {}
            paid_mark = "💎" if uid_s in paid else "👤"
            text += f"{paid_mark} {uid_s} @{st.get('username', '?')} | {st.get('total_msgs', 0)}msg\n"
            count += 1
        if len(all_uids) > 20:
            text += f"\n...и ещё {len(all_uids) - 20}"
        bot.send_message(uid, text, reply_markup=admin_kb())
        return

    if data.startswith("adm_time_"):
        if not is_admin(u):
            return
        parts = data.split("_")
        if len(parts) >= 4:
            target_uid = parts[2]
            days = int(parts[3]) if parts[3].isdigit() else 7
            try:
                t = int(target_uid)
                exp = datetime.now() + timedelta(days=days)
                db.set_user(t, exp, plan=f"{days}days")
                db.unblock(t)
                bot.answer_callback_query(call.id, f"✅ {days} дней выдано!")
                bot.send_message(uid, f"✅ Пользователю {t} выдано {days} дней до {exp.strftime('%d.%m.%Y')}",
                                 reply_markup=admin_kb())
                try:
                    bot.send_message(t, f"🎉 Тебе открыт доступ на {days} дней! /start 🌸")
                except:
                    pass
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Ошибка: {e}")
        return


# ── ТЕКСТОВЫЕ СООБЩЕНИЯ ──
@bot.message_handler(content_types=["text"])
def handle_text(msg):
    uid = msg.from_user.id
    u = msg.from_user.username or ""
    text = msg.text.strip()
    m = modes.get(uid, "normal")

    # Регистрируем пользователя
    db.register_user(uid, u, msg.from_user.first_name or "")

    # ── РЕЖИМЫ ВВОДА ──

    # Режим генерации картинки
    if m == "imagine_mode":
        if not check_and_count(msg):
            return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        _gen_img(msg.chat.id, uid, text)
        return

    # Режим погоды
    if m == "weather_mode":
        if not check_and_count(msg):
            return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        bot.reply_to(msg, get_weather(text),
                     reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        return

    # Режим рецепта
    if m == "recipe_mode":
        if not check_and_count(msg):
            return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Придумай вкусный рецепт с этими продуктами или по этому запросу: {text}. "
                                 f"Напиши: название блюда, ингредиенты, пошаговый рецепт, время приготовления.",
                            custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, reply_to=msg, kb=after_kb())
        except Exception as e:
            log_event(f"Recipe error: {e}")
            bot.reply_to(msg, "😔 Ошибка. Попробуй ещё раз!")
        return

    # Режим перевода
    if m.startswith("translate_"):
        if not check_and_count(msg):
            return
        lang = m.replace("translate_", "")
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Переведи на {lang}. Только перевод, без пояснений: {text}",
                            custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🌍 Другой язык", callback_data="btn_translate"),
                   types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
            send_safe(uid, answer, reply_to=msg, kb=kb)
        except Exception as e:
            log_event(f"Translate error: {e}")
            bot.reply_to(msg, "😔 Ошибка перевода. Попробуй ещё раз!")
        return

    # Режим пересказа
    if m == "summarize_mode":
        if not check_and_count(msg):
            return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Сделай краткий пересказ этого текста. "
                                 f"Выдели основные идеи, ключевые факты, главный вывод:\n\n{text}",
                            custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, reply_to=msg, kb=after_kb())
        except Exception as e:
            log_event(f"Summarize error: {e}")
            bot.reply_to(msg, "😔 Ошибка. Попробуй ещё раз!")
        return

    # Режим любовного письма
    if m == "love_letter":
        if not check_and_count(msg):
            return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Напиши красивое любовное письмо. Информация: {text}. "
                                 f"Письмо должно быть искренним, нежным, с эмоциями.",
                            custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, reply_to=msg, kb=after_kb())
        except Exception as e:
            log_event(f"Love letter error: {e}")
            bot.reply_to(msg, "😔 Ошибка. Попробуй ещё раз!")
        return

    # Режим заметки
    if m == "note_mode":
        modes[uid] = "normal"
        db.add_note(uid, text)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📓 Дневник", callback_data="btn_notes"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.reply_to(msg, "📓 Заметка сохранена! ✨", reply_markup=kb)
        return

    # Режим планировщика
    if m == "planner_mode":
        if not check_and_count(msg):
            return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Помоги составить план. Задачи/цели: {text}. "
                                 f"Расставь приоритеты, предложи порядок выполнения, дай временные рамки.",
                            custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, reply_to=msg, kb=after_kb())
        except Exception as e:
            log_event(f"Planner error: {e}")
            bot.reply_to(msg, "😔 Ошибка. Попробуй ещё раз!")
        return

    # Режим Todo: добавить
    if m == "todo_add":
        modes[uid] = "normal"
        if uid not in todo_list:
            todo_list[uid] = []
        todo_list[uid].append({"text": text, "done": False})
        bot.reply_to(msg, f"✅ Добавлено: {text}",
                     reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("📝 Список дел", callback_data="btn_todo"),
                         types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        return

    # Режим Todo: отметить выполненным
    if m == "todo_done":
        modes[uid] = "normal"
        try:
            idx = int(text) - 1
            todo = todo_list.get(uid, [])
            if 0 <= idx < len(todo):
                todo[idx]["done"] = True
                bot.reply_to(msg, f"✅ Отмечено: {todo[idx]['text']}",
                             reply_markup=types.InlineKeyboardMarkup().add(
                                 types.InlineKeyboardButton("📝 Список дел", callback_data="btn_todo")))
            else:
                bot.reply_to(msg, "❌ Неверный номер")
        except:
            bot.reply_to(msg, "❌ Напиши номер задачи (цифру)")
        return

    # Режим удаления напоминания
    if m == "reminder_delete":
        modes[uid] = "normal"
        try:
            idx = int(text) - 1
            db.remove_reminder(uid, idx)
            bot.reply_to(msg, "✅ Напоминание удалено!",
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("⏰ Напоминания", callback_data="btn_reminders"),
                             types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        except:
            bot.reply_to(msg, "❌ Неверный номер")
        return

    # Режим памяти
    if m.startswith("memory_"):
        field = m.replace("memory_", "")
        modes[uid] = "normal"
        db.save_memory(uid, field, text)
        bot.reply_to(msg, f"🧠 Запомнила: {field} = {text} ✨",
                     reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🧠 Память", callback_data="btn_memory"),
                         types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")))
        return

    # Режим вопроса по документу
    if m == "doc_question":
        if not check_and_count(msg):
            return
        bot.send_chat_action(uid, "typing")
        doc_context = last_answer.get(uid, "")
        try:
            answer = ask_ai(uid,
                            f"На основе этого документа:\n{doc_context[:2000]}\n\nОтветь на вопрос: {text}",
                            custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, reply_to=msg, kb=after_kb())
        except Exception as e:
            log_event(f"Doc question error: {e}")
            bot.reply_to(msg, "😔 Ошибка. Попробуй ещё раз!")
        return

    # ── ADMIN РЕЖИМЫ ──
    if m == "adm_grant" and is_admin(u):
        modes[uid] = "normal"
        try:
            t = int(text.strip())
            db.set_user(t, None, plan="forever")
            db.unblock(t)
            bot.reply_to(msg, f"✅ Бессрочная выдана {t}")
            try:
                bot.send_message(t, "🎉 Тебе выдан бессрочный доступ! /start 🌸")
            except:
                pass
        except:
            bot.reply_to(msg, "❌ Неверный ID")
        return

    if m == "adm_grant_time_wait" and is_admin(u):
        try:
            t = int(text.strip())
            modes[uid] = f"adm_time_{t}"
            bot.reply_to(msg, f"На сколько дней? Выбери:", reply_markup=time_kb(str(t)))
        except:
            modes[uid] = "normal"
            bot.reply_to(msg, "❌ Неверный ID")
        return

    if m == "adm_revoke" and is_admin(u):
        modes[uid] = "normal"
        try:
            t = int(text.strip())
            db.remove_user(t)
            bot.reply_to(msg, f"✅ Доступ отозван у {t}")
        except:
            bot.reply_to(msg, "❌ Неверный ID")
        return

    if m == "adm_block" and is_admin(u):
        modes[uid] = "normal"
        try:
            t = int(text.strip())
            db.block(t)
            bot.reply_to(msg, f"🚫 Пользователь {t} заблокирован")
        except:
            bot.reply_to(msg, "❌ Неверный ID")
        return

    if m == "adm_unblock" and is_admin(u):
        modes[uid] = "normal"
        try:
            t = int(text.strip())
            db.unblock(t)
            bot.reply_to(msg, f"✅ Пользователь {t} разблокирован")
        except:
            bot.reply_to(msg, "❌ Неверный ID")
        return

    if m == "adm_broadcast" and is_admin(u):
        modes[uid] = "normal"
        all_uids = db.r.smembers("all_uids")
        sent = failed = 0
        for target_uid in all_uids:
            try:
                bot.send_message(int(target_uid), f"📢 {text}")
                sent += 1
                time.sleep(0.05)
            except:
                failed += 1
        bot.reply_to(msg, f"📢 Рассылка: отправлено {sent}, ошибок {failed}", reply_markup=admin_kb())
        return

    # ── ОБЫЧНЫЙ ЧАТ ──
    if not check_and_count(msg):
        return

    bot.send_chat_action(uid, "typing")
    try:
        answer = ask_ai(uid, text)
        last_answer[uid] = answer
        send_safe(uid, answer, reply_to=msg, kb=after_kb())
    except Exception as e:
        log_event(f"Text error: {e}")
        bot.reply_to(msg,
                     "😔 Что-то пошло не так. Попробуй:\n• Написать ещё раз\n• /new — начать новый диалог\n• Подождать немного")


# ── ЗАПУСК ──
log_event("Liya v4.0 starting...")
print("🌸 Liya v4.0 запускается...")
bot.infinity_polling(timeout=60, long_polling_timeout=30)