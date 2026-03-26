import telebot, requests, json, random, base64, os, re, tempfile, threading, time
from typing import Optional
from telebot import types
from datetime import datetime, timedelta
from urllib.parse import quote
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── КОНФИГ ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CLAUDE_KEY       = os.environ.get("ANTHROPIC_API_KEY", "")  # ← НОВЫЙ
GROQ_KEY         = os.environ.get("GROQ_KEY", "")           # для Whisper + Vision
UPSTASH_URL      = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN    = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

BOT_NAME         = "Лия"
PRICE_STARS      = 100
PAYMENT_LINK     = "https://t.me/tronqx"
TRIAL_DAYS       = 3
FREE_MSG_LIMIT   = 5
FREE_DAILY_LIMIT = 20
LOG_FILE         = "/tmp/liya_log.txt"
VIP_USERNAMES    = {"tronqx", "dhl1929"}
ADMIN_USERNAMES  = {"tronqx"}

# ── МОДЕЛИ ──────────────────────────────────────────────
CLAUDE_MODEL  = "claude-haiku-4-5-20251001"                      # текст
MODEL_VISION  = "meta-llama/llama-4-scout-17b-16e-instruct"      # фото (Groq)
MODEL_WHISPER = "whisper-large-v3-turbo"                         # голос (Groq)

SYSTEM_PROMPT = """Ты — Лия, умный AI-помощник и подруга. Всегда отвечаешь по-русски.
Характер: тёплая, заботливая, умная, с лёгким юмором.

ПРАВИЛА ФОРМАТИРОВАНИЯ:
1. НИКОГДА не используй LaTeX: никаких $, $$, frac, sqrt и обратных слешей
2. Математику пиши обычным текстом: дроби = 1/2, корни = корень(4), степени = x^2
3. Греческие буквы пиши символами: α β π σ
4. Bullet-points через •
5. При решении задач каждый шаг на новой строке

ЧТО УМЕЕШЬ:
- Решаешь любые задачи точно: математика, физика, химия, история
- Пишешь и объясняешь код на любом языке
- Анализируешь фото и решаешь задачи с фото
- Переводишь на любые языки
- Помогаешь с учёбой, работой, жизнью"""


# ════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ════════════════════════════════════════════════════════

def log_event(text):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {text}\n")
    except Exception:
        pass


def clean_response(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'\$\$(.+?)\$\$', lambda m: m.group(1).strip(), text, flags=re.DOTALL)
    text = re.sub(r'\$(.+?)\$', lambda m: m.group(1).strip(), text)
    text = re.sub(r'\\[dc]?frac\{([^}]+)\}\{([^}]+)\}', r'\1/\2', text)
    text = re.sub(r'\\sqrt\{([^}]+)\}', r'корень(\1)', text)
    text = re.sub(r'\\sqrt', 'корень', text)
    text = re.sub(r'\\cdot', 'x', text)
    text = re.sub(r'\\left[\(\[]', '(', text)
    text = re.sub(r'\\right[\)\]]', ')', text)
    text = re.sub(r'\^\{([^}]+)\}', r'^\1', text)
    text = re.sub(r'\_\{([^}]+)\}', r'_\1', text)
    greek = {
        'alpha':'α','beta':'β','gamma':'γ','delta':'δ','epsilon':'ε',
        'theta':'θ','lambda':'λ','mu':'μ','pi':'π','sigma':'σ',
        'phi':'φ','omega':'ω','infty':'∞','pm':'±','times':'×','leq':'≤','geq':'≥','neq':'≠'
    }
    for eng, sym in greek.items():
        text = text.replace('\\'+eng+' ', sym+' ').replace('\\'+eng, sym)
    text = re.sub(r'\\[a-zA-Z]+\s?', '', text)
    text = re.sub(r'\{([^{}]*)\}', r'\1', text)
    text = re.sub(r'[{}]', '', text)
    text = re.sub(r'#{2,6}\s*', '', text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ════════════════════════════════════════════════════════
#  REDIS CLIENT
# ════════════════════════════════════════════════════════

class RedisClient:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    def _cmd(self, *args):
        for attempt in range(3):
            try:
                r = requests.post(self.url, headers=self.headers, json=list(args), timeout=8)
                if r.status_code == 200:
                    return r.json().get("result")
                log_event(f"Redis HTTP {r.status_code} attempt {attempt+1}")
            except Exception as e:
                log_event(f"Redis error attempt {attempt+1}: {e}")
            if attempt < 2:
                time.sleep(0.3)
        return None

    def get(self, key):
        raw = self._cmd("GET", key)
        if raw is None: return None
        try: return json.loads(raw)
        except: return raw

    def set(self, key, value):
        self._cmd("SET", key, json.dumps(value, ensure_ascii=False, default=str))

    def delete(self, key): self._cmd("DEL", key)
    def sadd(self, key, *members):
        for m in members: self._cmd("SADD", key, str(m))
    def srem(self, key, member): self._cmd("SREM", key, str(member))
    def smembers(self, key):
        result = self._cmd("SMEMBERS", key)
        return set(str(x) for x in result) if result else set()


# ════════════════════════════════════════════════════════
#  DATA STORE
# ════════════════════════════════════════════════════════

class DataStore:
    def __init__(self):
        self.r = RedisClient(UPSTASH_URL, UPSTASH_TOKEN)

    def get_user(self, uid): return self.r.get(f"user:{str(uid).strip()}")

    def set_user(self, uid, expires, plan="paid"):
        uid = str(uid).strip()
        exp_str = expires.isoformat() if isinstance(expires, datetime) else (str(expires).strip() if expires else None)
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
        if username and username.lower().lstrip("@") in VIP_USERNAMES: return True
        if self.is_blocked(uid): return False
        if uid in access_cache:
            cached = access_cache[uid]
            if cached.get("expires") is None: return True
            if datetime.now() < cached["expires"]: return True
            else: del access_cache[uid]
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
                log_event(f"has_access: expired uid={uid} exp={exp_str}")
                self.remove_user(uid)
                return False
        except Exception as e:
            log_event(f"has_access date parse error uid={uid}: {e}")
            access_cache[uid] = {"expires": None, "plan": plan}
            return True

    def sub_status(self, uid):
        uid = str(uid).strip()
        if self.is_blocked(uid): return "🚫 Заблокирован"
        u = self.get_user(uid)
        if not u: return "❌ Нет подписки"
        exp = u.get("expires")
        plan = u.get("plan", "paid")
        if exp is None: return f"♾ Бессрочная ({plan})"
        try:
            exp_dt = datetime.fromisoformat(str(exp).strip())
            if datetime.now() < exp_dt:
                left = (exp_dt - datetime.now()).days
                return f"✅ {plan} до {exp_dt.strftime('%d.%m.%Y')} ({left}д)"
            return "❌ Истекла"
        except: return "❓ Неизвестно"

    def block(self, uid): self.r.sadd("blocked", str(uid))
    def unblock(self, uid): self.r.srem("blocked", str(uid))
    def is_blocked(self, uid): return str(uid).strip() in self.r.smembers("blocked")

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
        if mc.get("date") != today: mc = {"date": today, "count": 0}
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
            if today in st.get("daily", {}): dau_today += 1
            if yesterday in st.get("daily", {}): dau_yest += 1
            if st.get("joined", "0") > week_ago: new_week += 1
            top_list.append((uid, st))
        top_list.sort(key=lambda x: x[1].get("total_msgs", 0), reverse=True)
        return {
            "total_users": len(all_uids), "paid_users": len(paid_uids),
            "blocked": len(blocked), "dau_today": dau_today, "dau_yest": dau_yest,
            "total_msgs": total_msgs, "new_week": new_week, "top_users": top_list[:5],
            "paid_uids": paid_uids
        }

    def save_memory(self, uid, key, value):
        uid = str(uid).strip()
        mem = self.r.get(f"memory:{uid}") or {}
        mem[key] = value
        self.r.set(f"memory:{uid}", mem)

    def get_memory(self, uid): return self.r.get(f"memory:{uid}") or {}

    def get_memory_context(self, uid):
        mem = self.get_memory(uid)
        parts = []
        if mem.get("name"): parts.append(f"Имя: {mem['name']}")
        if mem.get("age"): parts.append(f"Возраст: {mem['age']}")
        if mem.get("birthday"): parts.append(f"День рождения: {mem['birthday']}")
        if mem.get("city"): parts.append(f"Город: {mem['city']}")
        if mem.get("interests"): parts.append(f"Интересы: {mem['interests']}")
        if mem.get("about"): parts.append(f"О себе: {mem['about']}")
        return ("\n\nЧто ты знаешь о пользователе:\n" + "\n".join(parts)) if parts else ""

    def get_ref_code(self, uid):
        uid = str(uid).strip()
        data = self.r.get(f"referral:{uid}")
        if not data:
            code = f"ref{uid}"
            data = {"code": code, "invited": [], "bonus_days": 0}
            self.r.set(f"referral:{uid}", data)
            # обратный индекс: быстрый поиск по коду без O(N) скана
            self.r.set(f"refcode:{code}", uid)
        return data["code"]

    def apply_referral(self, new_uid, ref_code):
        new_uid = str(new_uid).strip()
        # O(1) поиск владельца через обратный индекс
        owner_uid = self.r.get(f"refcode:{ref_code}")
        if not owner_uid:
            return None
        owner_uid = str(owner_uid).strip()
        if owner_uid == new_uid:
            return None
        rd = self.r.get(f"referral:{owner_uid}")
        if not rd or new_uid in rd.get("invited", []):
            return None
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
        except Exception as e:
            log_event(f"apply_referral bonus error: {e}")
        return int(owner_uid)

    def add_note(self, uid, text):
        uid = str(uid).strip()
        notes = self.r.get(f"notes:{uid}") or []
        notes.append({"text": text, "date": datetime.now().isoformat()})
        self.r.set(f"notes:{uid}", notes[-50:])

    def get_notes(self, uid): return self.r.get(f"notes:{uid}") or []

    def add_reminder(self, uid, text, time_str, daily=False):
        uid = str(uid).strip()
        rems = self.r.get(f"reminders:{uid}") or []
        rems.append({"text": text, "time": time_str, "daily": daily, "created": datetime.now().isoformat()})
        self.r.set(f"reminders:{uid}", rems)

    def get_reminders(self, uid): return self.r.get(f"reminders:{uid}") or []

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
            if rems: result[uid] = rems
        return result


db = DataStore()
bot = telebot.TeleBot(TELEGRAM_TOKEN)
histories = {}; modes = {}; mood_log = {}; todo_list = {}; last_answer = {}; quiz_state = {}
access_cache = {}
_histories_lock = threading.Lock()  # защита от race condition при параллельных запросах
MAX_HISTORY = 20

# Периодическая очистка access_cache от старых "вечных" записей (защита от утечки памяти)
def _cache_cleanup_scheduler():
    while True:
        time.sleep(3600)
        try:
            now = datetime.now()
            to_del = []
            for uid_str, val in list(access_cache.items()):
                exp = val.get("expires")
                if exp is not None and exp < now:
                    to_del.append(uid_str)
            for k in to_del:
                access_cache.pop(k, None)
            if to_del:
                log_event(f"cache_cleanup: removed {len(to_del)} expired entries")
        except Exception as e:
            log_event(f"cache_cleanup error: {e}")

threading.Thread(target=_cache_cleanup_scheduler, daemon=True).start()

try:
    from gtts import gTTS
    VOICE_ENABLED = True
except:
    VOICE_ENABLED = False


# ════════════════════════════════════════════════════════
#  HTTP KEEPALIVE SERVER
# ════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        try:
            a = db.get_analytics()
            self.wfile.write(
                f"Liya v4.0 | Users:{a['total_users']} Paid:{a['paid_users']} DAU:{a['dau_today']}".encode()
            )
        except:
            self.wfile.write(b"Liya v4.0 OK")
    def log_message(self, *a): pass

threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), Handler).serve_forever(),
    daemon=True
).start()


# ════════════════════════════════════════════════════════
#  ПЛАНИРОВЩИКИ
# ════════════════════════════════════════════════════════

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
                            bot.send_message(int(uid_str), f"⏰ Напоминание!\n\n{r['text']}\n\n{d}", reply_markup=kb)
                        except: pass
                        if not r.get("daily"): to_del.append(i)
                for idx in reversed(to_del): db.remove_reminder(uid_str, idx)
        except Exception as e:
            log_event(f"Reminder: {e}")
        time.sleep(60)

threading.Thread(target=reminder_scheduler, daemon=True).start()


def expiry_notification_scheduler():
    """Уведомляет за 2 дня до конца подписки + удаляет истекшие."""
    while True:
        try:
            now = datetime.now()
            warn_dt = now + timedelta(days=2)
            for uid_str in list(db.r.smembers("paid_uids")):
                try:
                    u = db.get_user(int(uid_str))
                    if not u: continue
                    exp = u.get("expires")
                    if not exp: continue  # бессрочная
                    exp_dt = datetime.fromisoformat(str(exp).strip())

                    # Истекла → удаляем и уведомляем
                    if exp_dt < now:
                        db.remove_user(uid_str)
                        try:
                            kb = types.InlineKeyboardMarkup()
                            kb.add(types.InlineKeyboardButton("⭐ Продлить", callback_data="btn_pay_stars"))
                            bot.send_message(
                                int(uid_str),
                                "😔 Подписка истекла.\n\nПродли чтобы продолжить пользоваться Лией! 🌸",
                                reply_markup=kb
                            )
                        except: pass
                        continue

                    # Истекает через ≤ 2 дней — предупреждаем (раз в сутки)
                    if exp_dt < warn_dt:
                        warned_key = f"warned_expiry:{uid_str}"
                        if not db.r.get(warned_key):
                            days_left = max((exp_dt - now).days, 0)
                            try:
                                kb = types.InlineKeyboardMarkup()
                                kb.add(types.InlineKeyboardButton("⭐ Продлить сейчас", callback_data="btn_pay_stars"))
                                bot.send_message(
                                    int(uid_str),
                                    f"⚠️ Подписка истекает через {days_left} дн. ({exp_dt.strftime('%d.%m.%Y')})\n\nПродли чтобы не потерять доступ! 🌸",
                                    reply_markup=kb
                                )
                                db.r._cmd("SET", warned_key, "1", "EX", "86400")
                            except: pass
                except Exception as e:
                    log_event(f"Expiry check uid={uid_str}: {e}")
        except Exception as e:
            log_event(f"Expiry scheduler error: {e}")
        time.sleep(3600)

threading.Thread(target=expiry_notification_scheduler, daemon=True).start()


# ════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════════════

def get_greeting():
    h = datetime.now().hour
    if 5 <= h < 12: return "☀️ Доброе утро"
    if 12 <= h < 17: return "🌤 Добрый день"
    if 17 <= h < 22: return "🌆 Добрый вечер"
    return "🌙 Привет"

def get_history(uid):
    with _histories_lock:
        if uid not in histories:
            histories[uid] = []
        return histories[uid]

def is_admin(u): return u and u.lower().lstrip("@") in ADMIN_USERNAMES
def is_vip(u): return u and u.lower().lstrip("@") in VIP_USERNAMES

def mode_system(uid):
    base = SYSTEM_PROMPT + db.get_memory_context(uid)
    m = modes.get(uid, "normal")
    if m == "study":   base += "\n\nРежим УЧЁБЫ: объясняй по шагам с примерами."
    if m == "support": base += "\n\nРежим ПОДДЕРЖКИ: будь нежной и заботливой."
    if m == "creative": base += "\n\nРежим ТВОРЧЕСТВА: предлагай необычные идеи."
    return base

def mode_name(uid):
    return {"normal":"💬 Обычный","study":"📚 Учёба","support":"🤗 Поддержка","creative":"🎨 Творчество"}.get(
        modes.get(uid, "normal"), "💬 Обычный"
    )

def check_daily_limit(uid, username=""):
    if is_vip(username): return True, 0, 9999
    u = db.get_user(uid)
    count = db.get_daily_count(uid)
    if not u: return count < FREE_MSG_LIMIT, count, FREE_MSG_LIMIT
    if u.get("plan") == "trial": return count < FREE_DAILY_LIMIT, count, FREE_DAILY_LIMIT
    return True, count, 9999


# ════════════════════════════════════════════════════════
#  CLAUDE API — ОСНОВНОЙ AI
# ════════════════════════════════════════════════════════

def ask_claude_raw(messages: list, system: str, max_tokens: int = 2000) -> str:
    """Прямой вызов Claude API."""
    if not CLAUDE_KEY:
        raise Exception("ANTHROPIC_API_KEY не задан!")

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": CLAUDE_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        },
        timeout=45,
    )

    if r.status_code != 200:
        err = r.json().get("error", {})
        raise Exception(f"Claude {r.status_code}: {err.get('message', 'error')}")

    return clean_response(r.json()["content"][0]["text"])


def ask_ai(uid, text, image_b64=None, custom_system=None):
    """
    Основная функция ответа бота.
    Текст → Claude API (стабильно, не слетает)
    Фото  → Groq Vision
    """
    history = get_history(uid)
    sys_msg = custom_system or mode_system(uid)

    # ── ФОТО → Groq Vision ──
    if image_b64:
        msgs_v = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": text or "Что на фото? Если есть задачи — реши пошагово."}
            ]}
        ]
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={"model": MODEL_VISION, "messages": msgs_v, "max_tokens": 2000},
                timeout=60
            )
            data = r.json()
            if "error" in data:
                raise Exception(data["error"].get("message", "Vision error"))
            return clean_response(data["choices"][0]["message"]["content"])
        except Exception as e:
            log_event(f"Vision error: {e}")
            # Fallback через Claude без фото
            return ask_claude_raw(
                [{"role": "user", "content": f"Пользователь прислал фото с подписью: '{text}'. Скажи что не смогла обработать фото и предложи описать задачу текстом."}],
                system=sys_msg
            )

    # ── ТЕКСТ → Claude ──
    with _histories_lock:
        history.append({"role": "user", "content": text})
        # Обрезаем здесь, до отправки — не после
        if len(history) > MAX_HISTORY:
            histories[uid] = history[-MAX_HISTORY:]
            history = histories[uid]
        snapshot = list(history)  # копия для безопасной передачи в API

    try:
        answer = ask_claude_raw(snapshot, system=sys_msg)
        with _histories_lock:
            history.append({"role": "assistant", "content": answer})
        return answer
    except Exception as e:
        log_event(f"Claude error uid={uid}: {e}")
        with _histories_lock:
            # убираем user message из истории при ошибке
            if history and history[-1]["role"] == "user":
                history.pop()
        raise


def transcribe_voice(audio, fname="voice.ogg"):
    r = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_KEY}"},
        files={"file": (fname, audio, "audio/ogg")},
        data={"model": MODEL_WHISPER, "language": "ru", "response_format": "text"},
        timeout=30
    )
    if r.status_code == 200: return r.text.strip()
    raise Exception(f"Whisper {r.status_code}")


# ════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ КАРТИНОК — УЛУЧШЕННАЯ
# ════════════════════════════════════════════════════════

def translate_prompt(text_ru: str) -> str:
    """Переводим промпт через Claude — быстро и правильно."""
    try:
        return ask_claude_raw(
            [{"role": "user", "content": f"Translate to English for image generation. Return ONLY translation, no extra words: {text_ru}"}],
            system="You are a translator. Return only English translation.",
            max_tokens=150
        ).strip()
    except Exception as e:
        log_event(f"Translate prompt error: {e}")
        return text_ru


def generate_image(prompt_ru: str) -> "Optional[bytes]":
    """Генерация картинок через Pollinations (таймаут 45с вместо 90с)."""
    prompt_en = translate_prompt(prompt_ru)
    log_event(f"Generating: {prompt_en[:80]}")

    for model in ["flux", "turbo"]:
        try:
            seed = random.randint(1, 99999)
            url = (
                f"https://image.pollinations.ai/prompt/{quote(prompt_en)}"
                f"?model={model}&width=1024&height=1024&seed={seed}&nologo=true"
            )
            resp = requests.get(url, timeout=45)
            if resp.status_code == 200 and len(resp.content) > 5000:
                log_event(f"Image OK model={model} size={len(resp.content)}")
                return resp.content
        except requests.exceptions.Timeout:
            log_event(f"Image timeout model={model}")
        except Exception as e:
            log_event(f"Image error model={model}: {e}")

    return None


# ════════════════════════════════════════════════════════
#  ДРУГИЕ СЕРВИСЫ
# ════════════════════════════════════════════════════════

def get_weather(city):
    try:
        r = requests.get(f"https://wttr.in/{quote(city)}?format=j1&lang=ru", timeout=10)
        c = r.json()["current_condition"][0]
        return (
            f"🌤 Погода в {city}:\n\n"
            f"🌡 {c['temp_C']}°C (ощущается {c['FeelsLikeC']}°C)\n"
            f"☁️ {c['lang_ru'][0]['value']}\n"
            f"💧 Влажность: {c['humidity']}%\n"
            f"💨 Ветер: {c['windspeedKmph']} км/ч"
        )
    except: return f"😔 Не нашла погоду для '{city}'."

def get_currency():
    try:
        rates = requests.get("https://api.exchangerate-api.com/v4/latest/RUB", timeout=10).json().get("rates", {})
        return (
            f"💰 Курс валют:\n\n"
            f"🇺🇸 1 USD = {round(1/rates.get('USD', 0.011), 2)} ₽\n"
            f"🇪🇺 1 EUR = {round(1/rates.get('EUR', 0.010), 2)} ₽\n"
            f"🇰🇿 1 ₽ = {round(rates.get('KZT', 5.5), 2)} ₸\n\nОбновлено ⏱"
        )
    except: return "😔 Не могу получить курс."

def notify_admin(text):
    for uid in db.r.smembers("all_uids"):
        st = db.r.get(f"stats:{uid}") or {}
        if st.get("username", "").lower() in ADMIN_USERNAMES:
            try: bot.send_message(int(uid), f"🔔 {text}")
            except: pass

def send_safe(chat_id, text, reply_to=None, kb=None, delete_msg_id=None):
    if delete_msg_id:
        try: bot.delete_message(chat_id, delete_msg_id)
        except: pass
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for i, chunk in enumerate(chunks):
        markup = kb if i == len(chunks)-1 else None
        try:
            if reply_to and i == 0: bot.reply_to(reply_to, chunk, reply_markup=markup)
            else: bot.send_message(chat_id, chunk, reply_markup=markup)
        except Exception as e:
            log_event(f"send_safe error: {e}")

def delete_and_send(call, text, kb=None):
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    try: bot.send_message(call.from_user.id, text, reply_markup=kb)
    except Exception as e: log_event(f"delete_and_send error: {e}")


# ════════════════════════════════════════════════════════
#  КОНСТАНТЫ И КЛАВИАТУРЫ
# ════════════════════════════════════════════════════════

ZODIAC_SIGNS = ["♈ Овен","♉ Телец","♊ Близнецы","♋ Рак","♌ Лев","♍ Дева",
                "♎ Весы","♏ Скорпион","♐ Стрелец","♑ Козерог","♒ Водолей","♓ Рыбы"]
QUIZ_TOPICS = {"🌍 География":"geography","🎬 Кино":"movies","🎵 Музыка":"music",
               "🧪 Наука":"science","📚 Литература":"literature","🏆 Спорт":"sport",
               "🍕 Еда":"food","💄 Красота":"beauty","🐾 Животные":"animals","🌟 Случайное":"random"}
COMPLIMENTS = ["Ты просто замечательная! ✨","Ты умница и красавица 💕",
               "С тобой всегда интересно! 🌸","Ты справишься со всем, верю в тебя 💪","Ты особенная 🦋"]
AFFIRMATIONS = ["Я достойна любви и счастья 💕","Я справляюсь со всем 💪",
                "Каждый день я становлюсь лучше ✨","Мои мечты реальны 🎯","Я верю в себя 🦋"]
MEDITATIONS = [
    {"name":"🌬 Дыхание 4-7-8","text":"Снимает тревогу:\n\n1. Вдох — 4 сек\n2. Задержка — 7 сек\n3. Выдох — 8 сек\n\nПовтори 4 раза 🌿"},
    {"name":"🧘 5-4-3-2-1","text":"Назови:\n\n5 вещей которые видишь\n4 которые потрогаешь\n3 звука\n2 запаха\n1 вкус\n\nВозвращает в момент 💙"},
    {"name":"💤 Для сна","text":"Перед сном:\n\n• Напряги всё тело 5 сек\n• Резко расслабь\n• Медленно дыши\n• Думай о приятном 😴"},
]
MOOD_EMOJIS = {"😊":"Хорошо","🤩":"Отлично","😔":"Грустно","😤":"Злюсь",
               "😰":"Тревожно","😴":"Устала","🥰":"Влюблена","😐":"Нейтрально"}
LANGUAGES = {"🇬🇧 Английский":"English","🇩🇪 Немецкий":"German","🇫🇷 Французский":"French",
             "🇪🇸 Испанский":"Spanish","🇨🇳 Китайский":"Chinese","🇯🇵 Японский":"Japanese",
             "🇰🇷 Корейский":"Korean","🇹🇷 Турецкий":"Turkish"}


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


# ════════════════════════════════════════════════════════
#  ПРОВЕРКА ДОСТУПА
# ════════════════════════════════════════════════════════

def check_access(msg):
    uid = msg.from_user.id; u = msg.from_user.username or ""
    if is_vip(u): return True
    if db.is_blocked(str(uid)):
        bot.reply_to(msg, "🚫 Заблокирована!")
        return False
    if db.has_access(uid, u): return True
    modes[uid] = "normal"
    bot.reply_to(msg, f"🔒 Доступ платный\n\n⭐ {PRICE_STARS} Stars или @tronqx\n\n🎁 Или 3 дня бесплатно!", reply_markup=access_kb())
    return False

def check_access_cb(call):
    uid = call.from_user.id; u = call.from_user.username or ""
    if is_vip(u): return True
    if db.is_blocked(str(uid)):
        bot.answer_callback_query(call.id, "🚫 Заблокирована!")
        return False
    if db.has_access(uid, u): return True
    modes[uid] = "normal"
    bot.answer_callback_query(call.id, "🔒 Нет доступа!")
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    bot.send_message(uid, f"🔒 Нужна подписка!\n\n⭐ {PRICE_STARS} Stars или @tronqx\n\n🎁 Или 3 дня бесплатно!", reply_markup=access_kb())
    return False

def check_and_count(msg):
    uid = msg.from_user.id; u = msg.from_user.username or ""
    if not check_access(msg): return False
    ok, count, limit = check_daily_limit(uid, u)
    if not ok:
        plan = (db.get_user(uid) or {}).get("plan", "free")
        if plan == "trial":
            bot.reply_to(msg, f"⚠️ Лимит пробного: {limit} сообщений/день\n\nКупи полный доступ!", reply_markup=access_kb())
        else:
            bot.reply_to(msg, f"⚠️ Лимит {limit}/день исчерпан.")
        return False
    db.count_message(uid)
    return True


# ════════════════════════════════════════════════════════
#  КОМАНДЫ
# ════════════════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid = msg.from_user.id; u = msg.from_user.username or ""; name = msg.from_user.first_name or "Солнышко"
    is_new = db.register_user(uid, u, name)
    with _histories_lock:
        histories[uid] = []
    modes[uid] = "normal"
    db.save_memory(uid, "name", name)
    ref_bonus = ""
    parts = msg.text.split()
    if len(parts) > 1 and parts[1].startswith("ref"):
        owner = db.apply_referral(uid, parts[1])
        if owner:
            ref_bonus = "\n🎁 Реферальный бонус применён!"
            try: bot.send_message(owner, "🎉 По твоей ссылке зарегистрировались! +7 дней 🌸")
            except: pass
    if is_new: notify_admin(f"👤 Новый: {uid} @{u} {name}")
    bot.reply_to(msg,
        f"{get_greeting()}, {name}! ✨{ref_bonus}\n\n"
        f"Я Лия — твой умный AI-помощник 👑\n\n"
        f"💬 Общаюсь как ChatGPT\n📸 Решаю задачи по фото\n"
        f"🖼 Генерирую картинки\n🎤 Расшифровываю голосовые\n"
        f"🧮 Математика, физика, код\n🌙 Гороскоп, рецепты, переводчик\n\n"
        f"Выбери с чего начнём 👇",
        reply_markup=main_menu_kb(u)
    )

@bot.message_handler(commands=["menu"])
def cmd_menu(msg):
    db.register_user(msg.from_user.id, msg.from_user.username or "", msg.from_user.first_name or "")
    bot.reply_to(msg, f"Меню 🌸 | Режим: {mode_name(msg.from_user.id)}", reply_markup=main_menu_kb(msg.from_user.username or ""))

@bot.message_handler(commands=["new"])
def cmd_new(msg):
    uid = msg.from_user.id
    with _histories_lock:
        histories[uid] = []
    modes[uid] = "normal"
    bot.reply_to(msg, "🔄 Начнём с чистого листа! Теперь пиши 🌸")

@bot.message_handler(commands=["status"])
def cmd_status(msg):
    uid = msg.from_user.id; u = msg.from_user.username or ""
    has = db.has_access(uid, u)
    sub = db.sub_status(uid)
    bot.reply_to(msg, f"ID: {uid}\nДоступ: {has}\nПодписка: {sub}\nVIP: {is_vip(u)}\nAI: Claude {CLAUDE_MODEL}")

@bot.message_handler(commands=["myid"])
def cmd_myid(msg): bot.reply_to(msg, f"Твой ID: {msg.from_user.id}")

@bot.message_handler(commands=["grant"])
def cmd_grant(msg):
    if not is_admin(msg.from_user.username or ""): return
    p = msg.text.split()
    if len(p) < 2: bot.reply_to(msg, "Использование: /grant [id]"); return
    try:
        t = int(p[1]); db.set_user(t, None, plan="forever"); db.unblock(t)
        bot.reply_to(msg, f"✅ Бессрочная выдана {t}")
        try: bot.send_message(t, "🎉 Тебе выдан бессрочный доступ! /start 🌸")
        except: pass
    except: bot.reply_to(msg, "❌ Неверный ID")

@bot.message_handler(commands=["remind"])
def cmd_remind(msg):
    if not check_access(msg): return
    p = msg.text.split(maxsplit=2)
    if len(p) < 3: bot.reply_to(msg, "⏰ Формат: /remind 18:00 выпить воду"); return
    t = p[1]; txt = p[2]; daily = txt.endswith("каждый день")
    if daily: txt = txt[:-len("каждый день")].strip()
    if not re.match(r"^\d{2}:\d{2}$", t): bot.reply_to(msg, "❌ Формат времени: ЧЧ:ММ"); return
    db.add_reminder(msg.from_user.id, txt, t, daily=daily)
    bot.reply_to(msg, f"✅ Напоминание:\n⏰ {t}{' 🔁' if daily else ''}\n📝 {txt}")

@bot.message_handler(commands=["weather"])
def cmd_weather(msg):
    p = msg.text.split(maxsplit=1)
    if len(p) < 2: bot.reply_to(msg, "🌤 Напиши город: /weather Москва"); return
    bot.send_chat_action(msg.chat.id, "typing"); bot.reply_to(msg, get_weather(p[1]))


# ════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ МЕДИА
# ════════════════════════════════════════════════════════

@bot.message_handler(content_types=["voice"])
def handle_voice(msg):
    uid = msg.from_user.id
    if not check_and_count(msg): return
    bot.send_chat_action(msg.chat.id, "typing")
    wait = bot.reply_to(msg, "🎤 Слушаю...")
    try:
        fi = bot.get_file(msg.voice.file_id)
        audio = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{fi.file_path}", timeout=20).content
        text = transcribe_voice(audio)
        try: bot.delete_message(uid, wait.message_id)
        except: pass
        if modes.get(uid) == "note_voice_mode":
            modes[uid] = "normal"; db.add_note(uid, text)
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
        try: bot.delete_message(uid, wait.message_id)
        except: pass
        bot.reply_to(msg, "😔 Не смогла расшифровать. Попробуй ещё раз!")


@bot.message_handler(content_types=["photo"])
def handle_photo(msg):
    uid = msg.from_user.id
    if not check_and_count(msg): return
    bot.send_chat_action(msg.chat.id, "typing")
    wait = None
    try:
        photos = msg.photo
        photo = photos[-2] if len(photos) >= 2 else photos[-1]
        fi = bot.get_file(photo.file_id)
        resp = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{fi.file_path}", timeout=30)
        if resp.status_code != 200:
            bot.reply_to(msg, "😔 Не смогла скачать фото. Попробуй ещё раз!")
            return
        image_b64 = base64.b64encode(resp.content).decode("utf-8")
        wait = bot.send_message(uid, "📸 Анализирую фото... ⏳")
        caption = msg.caption or "Внимательно посмотри на фото. Если есть математика, задачи, уравнения, текст — прочитай всё и реши/объясни пошагово. Пиши обычным текстом без LaTeX."
        answer = ask_ai(uid, caption, image_b64=image_b64)
        last_answer[uid] = answer
        try: bot.delete_message(uid, wait.message_id)
        except: pass
        send_safe(uid, answer, reply_to=msg, kb=after_kb())
    except Exception as e:
        log_event(f"Photo error: {e}")
        try:
            if wait: bot.delete_message(uid, wait.message_id)
        except: pass
        bot.reply_to(msg, "😔 Не смогла обработать фото. Напиши задачу текстом — обязательно помогу!")


# ════════════════════════════════════════════════════════
#  ПЛАТЁЖНАЯ СИСТЕМА
# ════════════════════════════════════════════════════════

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q): bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=["successful_payment"])
def successful_payment(msg):
    uid = msg.from_user.id; u = msg.from_user.username or ""
    stars = msg.successful_payment.total_amount

    if stars >= 500:
        exp = None; plan = "forever"; days_text = "навсегда ♾"
    elif stars >= 200:
        exp = datetime.now() + timedelta(days=90); plan = "90days"; days_text = "90 дней"
    else:
        exp = datetime.now() + timedelta(days=30); plan = "30days"; days_text = "30 дней"

    db.set_user(uid, exp, plan=plan)
    db.unblock(uid)

    log_event(f"Payment OK: uid={uid} @{u} stars={stars} plan={plan}")
    notify_admin(f"💳 Оплата! {uid} @{u} {stars} Stars → {plan}")

    bot.send_message(
        uid,
        f"🎉 Оплата прошла успешно!\n\n"
        f"⭐ Списано: {stars} Stars\n"
        f"✅ Подписка активна: {days_text}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"Все функции открыты! Пиши что угодно 🌸",
        reply_markup=main_menu_kb(u)
    )


# ════════════════════════════════════════════════════════
#  ВНУТРЕННЯЯ ГЕНЕРАЦИЯ КАРТИНОК
# ════════════════════════════════════════════════════════

def _gen_img(chat_id, uid, prompt):
    """Общая логика генерации и отправки картинки."""
    wait = bot.send_message(chat_id, "🎨 Рисую... это займёт ~30 секунд ⏳")
    try:
        img_data = generate_image(prompt)
        try: bot.delete_message(chat_id, wait.message_id)
        except: pass
        if img_data:
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🔄 Ещё раз", callback_data=f"imagine_again_{prompt[:50]}"),
                types.InlineKeyboardButton("✍️ Другое", callback_data="imagine_custom"),
                types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"),
            )
            bot.send_photo(chat_id, img_data, caption=f"🖼 {prompt[:100]}", reply_markup=kb)
            last_answer[uid] = f"[Картинка: {prompt}]"
        else:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔄 Попробовать снова", callback_data=f"imagine_again_{prompt[:50]}"))
            bot.send_message(chat_id, "😔 Не смогла нарисовать. Сервис перегружен — попробуй через минуту!", reply_markup=kb)
    except Exception as e:
        log_event(f"_gen_img error: {e}")
        try: bot.delete_message(chat_id, wait.message_id)
        except: pass
        bot.send_message(chat_id, "😔 Ошибка при генерации. Попробуй позже!")


# ════════════════════════════════════════════════════════
#  CALLBACK ОБРАБОТЧИКИ
# ════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    uid = call.from_user.id; data = call.data; u = call.from_user.username or ""

    FREE_CB = {"btn_menu","btn_help","btn_trial","btn_pay_stars",
               "pay_stars_30","pay_stars_90","pay_stars_forever","btn_account","adm_panel"}

    if data not in FREE_CB and not data.startswith("adm_") and not check_access_cb(call):
        return

    # ── ПРОБНЫЙ ПЕРИОД ──
    if data == "btn_trial":
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        if db.get_user(uid):
            bot.send_message(uid, f"У тебя уже есть подписка!\nСтатус: {db.sub_status(uid)}")
            return
        exp = datetime.now() + timedelta(days=TRIAL_DAYS)
        db.set_user(uid, exp, plan="trial")
        log_event(f"Trial: {uid} @{u}")
        notify_admin(f"🎁 Пробный: {uid} @{u}")
        bot.send_message(uid,
            f"🎁 Пробный период активирован!\n\n✅ {TRIAL_DAYS} дня бесплатно\n"
            f"📊 Лимит: {FREE_DAILY_LIMIT} сообщений/день\n⏰ До: {exp.strftime('%d.%m.%Y')}\n\n"
            f"Теперь все функции доступны! 🌸", reply_markup=main_menu_kb(u))
        return

    # ── ОПЛАТА ──
    if data == "btn_pay_stars":
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(f"⭐ {PRICE_STARS} Stars — 30 дней", callback_data="pay_stars_30"),
            types.InlineKeyboardButton("⭐ 200 Stars — 90 дней", callback_data="pay_stars_90"),
            types.InlineKeyboardButton("⭐ 500 Stars — Навсегда", callback_data="pay_stars_forever"),
            types.InlineKeyboardButton("💳 Написать @tronqx", url=PAYMENT_LINK),
        )
        bot.send_message(uid, "⭐ Выбери план подписки:", reply_markup=kb)
        return

    if data.startswith("pay_stars_"):
        plan_key = data.replace("pay_stars_", "")
        plans = {"30": (PRICE_STARS, "30 дней"), "90": (200, "90 дней"), "forever": (500, "Навсегда")}
        if plan_key not in plans: bot.answer_callback_query(call.id); return
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
            bot.send_message(uid, f"⭐ {label} — {amount} Stars\n\nНапиши @tronqx: «Подписка {label}, ID: {uid}»", reply_markup=kb)
        return

    # ── АККАУНТ ──
    if data == "btn_account":
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        st = db.r.get(f"stats:{uid}") or {}
        ref = db.r.get(f"referral:{uid}") or {}
        code = db.get_ref_code(uid)
        link = f"https://t.me/{bot.get_me().username}?start={code}"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("💳 Подписка", callback_data="btn_pay_stars"),
            types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")
        )
        bot.send_message(uid,
            f"📱 Мой аккаунт\n\n🆔 ID: {uid}\n📅 С: {st.get('joined','?')[:10]}\n"
            f"💎 Статус: {db.sub_status(uid)}\n✉️ Сообщений сегодня: {db.get_daily_count(uid)}\n"
            f"✉️ Всего: {st.get('total_msgs',0)}\n🤖 AI: Claude {CLAUDE_MODEL}\n\n"
            f"🔗 Реф. ссылка:\n{link}\n👥 Приглашено: {len(ref.get('invited',[]))}",
            reply_markup=kb)
        return

    # ── МЕНЮ ──
    if data == "btn_menu":
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        bot.send_message(uid, f"Меню 🌸 | Режим: {mode_name(uid)}", reply_markup=main_menu_kb(u))
        return

    if data == "btn_new":
        with _histories_lock:
            histories[uid] = []
        modes[uid] = "normal"
        bot.answer_callback_query(call.id, "🔄 Очищено!")
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        bot.send_message(uid, "🔄 Новый диалог! Пиши что угодно 🌸")
        return

    if data == "btn_help":
        bot.answer_callback_query(call.id)
        bot.send_message(uid,
            "ℹ️ Помощь:\n\n💬 Пиши любое сообщение\n📸 Отправь фото — решу задачу\n"
            "🎤 Запиши голосовое — расшифрую\n\nКоманды:\n/start — перезапуск\n"
            "/new — новый диалог\n/remind 18:00 текст — напоминание\n"
            "/weather Москва — погода\n/myid — твой ID",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")
            ))
        return

    # ── РЕЖИМЫ ──
    if data.startswith("mode_"):
        m = data.replace("mode_", ""); modes[uid] = m
        with _histories_lock:
            histories[uid] = []
        names = {"normal":"💬 Пиши что угодно!","study":"📚 Помогу с учёбой!",
                 "support":"🤗 Я здесь 💕","creative":"🎨 Придумаем что-нибудь! ✨"}
        bot.answer_callback_query(call.id, "Режим изменён!")
        bot.send_message(uid, names.get(m, "Режим изменён!"))
        return

    # ── КАРТИНКИ ──
    if data == "btn_imagine":
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🌅 Закат у моря", callback_data="imagine_q_sunset at sea golden hour photorealistic"),
            types.InlineKeyboardButton("🌸 Аниме девушка", callback_data="imagine_q_beautiful anime girl sakura spring"),
            types.InlineKeyboardButton("🏙 Ночной город", callback_data="imagine_q_night city cyberpunk neon lights"),
            types.InlineKeyboardButton("🐱 Котик", callback_data="imagine_q_cute fluffy cat adorable soft lighting"),
            types.InlineKeyboardButton("✍️ Своё описание", callback_data="imagine_custom"),
        )
        bot.send_message(uid, "🖼 Выбери стиль или опиши своё:", reply_markup=kb)
        return

    if data == "imagine_custom":
        bot.answer_callback_query(call.id)
        modes[uid] = "imagine_mode"
        bot.send_message(uid, "✍️ Опиши что нарисовать (на русском):\n\nНапример: красивая девушка в кафе, уютно, осень")
        return

    if data.startswith("imagine_q_"):
        prompt = data.replace("imagine_q_", "")
        bot.answer_callback_query(call.id, "🎨 Рисую...")
        _gen_img(call.message.chat.id, uid, prompt)
        return

    if data.startswith("imagine_again_"):
        prompt = data.replace("imagine_again_", "")
        bot.answer_callback_query(call.id, "🔄 Перегенерирую...")
        _gen_img(call.message.chat.id, uid, prompt)
        return

    # ── ADMIN ПАНЕЛЬ ──
    if data == "adm_panel":
        if not is_admin(u): bot.answer_callback_query(call.id, "❌ Нет прав"); return
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        bot.send_message(uid, "👑 Админ-панель:", reply_markup=admin_kb())
        return

    if data == "adm_stats":
        if not is_admin(u): bot.answer_callback_query(call.id, "❌"); return
        bot.answer_callback_query(call.id, "📊 Загружаю...")
        try:
            a = db.get_analytics()
            paid_uids = a.get("paid_uids", set())

            # Подсчёт по планам
            count_active = count_trial = count_forever = 0
            for uid_str in paid_uids:
                usr = db.get_user(int(uid_str))
                if not usr: continue
                plan = usr.get("plan", "?")
                exp = usr.get("expires")
                if plan == "trial": count_trial += 1
                elif exp is None: count_forever += 1
                else:
                    try:
                        if datetime.fromisoformat(str(exp).strip()) > datetime.now():
                            count_active += 1
                    except: pass

            top_text = ""
            for i, (u_id, st) in enumerate(a.get("top_users", [])[:5], 1):
                uname = st.get("username", "")
                name = st.get("name", "")
                msgs = st.get("total_msgs", 0)
                label = f"@{uname}" if uname else name or u_id
                top_text += f"  {i}. {label} — {msgs} сообщ.\n"

            stats_text = (
                f"📊 Статистика Лия Бот v4.0\n"
                f"{'─' * 30}\n\n"
                f"👥 Пользователи:\n"
                f"  Всего: {a['total_users']}\n"
                f"  Новых за неделю: {a['new_week']}\n"
                f"  DAU сегодня: {a['dau_today']}\n"
                f"  DAU вчера: {a['dau_yest']}\n\n"
                f"💳 Подписки:\n"
                f"  Активных платных: {count_active}\n"
                f"  Пробных: {count_trial}\n"
                f"  Бессрочных: {count_forever}\n"
                f"  Заблокировано: {a['blocked']}\n\n"
                f"💬 Сообщения:\n"
                f"  Всего: {a['total_msgs']}\n\n"
                f"🏆 Топ активных:\n{top_text or '  —'}\n"
                f"🤖 AI модель: {CLAUDE_MODEL}\n"
                f"⏱ {datetime.now().strftime('%H:%M %d.%m.%Y')}"
            )
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 Панель", callback_data="adm_panel"))
            bot.send_message(uid, stats_text, reply_markup=kb)
        except Exception as e:
            bot.send_message(uid, f"❌ Ошибка: {e}")
        return

    if data == "adm_grant":
        if not is_admin(u): return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_grant_mode"
        bot.send_message(uid, "➕ Введи ID пользователя для бессрочного доступа:")
        return

    if data == "adm_grant_time":
        if not is_admin(u): return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_grant_time_mode"
        bot.send_message(uid, "🕐 Введи ID пользователя для доступа на время:", reply_markup=time_kb())
        return

    if data == "adm_revoke":
        if not is_admin(u): return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_revoke_mode"
        bot.send_message(uid, "❌ Введи ID для отзыва подписки:")
        return

    if data == "adm_block":
        if not is_admin(u): return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_block_mode"
        bot.send_message(uid, "🚫 Введи ID для блокировки:")
        return

    if data == "adm_unblock":
        if not is_admin(u): return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_unblock_mode"
        bot.send_message(uid, "✅ Введи ID для разблокировки:")
        return

    if data == "adm_broadcast":
        if not is_admin(u): return
        bot.answer_callback_query(call.id)
        modes[uid] = "adm_broadcast_mode"
        bot.send_message(uid, "📢 Введи текст рассылки всем пользователям:")
        return

    if data == "adm_users":
        if not is_admin(u): return
        bot.answer_callback_query(call.id, "📋 Загружаю...")
        all_uids = db.r.smembers("all_uids")
        paid_uids = db.r.smembers("paid_uids")
        lines = [f"👥 Все пользователи ({len(all_uids)}):\n"]
        for uid_s in list(all_uids)[:30]:
            st = db.r.get(f"stats:{uid_s}") or {}
            uname = st.get("username", "")
            is_paid = uid_s in paid_uids
            sub = db.sub_status(int(uid_s))
            lines.append(f"{'✅' if is_paid else '❌'} {uid_s} @{uname}\n   {sub}")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 Панель", callback_data="adm_panel"))
        bot.send_message(uid, "\n".join(lines[:50]), reply_markup=kb)
        return

    if data.startswith("adm_time_"):
        if not is_admin(u): return
        parts = data.split("_")
        days = int(parts[-1])
        target_uid = parts[-2] if parts[-2] else None
        if target_uid and target_uid.isdigit():
            t = int(target_uid)
            exp = datetime.now() + timedelta(days=days)
            db.set_user(t, exp, plan=f"{days}days")
            bot.answer_callback_query(call.id, f"✅ {days} дней выдано!")
            bot.send_message(uid, f"✅ Пользователю {t} выдано {days} дней до {exp.strftime('%d.%m.%Y')}")
            try: bot.send_message(t, f"🎉 Тебе выдано {days} дней подписки! Приятного пользования 🌸")
            except: pass
        else:
            bot.answer_callback_query(call.id)
            modes[uid] = f"adm_time_{days}_mode"
            bot.send_message(uid, f"🕐 Введи ID пользователя для {days} дней:")
        return

    # ── ОСТАЛЬНЫЕ КНОПКИ — через AI ──
    ai_callbacks = {
        "btn_joke": "Расскажи смешную шутку на русском.",
        "btn_fact": "Расскажи интересный факт дня.",
        "btn_motivation": "Дай мощную мотивацию на день.",
        "btn_compliment": random.choice(COMPLIMENTS) if True else "",
        "btn_currency": None,  # специальная обработка
        "btn_weather": None,
        "btn_horoscope": None,
        "btn_meditation": None,
        "btn_photo_hint": "📸 Отправь фото и я его проанализирую или решу задачи с него!",
        "btn_explain_simple": "Объясни предыдущий ответ более простыми словами, как для ребёнка.",
        "btn_elaborate": "Расскажи подробнее о предыдущей теме.",
        "btn_save_note": None,
        "btn_voice_last": None,
        "btn_love": None,
        "btn_beauty": None,
        "btn_recipe": None,
        "btn_summarize": None,
        "btn_translate": None,
        "btn_mood": None,
        "btn_planner": None,
        "btn_todo": None,
        "btn_reminders": None,
        "btn_notes": None,
        "btn_memory": None,
        "btn_referral": None,
    }

    # Простые AI-запросы
    simple_ai = {"btn_joke","btn_fact","btn_motivation"}
    if data in simple_ai:
        prompts = {
            "btn_joke": "Расскажи одну смешную шутку на русском.",
            "btn_fact": "Расскажи один интересный факт дня.",
            "btn_motivation": "Дай одну мощную мотивационную цитату на русском с объяснением.",
        }
        bot.answer_callback_query(call.id)
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, prompts[data])
            last_answer[uid] = answer
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Callback AI error {data}: {e}")
            bot.send_message(uid, "😔 Не смогла ответить. Попробуй ещё раз!")
        return

    if data == "btn_compliment":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, random.choice(COMPLIMENTS), reply_markup=after_kb())
        return

    if data == "btn_currency":
        bot.answer_callback_query(call.id)
        bot.send_message(uid, get_currency(), reply_markup=after_kb())
        return

    if data == "btn_weather":
        bot.answer_callback_query(call.id)
        modes[uid] = "weather_mode"
        bot.send_message(uid, "🌤 Напиши название города:")
        return

    if data == "btn_horoscope":
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        kb = types.InlineKeyboardMarkup(row_width=3)
        for sign in ZODIAC_SIGNS:
            kb.add(types.InlineKeyboardButton(sign, callback_data=f"horo_{sign}"))
        kb.add(types.InlineKeyboardButton("🔙 Меню", callback_data="btn_menu"))
        bot.send_message(uid, "🌙 Выбери знак зодиака:", reply_markup=kb)
        return

    if data.startswith("horo_"):
        sign = data.replace("horo_", "")
        bot.answer_callback_query(call.id, "🔮 Составляю...")
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Составь подробный гороскоп на сегодня для знака {sign}. Будь позитивной и конкретной.", custom_system=SYSTEM_PROMPT)
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Horoscope error: {e}")
            bot.send_message(uid, "😔 Не смогла составить гороскоп. Попробуй позже!")
        return

    if data == "btn_meditation":
        bot.answer_callback_query(call.id)
        med = random.choice(MEDITATIONS)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("🧘 Ещё", callback_data="btn_meditation"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid, f"{med['name']}\n\n{med['text']}", reply_markup=kb)
        return

    if data == "btn_love":
        bot.answer_callback_query(call.id)
        modes[uid] = "love_mode"
        bot.send_message(uid, "💌 Кому написать любовное письмо? Напиши имя или опиши:")
        return

    if data == "btn_beauty":
        bot.answer_callback_query(call.id)
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, "Дай 5 советов по уходу за собой на сегодня: кожа, волосы, здоровье. Будь конкретной и практичной.", custom_system=SYSTEM_PROMPT)
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Beauty error: {e}")
            bot.send_message(uid, "😔 Попробуй позже!")
        return

    if data == "btn_recipe":
        bot.answer_callback_query(call.id)
        modes[uid] = "recipe_mode"
        bot.send_message(uid, "🍽 Что приготовить? Напиши продукты или блюдо:")
        return

    if data == "btn_summarize":
        bot.answer_callback_query(call.id)
        modes[uid] = "summarize_mode"
        bot.send_message(uid, "📖 Вставь текст который нужно пересказать:")
        return

    if data == "btn_translate":
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        kb = types.InlineKeyboardMarkup(row_width=2)
        for lang_name, lang_code in LANGUAGES.items():
            kb.add(types.InlineKeyboardButton(lang_name, callback_data=f"translate_{lang_code}"))
        kb.add(types.InlineKeyboardButton("🔙 Меню", callback_data="btn_menu"))
        bot.send_message(uid, "🌍 Выбери язык перевода:", reply_markup=kb)
        return

    if data.startswith("translate_"):
        lang = data.replace("translate_", "")
        bot.answer_callback_query(call.id)
        modes[uid] = f"translate_{lang}_mode"
        bot.send_message(uid, f"🌍 Пиши текст для перевода на {lang}:")
        return

    if data == "btn_mood":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=4)
        for emoji, name in MOOD_EMOJIS.items():
            kb.add(types.InlineKeyboardButton(f"{emoji} {name}", callback_data=f"mood_{name}"))
        kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid, "📊 Как ты сейчас?", reply_markup=kb)
        return

    if data.startswith("mood_"):
        mood_name = data.replace("mood_", "")
        today = datetime.now().strftime("%Y-%m-%d")
        if uid not in mood_log: mood_log[uid] = {}
        mood_log[uid][today] = mood_name
        bot.answer_callback_query(call.id)
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Пользователь чувствует себя: {mood_name}. Отреагируй с заботой и предложи что-то полезное.", custom_system=SYSTEM_PROMPT)
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Mood error: {e}")
            bot.send_message(uid, f"Поняла, ты чувствуешь себя: {mood_name}. Я здесь для тебя! 🌸")
        return

    if data == "btn_planner":
        bot.answer_callback_query(call.id)
        modes[uid] = "planner_mode"
        bot.send_message(uid, "🗓 Опиши свои дела или цели — составлю план на день:")
        return

    if data == "btn_todo":
        bot.answer_callback_query(call.id)
        todos = todo_list.get(uid, [])
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("➕ Добавить дело", callback_data="todo_add"),
               types.InlineKeyboardButton("🗑 Очистить всё", callback_data="todo_clear"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        if todos:
            text = "📝 Список дел:\n\n" + "\n".join([f"{i+1}. {t}" for i, t in enumerate(todos)])
        else:
            text = "📝 Список дел пуст. Добавь первое дело!"
        bot.send_message(uid, text, reply_markup=kb)
        return

    if data == "todo_add":
        bot.answer_callback_query(call.id)
        modes[uid] = "todo_mode"
        bot.send_message(uid, "➕ Напиши дело для добавления:")
        return

    if data == "todo_clear":
        todo_list[uid] = []
        bot.answer_callback_query(call.id, "🗑 Очищено!")
        bot.send_message(uid, "🗑 Список дел очищен!", reply_markup=main_menu_kb(u))
        return

    if data == "btn_reminders":
        bot.answer_callback_query(call.id)
        rems = db.get_reminders(uid)
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        if rems:
            text = "⏰ Напоминания:\n\n"
            for i, r in enumerate(rems):
                d = "🔁" if r.get("daily") else "📅"
                text += f"{i+1}. {d} {r['time']} — {r['text']}\n"
            text += "\n/remind ЧЧ:ММ текст — добавить"
        else:
            text = "⏰ Нет напоминаний.\n\n/remind 18:00 текст — добавить"
        bot.send_message(uid, text, reply_markup=kb)
        return

    if data == "btn_notes":
        bot.answer_callback_query(call.id)
        notes = db.get_notes(uid)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("➕ Добавить", callback_data="note_add"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        if notes:
            text = "📓 Дневник:\n\n"
            for n in notes[-10:]:
                date = n.get("date", "")[:10]
                text += f"📅 {date}\n{n['text']}\n\n"
        else:
            text = "📓 Дневник пуст. Добавь первую запись!"
        bot.send_message(uid, text[-4000:], reply_markup=kb)
        return

    if data == "note_add":
        bot.answer_callback_query(call.id)
        modes[uid] = "note_mode"
        bot.send_message(uid, "📓 Напиши запись для дневника:")
        return

    if data == "btn_memory":
        bot.answer_callback_query(call.id)
        mem = db.get_memory(uid)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✏️ Обновить", callback_data="memory_update"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        if mem:
            text = "🧠 Что я о тебе знаю:\n\n"
            for k, v in mem.items():
                text += f"• {k}: {v}\n"
        else:
            text = "🧠 Я пока ничего о тебе не знаю. Расскажи о себе!"
        bot.send_message(uid, text, reply_markup=kb)
        return

    if data == "memory_update":
        bot.answer_callback_query(call.id)
        modes[uid] = "memory_mode"
        bot.send_message(uid, "🧠 Расскажи о себе (имя, возраст, город, интересы):")
        return

    if data == "btn_referral":
        bot.answer_callback_query(call.id)
        code = db.get_ref_code(uid)
        link = f"https://t.me/{bot.get_me().username}?start={code}"
        ref = db.r.get(f"referral:{uid}") or {}
        bot.send_message(uid,
            f"🔗 Реферальная программа!\n\n"
            f"За каждого приглашённого — +7 дней подписки 🎁\n\n"
            f"Твоя ссылка:\n{link}\n\n"
            f"👥 Приглашено: {len(ref.get('invited', []))}\n"
            f"🎁 Бонус начислено: {ref.get('bonus_days', 0)} дней",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")
            ))
        return

    if data == "btn_save_note":
        bot.answer_callback_query(call.id)
        if uid in last_answer:
            db.add_note(uid, last_answer[uid])
            bot.answer_callback_query(call.id, "📓 Сохранено в дневник!")
        else:
            bot.answer_callback_query(call.id, "Нечего сохранять")
        return

    if data == "btn_voice_last":
        bot.answer_callback_query(call.id)
        if uid not in last_answer or not VOICE_ENABLED:
            bot.answer_callback_query(call.id, "🔇 Голос недоступен")
            return
        try:
            tts = gTTS(last_answer[uid], lang="ru")
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tts.save(f.name)
                with open(f.name, "rb") as audio_f:
                    bot.send_voice(uid, audio_f)
            os.unlink(f.name)
        except Exception as e:
            log_event(f"TTS error: {e}")
            bot.send_message(uid, "😔 Голос временно недоступен")
        return

    if data == "btn_explain_simple":
        bot.answer_callback_query(call.id)
        if uid not in last_answer:
            bot.send_message(uid, "Нечего объяснять — задай вопрос сначала!")
            return
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, "Объясни предыдущий ответ ещё проще, как для 10-летнего ребёнка.")
            last_answer[uid] = answer
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Explain simple error: {e}")
            bot.send_message(uid, "😔 Попробуй ещё раз!")
        return

    if data == "btn_elaborate":
        bot.answer_callback_query(call.id)
        if uid not in last_answer:
            bot.send_message(uid, "Нечего расширять — задай вопрос сначала!")
            return
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, "Расскажи подробнее о предыдущей теме. Добавь примеры, детали, интересные факты.")
            last_answer[uid] = answer
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Elaborate error: {e}")
            bot.send_message(uid, "😔 Попробуй ещё раз!")
        return

    bot.answer_callback_query(call.id)


# ════════════════════════════════════════════════════════
#  ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ
# ════════════════════════════════════════════════════════

@bot.message_handler(content_types=["text"])
def handle_text(msg):
    uid = msg.from_user.id; text = msg.text.strip(); u = msg.from_user.username or ""

    db.register_user(uid, u, msg.from_user.first_name or "")

    # ── РЕЖИМЫ ВВОДА ──
    mode = modes.get(uid, "normal")

    if mode == "weather_mode":
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        bot.reply_to(msg, get_weather(text))
        return

    if mode == "imagine_mode":
        if not check_and_count(msg): return
        modes[uid] = "normal"
        _gen_img(msg.chat.id, uid, text)
        return

    if mode == "love_mode":
        if not check_and_count(msg): return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Напиши красивое любовное письмо для {text}. Романтично, нежно, искренне.", custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Love mode error: {e}")
            bot.reply_to(msg, "😔 Попробуй ещё раз!")
        return

    if mode == "recipe_mode":
        if not check_and_count(msg): return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Дай подробный рецепт: {text}. Ингредиенты и пошаговое приготовление.", custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Recipe error: {e}")
            bot.reply_to(msg, "😔 Попробуй ещё раз!")
        return

    if mode == "summarize_mode":
        if not check_and_count(msg): return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Сделай краткий пересказ этого текста на русском:\n\n{text}", custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Summarize error: {e}")
            bot.reply_to(msg, "😔 Попробуй ещё раз!")
        return

    if mode and mode.startswith("translate_") and mode.endswith("_mode"):
        if not check_and_count(msg): return
        lang = mode.replace("translate_", "").replace("_mode", "")
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Переведи на {lang}:\n\n{text}", custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Translate error: {e}")
            bot.reply_to(msg, "😔 Попробуй ещё раз!")
        return

    if mode == "planner_mode":
        if not check_and_count(msg): return
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            answer = ask_ai(uid, f"Составь детальный план дня на основе: {text}. Разбей по времени, добавь советы по продуктивности.", custom_system=SYSTEM_PROMPT)
            last_answer[uid] = answer
            send_safe(uid, answer, kb=after_kb())
        except Exception as e:
            log_event(f"Planner error: {e}")
            bot.reply_to(msg, "😔 Попробуй ещё раз!")
        return

    if mode == "todo_mode":
        todo_list.setdefault(uid, []).append(text)
        modes[uid] = "normal"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("📝 Список", callback_data="btn_todo"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.reply_to(msg, f"✅ Добавлено: {text}", reply_markup=kb)
        return

    if mode == "note_mode":
        db.add_note(uid, text)
        modes[uid] = "normal"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("📓 Дневник", callback_data="btn_notes"),
               types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.reply_to(msg, "📓 Запись сохранена!", reply_markup=kb)
        return

    if mode == "memory_mode":
        modes[uid] = "normal"
        bot.send_chat_action(uid, "typing")
        try:
            extracted = ask_claude_raw(
                [{"role": "user", "content": f"Из текста извлеки данные о пользователе. Верни ТОЛЬКО JSON: {{\"name\":\"...\",\"age\":\"...\",\"city\":\"...\",\"interests\":\"...\",\"about\":\"...\"}}. Если поле не упомянуто — не включай его. Текст: {text}"}],
                system="Ты извлекаешь данные из текста и возвращаешь только JSON.",
                max_tokens=200
            )
            try:
                data_json = json.loads(extracted)
                for k, v in data_json.items():
                    if v: db.save_memory(uid, k, v)
                bot.reply_to(msg, "🧠 Запомнила! Теперь буду обращаться персонально 🌸",
                             reply_markup=types.InlineKeyboardMarkup().add(
                                 types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu")
                             ))
            except:
                db.save_memory(uid, "about", text)
                bot.reply_to(msg, "🧠 Сохранила информацию о тебе! 🌸")
        except Exception as e:
            log_event(f"Memory mode error: {e}")
            bot.reply_to(msg, "😔 Попробуй ещё раз!")
        return

    # ── ADMIN РЕЖИМЫ ──
    if mode == "adm_grant_mode" and is_admin(u):
        modes[uid] = "normal"
        try:
            t = int(text.strip()); db.set_user(t, None, plan="forever"); db.unblock(t)
            bot.reply_to(msg, f"✅ Бессрочная выдана: {t}")
            try: bot.send_message(t, "🎉 Тебе выдан бессрочный доступ к Лие! /start 🌸")
            except: pass
        except: bot.reply_to(msg, "❌ Неверный ID")
        return

    if mode == "adm_revoke_mode" and is_admin(u):
        modes[uid] = "normal"
        try:
            t = int(text.strip()); db.remove_user(t)
            bot.reply_to(msg, f"✅ Подписка отозвана: {t}")
        except: bot.reply_to(msg, "❌ Неверный ID")
        return

    if mode == "adm_block_mode" and is_admin(u):
        modes[uid] = "normal"
        try:
            t = int(text.strip()); db.block(t)
            bot.reply_to(msg, f"🚫 Заблокирован: {t}")
        except: bot.reply_to(msg, "❌ Неверный ID")
        return

    if mode == "adm_unblock_mode" and is_admin(u):
        modes[uid] = "normal"
        try:
            t = int(text.strip()); db.unblock(t)
            bot.reply_to(msg, f"✅ Разблокирован: {t}")
        except: bot.reply_to(msg, "❌ Неверный ID")
        return

    if mode and mode.startswith("adm_time_") and mode.endswith("_mode") and is_admin(u):
        days = int(mode.split("_")[2])
        modes[uid] = "normal"
        try:
            t = int(text.strip())
            exp = datetime.now() + timedelta(days=days)
            db.set_user(t, exp, plan=f"{days}days")
            bot.reply_to(msg, f"✅ {t} → {days} дней до {exp.strftime('%d.%m.%Y')}")
            try: bot.send_message(t, f"🎉 Тебе выдано {days} дней подписки Лия! 🌸")
            except: pass
        except: bot.reply_to(msg, "❌ Неверный ID")
        return

    if mode == "adm_broadcast_mode" and is_admin(u):
        modes[uid] = "normal"
        all_uids = db.r.smembers("all_uids")
        bot.reply_to(msg, f"📢 Рассылка запущена ({len(all_uids)} получателей)...")

        def _do_broadcast(text_to_send, uids, admin_uid):
            sent = failed = 0
            for uid_str in uids:
                try:
                    bot.send_message(int(uid_str), f"📢 Сообщение от Лии:\n\n{text_to_send}")
                    sent += 1
                    time.sleep(0.05)
                except Exception:
                    failed += 1
            try:
                bot.send_message(admin_uid, f"📢 Рассылка завершена!\n✅ Отправлено: {sent}\n❌ Ошибок: {failed}")
            except Exception:
                pass

        threading.Thread(target=_do_broadcast, args=(text, list(all_uids), uid), daemon=True).start()
        return

    # ── ОБЫЧНЫЙ ЧАТ ──
    if not check_and_count(msg): return

    bot.send_chat_action(uid, "typing")
    wait = None

    try:
        # Для длинных запросов показываем индикатор
        if len(text) > 100:
            wait = bot.reply_to(msg, "⌛ Думаю...")

        answer = ask_ai(uid, text)
        last_answer[uid] = answer

        if wait:
            try: bot.delete_message(uid, wait.message_id)
            except: pass

        send_safe(uid, answer, reply_to=msg, kb=after_kb())

    except Exception as e:
        log_event(f"Text handler error uid={uid}: {e}")
        if wait:
            try: bot.delete_message(uid, wait.message_id)
            except: pass
        bot.reply_to(msg,
            "😔 Что-то пошло не так. Попробуй:\n\n"
            "• /new — начать новый диалог\n"
            "• Повторить вопрос\n"
            "• Написать позже"
        )


# ════════════════════════════════════════════════════════
#  ЗАПУСК
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    log_event(f"Liya bot v4.0 started | Claude: {CLAUDE_MODEL}")
    print(f"✅ Liya v4.0 | Claude API | Bot started")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)