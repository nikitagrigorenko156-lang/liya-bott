import telebot, requests, json, random, base64, os, re, tempfile, threading, time
from telebot import types
from datetime import datetime, timedelta
from urllib.parse import quote
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GROQ_KEY       = os.environ.get("GROQ_KEY", "")
UPSTASH_URL    = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN  = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

BOT_NAME         = "Лия"
PRICE_STARS      = 100
PAYMENT_LINK     = "https://t.me/tronqx"
TRIAL_DAYS       = 3
FREE_MSG_LIMIT   = 5
FREE_DAILY_LIMIT = 20
LOG_FILE         = "/tmp/liya_log.txt"
VIP_USERNAMES    = {"tronqx", "dhl1929"}
ADMIN_USERNAMES  = {"tronqx"}
MODEL_TEXT       = "deepseek-r1-distill-llama-70b"
MODEL_VISION     = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_WHISPER    = "whisper-large-v3-turbo"

SYSTEM_PROMPT = """Ты — Лия, умный AI-помощник и подруга. Всегда отвечаешь по-русски.
Характер: тёплая, заботливая, умная, с лёгким юмором.

ПРАВИЛА ФОРМАТИРОВАНИЯ:
1. НИКОГДА не используй LaTeX: никаких $, $$, frac, sqrt и обратных слешей
2. Математику пиши обычным текстом: дроби = 1/2, корни = корень(4), степени = x^2
3. Греческие буквы пиши символами: α β π σ (не slash-alpha)
4. Используй bullet-points через • для списков
5. При решении задач каждый шаг на новой строке

ЧТО УМЕЕШЬ:
- Решаешь любые задачи точно: математика, физика, химия, история
- Пишешь и объясняешь код на любом языке
- Анализируешь фото и решаешь задачи с фото
- Переводишь на любые языки
- Помогаешь с учёбой, работой, жизнью"""

def log_event(text):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {text}\n")
    except: pass

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
    text = re.sub(r'_\{([^}]+)\}', r'_\1', text)
    greek = {'alpha':'α','beta':'β','gamma':'γ','delta':'δ','epsilon':'ε',
             'theta':'θ','lambda':'λ','mu':'μ','pi':'π','sigma':'σ',
             'phi':'φ','omega':'ω','infty':'∞','pm':'±','times':'×','leq':'≤','geq':'≥','neq':'≠'}
    for eng, sym in greek.items():
        text = text.replace('\\'+eng+' ', sym+' ').replace('\\'+eng, sym)
    text = re.sub(r'\\[a-zA-Z]+\s?', '', text)
    text = re.sub(r'\{([^{}]*)\}', r'\1', text)
    text = re.sub(r'[{}]', '', text)
    text = re.sub(r'#{2,6}\s*', '', text)
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

class RedisClient:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
    def _cmd(self, *args):
        try:
            r = requests.post(self.url, headers=self.headers, json=list(args), timeout=10)
            return r.json().get("result")
        except Exception as e:
            log_event(f"Redis error: {e}")
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

class DataStore:
    def __init__(self):
        self.r = RedisClient(UPSTASH_URL, UPSTASH_TOKEN)

    def get_user(self, uid): return self.r.get(f"user:{str(uid).strip()}")

    def set_user(self, uid, expires, plan="paid"):
        uid = str(uid).strip()
        exp_str = expires.isoformat() if isinstance(expires, datetime) else (str(expires).strip() if expires else None)
        self.r.set(f"user:{uid}", {"expires": exp_str, "plan": plan})
        self.r.sadd("paid_uids", uid)
        log_event(f"set_user uid={uid} plan={plan} exp={exp_str}")

    def remove_user(self, uid):
        uid = str(uid).strip()
        self.r.delete(f"user:{uid}")
        self.r.srem("paid_uids", uid)

    def has_access(self, uid, username=""):
        uid = str(uid).strip()
        if username and username.lower().lstrip("@") in VIP_USERNAMES: return True
        if self.is_blocked(uid): return False
        u = self.get_user(uid)
        if not u: return False
        exp = u.get("expires")
        if exp is None: return True
        try:
            if datetime.now() < datetime.fromisoformat(str(exp).strip()): return True
            self.remove_user(uid); return False
        except: return True

    def sub_status(self, uid):
        uid = str(uid).strip()
        if self.is_blocked(uid): return "🚫 Заблокирован"
        u = self.get_user(uid)
        if not u: return "❌ Нет подписки"
        exp  = u.get("expires")
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
        st  = self.r.get(f"stats:{uid}")
        self.r.sadd("all_uids", uid)
        if not st:
            self.r.set(f"stats:{uid}", {"total_msgs":0,"daily":{},"joined":datetime.now().isoformat(),"username":username or "","name":name or ""})
            log_event(f"New user: {uid} @{username}")
            return True
        st["username"] = username or st.get("username","")
        st["name"]     = name or st.get("name","")
        self.r.set(f"stats:{uid}", st)
        return False

    def count_message(self, uid):
        uid   = str(uid).strip()
        today = datetime.now().strftime("%Y-%m-%d")
        mc    = self.r.get(f"msgcount:{uid}") or {"date":today,"count":0}
        if mc.get("date") != today: mc = {"date":today,"count":0}
        mc["count"] += 1
        self.r.set(f"msgcount:{uid}", mc)
        st = self.r.get(f"stats:{uid}") or {}
        st["total_msgs"] = st.get("total_msgs",0) + 1
        daily = st.get("daily",{})
        daily[today] = daily.get(today,0) + 1
        st["daily"] = daily
        self.r.set(f"stats:{uid}", st)
        return mc["count"], st["total_msgs"]

    def get_daily_count(self, uid):
        uid   = str(uid).strip()
        today = datetime.now().strftime("%Y-%m-%d")
        mc    = self.r.get(f"msgcount:{uid}") or {}
        return mc.get("count",0) if mc.get("date")==today else 0

    def get_analytics(self):
        all_uids  = self.r.smembers("all_uids")
        paid_uids = self.r.smembers("paid_uids")
        blocked   = self.r.smembers("blocked")
        today     = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
        week_ago  = (datetime.now()-timedelta(days=7)).isoformat()
        total_msgs=dau_today=dau_yest=new_week=0
        top_list=[]
        for uid in all_uids:
            st = self.r.get(f"stats:{uid}") or {}
            total_msgs += st.get("total_msgs",0)
            if today    in st.get("daily",{}): dau_today+=1
            if yesterday in st.get("daily",{}): dau_yest+=1
            if st.get("joined","0") > week_ago: new_week+=1
            top_list.append((uid,st))
        top_list.sort(key=lambda x: x[1].get("total_msgs",0), reverse=True)
        return {"total_users":len(all_uids),"paid_users":len(paid_uids),"blocked":len(blocked),
                "dau_today":dau_today,"dau_yest":dau_yest,"total_msgs":total_msgs,
                "new_week":new_week,"top_users":top_list[:5]}

    def save_memory(self, uid, key, value):
        uid = str(uid).strip()
        mem = self.r.get(f"memory:{uid}") or {}
        mem[key] = value
        self.r.set(f"memory:{uid}", mem)

    def get_memory(self, uid): return self.r.get(f"memory:{uid}") or {}

    def get_memory_context(self, uid):
        mem = self.get_memory(uid)
        parts = []
        if mem.get("name"):      parts.append(f"Имя: {mem['name']}")
        if mem.get("age"):       parts.append(f"Возраст: {mem['age']}")
        if mem.get("birthday"):  parts.append(f"День рождения: {mem['birthday']}")
        if mem.get("city"):      parts.append(f"Город: {mem['city']}")
        if mem.get("interests"): parts.append(f"Интересы: {mem['interests']}")
        if mem.get("about"):     parts.append(f"О себе: {mem['about']}")
        return ("\n\nЧто ты знаешь о пользователе:\n" + "\n".join(parts)) if parts else ""

    def get_ref_code(self, uid):
        uid  = str(uid).strip()
        data = self.r.get(f"referral:{uid}")
        if not data:
            data = {"code":f"ref{uid}","invited":[],"bonus_days":0}
            self.r.set(f"referral:{uid}", data)
        return data["code"]

    def apply_referral(self, new_uid, ref_code):
        new_uid  = str(new_uid).strip()
        for owner_uid in self.r.smembers("all_uids"):
            rd = self.r.get(f"referral:{owner_uid}")
            if rd and rd.get("code")==ref_code and new_uid not in rd.get("invited",[]) and owner_uid!=new_uid:
                rd["invited"] = rd.get("invited",[]) + [new_uid]
                rd["bonus_days"] = rd.get("bonus_days",0) + 7
                self.r.set(f"referral:{owner_uid}", rd)
                try:
                    cur = self.get_user(int(owner_uid))
                    if cur and cur.get("expires"):
                        exp = datetime.fromisoformat(str(cur["expires"]).strip())
                        self.set_user(int(owner_uid), exp+timedelta(days=7), plan=cur.get("plan","paid"))
                    else:
                        self.set_user(int(owner_uid), datetime.now()+timedelta(days=7), plan="referral")
                except: pass
                return int(owner_uid)
        return None

    def add_note(self, uid, text):
        uid   = str(uid).strip()
        notes = self.r.get(f"notes:{uid}") or []
        notes.append({"text":text,"date":datetime.now().isoformat()})
        self.r.set(f"notes:{uid}", notes[-50:])

    def get_notes(self, uid): return self.r.get(f"notes:{uid}") or []

    def add_reminder(self, uid, text, time_str, daily=False):
        uid = str(uid).strip()
        rems = self.r.get(f"reminders:{uid}") or []
        rems.append({"text":text,"time":time_str,"daily":daily,"created":datetime.now().isoformat()})
        self.r.set(f"reminders:{uid}", rems)

    def get_reminders(self, uid): return self.r.get(f"reminders:{uid}") or []

    def remove_reminder(self, uid, idx):
        uid  = str(uid).strip()
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

db  = DataStore()
bot = telebot.TeleBot(TELEGRAM_TOKEN)
histories={};modes={};mood_log={};todo_list={};last_answer={};quiz_state={}
MAX_HISTORY=20
try:
    from gtts import gTTS
    VOICE_ENABLED=True
except: VOICE_ENABLED=False

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        try:
            a=db.get_analytics()
            self.wfile.write(f"Liya v3.0 | Users:{a['total_users']} Paid:{a['paid_users']} DAU:{a['dau_today']}".encode())
        except: self.wfile.write(b"Liya v3.0 OK")
    def log_message(self,*a): pass

threading.Thread(target=lambda: HTTPServer(("0.0.0.0",int(os.environ.get("PORT",10000))),Handler).serve_forever(), daemon=True).start()

def reminder_scheduler():
    while True:
        try:
            now_str = datetime.now().strftime("%H:%M")
            for uid_str, rems in list(db.all_reminders().items()):
                to_del = []
                for i, r in enumerate(rems):
                    if r.get("time")==now_str:
                        try:
                            kb=types.InlineKeyboardMarkup()
                            kb.add(types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
                            d="🔁 Ежедневное\n" if r.get("daily") else ""
                            bot.send_message(int(uid_str),f"⏰ Напоминание!\n\n{r['text']}\n\n{d}",reply_markup=kb)
                        except: pass
                        if not r.get("daily"): to_del.append(i)
                for idx in reversed(to_del): db.remove_reminder(uid_str,idx)
        except Exception as e: log_event(f"Reminder: {e}")
        time.sleep(60)

threading.Thread(target=reminder_scheduler,daemon=True).start()

def get_greeting():
    h=datetime.now().hour
    if 5<=h<12: return "☀️ Доброе утро"
    if 12<=h<17: return "🌤 Добрый день"
    if 17<=h<22: return "🌆 Добрый вечер"
    return "🌙 Привет"

def get_history(uid):
    if uid not in histories: histories[uid]=[]
    return histories[uid]

def is_admin(u): return u and u.lower().lstrip("@") in ADMIN_USERNAMES
def is_vip(u): return u and u.lower().lstrip("@") in VIP_USERNAMES

def mode_system(uid):
    base = SYSTEM_PROMPT + db.get_memory_context(uid)
    m    = modes.get(uid,"normal")
    if m=="study":   base+="\n\nРежим УЧЁБЫ: объясняй по шагам с примерами."
    if m=="support": base+="\n\nРежим ПОДДЕРЖКИ: будь нежной и заботливой."
    if m=="creative":base+="\n\nРежим ТВОРЧЕСТВА: предлагай необычные идеи."
    return base

def mode_name(uid):
    return {"normal":"💬 Обычный","study":"📚 Учёба","support":"🤗 Поддержка","creative":"🎨 Творчество"}.get(modes.get(uid,"normal"),"💬 Обычный")

def check_daily_limit(uid,username=""):
    if is_vip(username): return True,0,9999
    u=db.get_user(uid)
    count=db.get_daily_count(uid)
    if not u: return count<FREE_MSG_LIMIT,count,FREE_MSG_LIMIT
    if u.get("plan")=="trial": return count<FREE_DAILY_LIMIT,count,FREE_DAILY_LIMIT
    return True,count,9999

def ask_ai(uid, text, image_b64=None, custom_system=None):
    history = get_history(uid)
    sys_msg = custom_system or mode_system(uid)
    if image_b64:
        msgs  = [{"role":"system","content":sys_msg},
                 {"role":"user","content":[
                     {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_b64}"}},
                     {"type":"text","text":text or "Что на фото? Если есть задачи — реши пошагово."}
                 ]}]
        model=MODEL_VISION; timeout=60
    else:
        history.append({"role":"user","content":text})
        if len(history)>MAX_HISTORY: histories[uid]=history[-MAX_HISTORY:]; history=histories[uid]
        msgs=[{"role":"system","content":sys_msg}]+history
        model=MODEL_TEXT; timeout=45

    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization":f"Bearer {GROQ_KEY}","Content-Type":"application/json"},
        data=json.dumps({"model":model,"messages":msgs,"max_tokens":2000}), timeout=timeout)
    data=r.json()
    if "error" in data: raise Exception(data["error"].get("message","Ошибка API"))
    answer=clean_response(data["choices"][0]["message"]["content"])
    if not image_b64: history.append({"role":"assistant","content":answer})
    return answer

def transcribe_voice(audio, fname="voice.ogg"):
    r=requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization":f"Bearer {GROQ_KEY}"},
        files={"file":(fname,audio,"audio/ogg")},
        data={"model":MODEL_WHISPER,"language":"ru","response_format":"text"},timeout=30)
    if r.status_code==200: return r.text.strip()
    raise Exception(f"Whisper {r.status_code}")

def generate_image(prompt_ru):
    # Переводим на английский
    prompt_en = prompt_ru
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            data=json.dumps({"model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": f"Translate to English for image generation, respond with translation only, no extra words: {prompt_ru}"}],
                "max_tokens": 150}), timeout=15)
        translated = r.json()["choices"][0]["message"]["content"].strip()
        if translated and len(translated) < 500:
            prompt_en = translated
    except Exception as e:
        log_event(f"Translation error: {e}")

    log_event(f"Generating image: {prompt_en[:80]}")

    # Пробуем разные модели Pollinations
    for model in ["flux", "turbo", "flux-realism"]:
        for attempt in range(2):
            try:
                seed = random.randint(1, 99999)
                url = f"https://image.pollinations.ai/prompt/{quote(prompt_en)}?model={model}&width=1024&height=1024&seed={seed}&nologo=true&safe=false"
                resp = requests.get(url, timeout=90, stream=True)
                if resp.status_code == 200:
                    data = resp.content
                    if len(data) > 1000:
                        log_event(f"Image OK: model={model} size={len(data)}")
                        return data
                log_event(f"Image attempt failed: model={model} status={resp.status_code}")
            except Exception as e:
                log_event(f"Image error model={model}: {e}")
                time.sleep(2)

    # Последний шанс - простой запрос
    try:
        simple_url = f"https://image.pollinations.ai/prompt/{quote(prompt_en)}"
        resp = requests.get(simple_url, timeout=90)
        if resp.status_code == 200 and len(resp.content) > 1000:
            return resp.content
    except Exception as e:
        log_event(f"Final image attempt failed: {e}")

    return None

def get_weather(city):
    try:
        r=requests.get(f"https://wttr.in/{quote(city)}?format=j1&lang=ru",timeout=10)
        c=r.json()["current_condition"][0]
        return f"🌤 Погода в {city}:\n\n🌡 {c['temp_C']}°C (ощущается {c['FeelsLikeC']}°C)\n☁️ {c['lang_ru'][0]['value']}\n💧 Влажность: {c['humidity']}%\n💨 Ветер: {c['windspeedKmph']} км/ч"
    except: return f"😔 Не нашла погоду для '{city}'."

def get_currency():
    try:
        rates=requests.get("https://api.exchangerate-api.com/v4/latest/RUB",timeout=10).json().get("rates",{})
        return f"💰 Курс валют:\n\n🇺🇸 1 USD = {round(1/rates.get('USD',0.011),2)} ₽\n🇪🇺 1 EUR = {round(1/rates.get('EUR',0.010),2)} ₽\n🇰🇿 1 ₽ = {round(rates.get('KZT',5.5),2)} ₸\n\nОбновлено ⏱"
    except: return "😔 Не могу получить курс."

def notify_admin(text):
    for uid in db.r.smembers("all_uids"):
        st=db.r.get(f"stats:{uid}") or {}
        if st.get("username","").lower() in ADMIN_USERNAMES:
            try: bot.send_message(int(uid),f"🔔 {text}")
            except: pass

def send_long(chat_id, text, reply_to=None, kb=None):
    for i,chunk in enumerate([text[j:j+4096] for j in range(0,len(text),4096)]):
        is_last=(i==len(text[::4096]))
        markup=kb if (i==len(range(0,len(text),4096))-1) else None
        try:
            if reply_to and i==0: bot.reply_to(reply_to,chunk,reply_markup=markup)
            else: bot.send_message(chat_id,chunk,reply_markup=markup)
        except: pass

def send_safe(chat_id, text, reply_to=None, kb=None):
    """Отправляет сообщение частями если длинное."""
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for i, chunk in enumerate(chunks):
        markup = kb if i == len(chunks)-1 else None
        try:
            if reply_to and i == 0:
                bot.reply_to(reply_to, chunk, reply_markup=markup)
            else:
                bot.send_message(chat_id, chunk, reply_markup=markup)
        except Exception as e:
            log_event(f"send_safe error: {e}")

# ── КЛАВИАТУРЫ ──
ZODIAC_SIGNS=["♈ Овен","♉ Телец","♊ Близнецы","♋ Рак","♌ Лев","♍ Дева","♎ Весы","♏ Скорпион","♐ Стрелец","♑ Козерог","♒ Водолей","♓ Рыбы"]
QUIZ_TOPICS={"🌍 География":"geography","🎬 Кино":"movies","🎵 Музыка":"music","🧪 Наука":"science","📚 Литература":"literature","🏆 Спорт":"sport","🍕 Еда":"food","💄 Красота":"beauty","🐾 Животные":"animals","🌟 Случайное":"random"}
COMPLIMENTS=["Ты просто замечательная! ✨","Ты умница и красавица 💕","С тобой всегда интересно! 🌸","Ты справишься со всем, верю в тебя 💪","Ты особенная 🦋"]
AFFIRMATIONS=["Я достойна любви и счастья 💕","Я справляюсь со всем 💪","Каждый день я становлюсь лучше ✨","Мои мечты реальны 🎯","Я верю в себя 🦋"]
MEDITATIONS=[
    {"name":"🌬 Дыхание 4-7-8","text":"Снимает тревогу:\n\n1. Вдох — 4 сек\n2. Задержка — 7 сек\n3. Выдох — 8 сек\n\nПовтори 4 раза 🌿"},
    {"name":"🧘 5-4-3-2-1","text":"Назови:\n\n5 вещей которые видишь\n4 которые потрогаешь\n3 звука\n2 запаха\n1 вкус\n\nВозвращает в момент 💙"},
    {"name":"💤 Для сна","text":"Перед сном:\n\n• Напряги всё тело 5 сек\n• Резко расслабь\n• Медленно дыши\n• Думай о приятном 😴"},
]
MOOD_EMOJIS={"😊":"Хорошо","🤩":"Отлично","😔":"Грустно","😤":"Злюсь","😰":"Тревожно","😴":"Устала","🥰":"Влюблена","😐":"Нейтрально"}
LANGUAGES={"🇬🇧 Английский":"English","🇩🇪 Немецкий":"German","🇫🇷 Французский":"French","🇪🇸 Испанский":"Spanish","🇨🇳 Китайский":"Chinese","🇯🇵 Японский":"Japanese","🇰🇷 Корейский":"Korean","🇹🇷 Турецкий":"Turkish"}

def main_menu_kb(username=""):
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💬 Поговорить",      callback_data="mode_normal"),
        types.InlineKeyboardButton("📚 Учёба",           callback_data="mode_study"),
        types.InlineKeyboardButton("🤗 Поддержка",       callback_data="mode_support"),
        types.InlineKeyboardButton("🎨 Творчество",      callback_data="mode_creative"),
        types.InlineKeyboardButton("🖼 Нарисовать",      callback_data="btn_imagine"),
        types.InlineKeyboardButton("🧠 Викторина",       callback_data="btn_quiz"),
        types.InlineKeyboardButton("🌙 Гороскоп",        callback_data="btn_horoscope"),
        types.InlineKeyboardButton("💄 Уход за собой",   callback_data="btn_beauty"),
        types.InlineKeyboardButton("💌 Любовное письмо", callback_data="btn_love"),
        types.InlineKeyboardButton("📖 Пересказ текста", callback_data="btn_summarize"),
        types.InlineKeyboardButton("🌍 Переводчик",      callback_data="btn_translate"),
        types.InlineKeyboardButton("💰 Курс валют",      callback_data="btn_currency"),
        types.InlineKeyboardButton("🌤 Погода",          callback_data="btn_weather"),
        types.InlineKeyboardButton("🍽 Рецепт",          callback_data="btn_recipe"),
        types.InlineKeyboardButton("🧘 Медитация",       callback_data="btn_meditation"),
        types.InlineKeyboardButton("📊 Настроение",      callback_data="btn_mood"),
        types.InlineKeyboardButton("🗓 Планировщик",     callback_data="btn_planner"),
        types.InlineKeyboardButton("📝 Список дел",      callback_data="btn_todo"),
        types.InlineKeyboardButton("⏰ Напоминания",     callback_data="btn_reminders"),
        types.InlineKeyboardButton("📓 Дневник",         callback_data="btn_notes"),
        types.InlineKeyboardButton("🧠 Моя память",      callback_data="btn_memory"),
        types.InlineKeyboardButton("🔗 Пригласить",      callback_data="btn_referral"),
        types.InlineKeyboardButton("🤣 Шутка",           callback_data="btn_joke"),
        types.InlineKeyboardButton("🌟 Факт дня",        callback_data="btn_fact"),
        types.InlineKeyboardButton("💪 Мотивация",       callback_data="btn_motivation"),
        types.InlineKeyboardButton("✨ Комплимент",      callback_data="btn_compliment"),
        types.InlineKeyboardButton("📸 Анализ фото",     callback_data="btn_photo_hint"),
        types.InlineKeyboardButton("📱 Мой аккаунт",    callback_data="btn_account"),
        types.InlineKeyboardButton("🔄 Новый диалог",    callback_data="btn_new"),
        types.InlineKeyboardButton("ℹ️ Помощь",          callback_data="btn_help"),
        types.InlineKeyboardButton("👨‍💻 Разработчик",     url="https://t.me/tronqx"),
    )
    if is_admin(username):
        kb.add(types.InlineKeyboardButton("👑 Админ-панель", callback_data="adm_panel"))
    return kb

def after_kb():
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Меню",           callback_data="btn_menu"),
        types.InlineKeyboardButton("🎵 Голос",          callback_data="btn_voice_last"),
        types.InlineKeyboardButton("🧠 Объясни проще",  callback_data="btn_explain_simple"),
        types.InlineKeyboardButton("📖 Подробнее",      callback_data="btn_elaborate"),
        types.InlineKeyboardButton("📓 В дневник",      callback_data="btn_save_note"),
        types.InlineKeyboardButton("🔄 Новый диалог",   callback_data="btn_new"),
    )
    return kb

def access_kb():
    kb=types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🎁 3 дня бесплатно",       callback_data="btn_trial"),
        types.InlineKeyboardButton("⭐ Оплатить Stars",         callback_data="btn_pay_stars"),
        types.InlineKeyboardButton("💳 Написать @tronqx",      url=PAYMENT_LINK),
    )
    return kb

def admin_kb():
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📊 Аналитика",        callback_data="adm_stats"),
        types.InlineKeyboardButton("👥 Пользователи",     callback_data="adm_users"),
        types.InlineKeyboardButton("➕ Бессрочная",       callback_data="adm_grant"),
        types.InlineKeyboardButton("🕐 На время",         callback_data="adm_grant_time"),
        types.InlineKeyboardButton("❌ Отозвать",         callback_data="adm_revoke"),
        types.InlineKeyboardButton("🚫 Заблок",           callback_data="adm_block"),
        types.InlineKeyboardButton("✅ Разблок",          callback_data="adm_unblock"),
        types.InlineKeyboardButton("📢 Рассылка",         callback_data="adm_broadcast"),
        types.InlineKeyboardButton("🔙 Меню",             callback_data="btn_menu"),
    )
    return kb

def time_kb(target=""):
    kb=types.InlineKeyboardMarkup(row_width=3)
    p=f"adm_time_{target}_" if target else "adm_time__"
    kb.add(
        types.InlineKeyboardButton("1 день",  callback_data=f"{p}1"),
        types.InlineKeyboardButton("3 дня",   callback_data=f"{p}3"),
        types.InlineKeyboardButton("7 дней",  callback_data=f"{p}7"),
        types.InlineKeyboardButton("14 дней", callback_data=f"{p}14"),
        types.InlineKeyboardButton("30 дней", callback_data=f"{p}30"),
        types.InlineKeyboardButton("90 дней", callback_data=f"{p}90"),
        types.InlineKeyboardButton("🔙 Назад",callback_data="adm_panel"),
    )
    return kb

# ── ПРОВЕРКА ДОСТУПА ──
def check_access(msg):
    uid=msg.from_user.id; u=msg.from_user.username or ""
    if is_vip(u): return True
    if db.is_blocked(str(uid)): bot.reply_to(msg,"🚫 Заблокирована!"); return False
    if db.has_access(uid,u): return True
    bot.reply_to(msg,f"🔒 Доступ платный\n\n⭐ {PRICE_STARS} Stars\n\n🎁 Или 3 дня бесплатно:",reply_markup=access_kb())
    return False

def check_access_cb(call):
    uid=call.from_user.id; u=call.from_user.username or ""
    if is_vip(u): return True
    if db.is_blocked(str(uid)): bot.answer_callback_query(call.id,"🚫 Заблокирована!"); return False
    if db.has_access(uid,u): return True
    bot.answer_callback_query(call.id,"🔒 Нет доступа!")
    bot.send_message(uid,f"🔒 Нужна подписка!\n\n⭐ {PRICE_STARS} Stars или @tronqx",reply_markup=access_kb())
    return False

def check_and_count(msg):
    uid=msg.from_user.id; u=msg.from_user.username or ""
    if not check_access(msg): return False
    ok,count,limit=check_daily_limit(uid,u)
    if not ok:
        plan=(db.get_user(uid) or {}).get("plan","free")
        if plan=="trial": bot.reply_to(msg,f"⚠️ Лимит пробного: {limit} сообщений/день\n\nКупи полный доступ!",reply_markup=access_kb())
        else: bot.reply_to(msg,f"⚠️ Лимит {limit}/день исчерпан.")
        return False
    db.count_message(uid)
    return True

# ── КОМАНДЫ ──
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid=msg.from_user.id; u=msg.from_user.username or ""; name=msg.from_user.first_name or "Солнышко"
    is_new=db.register_user(uid,u,name)
    histories[uid]=[]; modes[uid]="normal"
    db.save_memory(uid,"name",name)
    ref_bonus=""
    parts=msg.text.split()
    if len(parts)>1 and parts[1].startswith("ref"):
        owner=db.apply_referral(uid,parts[1])
        if owner:
            ref_bonus="\n🎁 Реферальный бонус применён!"
            try: bot.send_message(owner,"🎉 По твоей ссылке зарегистрировались! +7 дней 🌸")
            except: pass
    if is_new: notify_admin(f"👤 Новый: {uid} @{u} {name}")
    bot.reply_to(msg,
        f"{get_greeting()}, {name}! ✨{ref_bonus}\n\n"
        f"Я Лия — твой умный AI-помощник 👑\n\n"
        f"💬 Общаюсь как ChatGPT\n📸 Решаю задачи по фото\n"
        f"🖼 Генерирую картинки\n🎤 Расшифровываю голосовые\n"
        f"🧮 Математика, физика, код\n🌙 Гороскоп, рецепты, переводчик\n\n"
        f"Выбери с чего начнём 👇",
        reply_markup=main_menu_kb(u))

@bot.message_handler(commands=["menu"])
def cmd_menu(msg):
    db.register_user(msg.from_user.id,msg.from_user.username or "",msg.from_user.first_name or "")
    bot.reply_to(msg,f"Меню 🌸 | Режим: {mode_name(msg.from_user.id)}",reply_markup=main_menu_kb(msg.from_user.username or ""))

@bot.message_handler(commands=["new"])
def cmd_new(msg):
    histories[msg.from_user.id]=[]; bot.reply_to(msg,"🔄 Начнём с чистого листа! 🌸")

@bot.message_handler(commands=["myid"])
def cmd_myid(msg): bot.reply_to(msg,f"Твой ID: {msg.from_user.id}")

@bot.message_handler(commands=["grant"])
def cmd_grant(msg):
    if not is_admin(msg.from_user.username or ""): return
    p=msg.text.split()
    if len(p)<2: bot.reply_to(msg,"Использование: /grant [id]"); return
    try:
        t=int(p[1]); db.set_user(t,None,plan="forever"); db.unblock(t)
        bot.reply_to(msg,f"✅ Бессрочная выдана {t}")
        try: bot.send_message(t,"🎉 Тебе выдан бессрочный доступ! /start 🌸")
        except: pass
    except: bot.reply_to(msg,"❌ Неверный ID")

@bot.message_handler(commands=["remind"])
def cmd_remind(msg):
    if not check_access(msg): return
    p=msg.text.split(maxsplit=2)
    if len(p)<3: bot.reply_to(msg,"⏰ Формат: /remind 18:00 выпить воду\nЕжедневно: /remind 08:00 зарядка каждый день"); return
    t=p[1]; txt=p[2]; daily=txt.endswith("каждый день")
    if daily: txt=txt[:-len("каждый день")].strip()
    if not re.match(r"^\d{2}:\d{2}$",t): bot.reply_to(msg,"❌ Формат времени: ЧЧ:ММ"); return
    db.add_reminder(msg.from_user.id,txt,t,daily=daily)
    bot.reply_to(msg,f"✅ Напоминание:\n⏰ {t}{' 🔁' if daily else ''}\n📝 {txt}")

@bot.message_handler(commands=["weather"])
def cmd_weather(msg):
    p=msg.text.split(maxsplit=1)
    if len(p)<2: bot.reply_to(msg,"🌤 Напиши город: /weather Москва"); return
    bot.send_chat_action(msg.chat.id,"typing"); bot.reply_to(msg,get_weather(p[1]))

# ── ГОЛОСОВЫЕ ──
@bot.message_handler(content_types=["voice"])
def handle_voice(msg):
    uid=msg.from_user.id
    if not check_and_count(msg): return
    bot.send_chat_action(msg.chat.id,"typing")
    wait=bot.reply_to(msg,"🎤 Слушаю...")
    try:
        fi=bot.get_file(msg.voice.file_id)
        audio=requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{fi.file_path}",timeout=20).content
        text=transcribe_voice(audio)
        try: bot.delete_message(uid,wait.message_id)
        except: pass
        if modes.get(uid)=="note_voice_mode":
            modes[uid]="normal"; db.add_note(uid,text)
            kb=types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📓 Дневник",callback_data="btn_notes"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            bot.send_message(uid,f"📓 Заметка сохранена!\n\n{text}",reply_markup=kb); return
        bot.send_message(uid,f"🎤 Ты сказала:\n{text}")
        bot.send_chat_action(uid,"typing")
        answer=ask_ai(uid,text); last_answer[uid]=answer
        send_safe(uid,answer,kb=after_kb())
    except Exception as e:
        log_event(f"Voice error: {e}")
        try: bot.delete_message(uid,wait.message_id)
        except: pass
        bot.reply_to(msg,"😔 Не смогла расшифровать. Попробуй ещё раз!")

# ── ФОТО ──
@bot.message_handler(content_types=["photo"])
def handle_photo(msg):
    uid=msg.from_user.id
    if not check_and_count(msg): return
    bot.send_chat_action(msg.chat.id,"typing")
    wait=None
    try:
        fi=bot.get_file(msg.photo[-1].file_id)
        resp=requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{fi.file_path}",timeout=30)
        if resp.status_code!=200: bot.reply_to(msg,"😔 Не смогла скачать фото. Попробуй ещё раз!"); return
        image_b64=base64.b64encode(resp.content).decode("utf-8")
        wait=bot.send_message(uid,"📸 Анализирую фото...")
        caption=msg.caption or "Подробно опиши что на фото. Если есть задачи, уравнения или текст — прочитай и реши/объясни пошагово. Без LaTeX."
        answer=ask_ai(uid,caption,image_b64=image_b64)
        last_answer[uid]=answer
        try: bot.delete_message(uid,wait.message_id)
        except: pass
        send_safe(uid,answer,reply_to=msg,kb=after_kb())
    except Exception as e:
        log_event(f"Photo error: {e}")
        try:
            if wait: bot.delete_message(uid,wait.message_id)
        except: pass
        bot.reply_to(msg,"😔 Ошибка при обработке фото.\n\nПопробуй:\n• Отправить заново\n• Уменьшить фото\n• Написать вопрос текстом")

# ── ПЛАТЕЖИ ──
@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q): bot.answer_pre_checkout_query(q.id,ok=True)

@bot.message_handler(content_types=["successful_payment"])
def successful_payment(msg):
    uid=msg.from_user.id; u=msg.from_user.username or ""; stars=msg.successful_payment.total_amount
    if stars>=500: exp=None; plan="forever"; days_text="навсегда"
    elif stars>=200: exp=datetime.now()+timedelta(days=90); plan="90days"; days_text="90 дней"
    else: exp=datetime.now()+timedelta(days=30); plan="30days"; days_text="30 дней"
    db.set_user(uid,exp,plan=plan); db.unblock(uid)
    bot.send_message(uid,f"🎉 Оплата прошла!\n\n⭐ {stars} Stars\n✅ Подписка: {days_text}\n\nВсе функции открыты! 🌸",reply_markup=main_menu_kb(u))
    notify_admin(f"💳 Оплата! {uid} @{u} {stars} Stars {plan}")

# ── CALLBACKS ──
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    uid=call.from_user.id; data=call.data; u=call.from_user.username or ""

    FREE_CB={"btn_menu","btn_help","btn_trial","btn_pay_stars","pay_stars_30","pay_stars_90","pay_stars_forever","btn_account","adm_panel","adm_back"}

    if data not in FREE_CB and not data.startswith("adm_") and not check_access_cb(call): return

    # ── ПРОБНЫЙ ПЕРИОД ──
    if data=="btn_trial":
        bot.answer_callback_query(call.id)
        if db.get_user(uid): bot.send_message(uid,f"У тебя уже есть подписка!\nСтатус: {db.sub_status(uid)}"); return
        exp=datetime.now()+timedelta(days=TRIAL_DAYS)
        db.set_user(uid,exp,plan="trial")
        log_event(f"Trial: {uid} @{u} exp={exp.isoformat()}")
        notify_admin(f"🎁 Пробный: {uid} @{u}")
        bot.send_message(uid,
            f"🎁 Пробный период активирован!\n\n✅ {TRIAL_DAYS} дня бесплатно\n"
            f"📊 Лимит: {FREE_DAILY_LIMIT} сообщений/день\n⏰ До: {exp.strftime('%d.%m.%Y')}\n\n"
            f"Теперь все функции доступны! 🌸",reply_markup=main_menu_kb(u))
        return

    # ── ОПЛАТА ──
    if data=="btn_pay_stars":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(f"⭐ {PRICE_STARS} Stars — 30 дней",callback_data="pay_stars_30"),
            types.InlineKeyboardButton("⭐ 200 Stars — 90 дней",callback_data="pay_stars_90"),
            types.InlineKeyboardButton("⭐ 500 Stars — Навсегда",callback_data="pay_stars_forever"),
            types.InlineKeyboardButton("💳 Написать @tronqx",url=PAYMENT_LINK),
        )
        bot.send_message(uid,"⭐ Выбери план:",reply_markup=kb)
        return

    if data.startswith("pay_stars_"):
        plan_key=data.replace("pay_stars_","")
        plans={"30":(PRICE_STARS,"30 дней"),"90":(200,"90 дней"),"forever":(500,"Навсегда")}
        if plan_key not in plans: bot.answer_callback_query(call.id); return
        amount,label=plans[plan_key]
        bot.answer_callback_query(call.id)
        try:
            bot.send_invoice(chat_id=uid,title=f"Лия — {label}",
                description=f"Полный доступ к боту на {label}",
                invoice_payload=f"stars_{plan_key}_{uid}",
                provider_token="",currency="XTR",
                prices=[types.LabeledPrice(label=label,amount=amount)])
        except Exception as e:
            log_event(f"Invoice error: {e}")
            kb=types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("💬 Написать @tronqx",url=PAYMENT_LINK))
            bot.send_message(uid,f"⭐ {label} — {amount} Stars\n\nНапиши @tronqx: «Подписка {label}, ID: {uid}»",reply_markup=kb)
        return

    # ── АККАУНТ ──
    if data=="btn_account":
        bot.answer_callback_query(call.id)
        st=db.r.get(f"stats:{uid}") or {}; ref=db.r.get(f"referral:{uid}") or {}
        code=db.get_ref_code(uid); link=f"https://t.me/{bot.get_me().username}?start={code}"
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("💳 Подписка",callback_data="btn_pay_stars"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        bot.send_message(uid,
            f"📱 Мой аккаунт\n\n🆔 ID: {uid}\n📅 С: {st.get('joined','?')[:10]}\n"
            f"💎 Статус: {db.sub_status(uid)}\n✉️ Сообщений сегодня: {db.get_daily_count(uid)}\n"
            f"✉️ Всего: {st.get('total_msgs',0)}\n\n🔗 Реф. ссылка:\n{link}\n👥 Приглашено: {len(ref.get('invited',[]))}",reply_markup=kb)
        return

    # ── МЕНЮ ──
    if data=="btn_menu":
        bot.answer_callback_query(call.id)
        bot.send_message(uid,f"Меню 🌸 | Режим: {mode_name(uid)}",reply_markup=main_menu_kb(u))
        return

    if data=="btn_new":
        histories[uid]=[]; bot.answer_callback_query(call.id,"🔄 Очищено!")
        bot.send_message(uid,"🔄 Новый диалог! Пиши что угодно 🌸")
        return

    if data=="btn_help":
        bot.answer_callback_query(call.id)
        bot.send_message(uid,
            "ℹ️ Помощь:\n\n💬 Пиши любое сообщение\n📸 Отправь фото — решу задачу\n"
            "🎤 Запиши голосовое — расшифрую\n\nКоманды:\n/start — перезапуск\n"
            "/new — новый диалог\n/remind 18:00 текст — напоминание\n"
            "/weather Москва — погода\n/myid — твой ID",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu")))
        return

    # ── РЕЖИМЫ ──
    if data.startswith("mode_"):
        m=data.replace("mode_",""); modes[uid]=m; histories[uid]=[]
        names={"normal":"💬 Пиши что угодно!","study":"📚 Помогу с учёбой!","support":"🤗 Я здесь 💕","creative":"🎨 Придумаем что-нибудь! ✨"}
        bot.answer_callback_query(call.id,"Режим изменён!")
        bot.send_message(uid,names.get(m,"Режим изменён!"))
        return

    # ── КАРТИНКИ ──
    if data=="btn_imagine":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🌅 Закат у моря",   callback_data="imagine_q_sunset at sea golden hour photorealistic"),
            types.InlineKeyboardButton("🌸 Аниме девушка",  callback_data="imagine_q_beautiful anime girl sakura spring"),
            types.InlineKeyboardButton("🏙 Ночной город",   callback_data="imagine_q_night city cyberpunk neon lights"),
            types.InlineKeyboardButton("🐱 Котик",          callback_data="imagine_q_cute fluffy cat adorable soft lighting"),
            types.InlineKeyboardButton("✍️ Своё описание",  callback_data="imagine_custom"),
        )
        bot.send_message(uid,"🖼 Выбери стиль или опиши своё:",reply_markup=kb)
        return

    if data=="imagine_custom":
        bot.answer_callback_query(call.id); modes[uid]="imagine_mode"
        bot.send_message(uid,"✍️ Опиши что нарисовать (на русском):\n\nНапример: красивая девушка в кафе, уютно, осень")
        return

    if data.startswith("imagine_q_"):
        prompt=data.replace("imagine_q_","")
        bot.answer_callback_query(call.id,"🎨 Рисую...")
        _gen_img(call.message.chat.id,uid,prompt)
        return

    if data.startswith("imagine_again_"):
        prompt=data.replace("imagine_again_","")
        bot.answer_callback_query(call.id,"🎨 Рисую...")
        _gen_img(call.message.chat.id,uid,prompt)
        return

    # ── ГОРОСКОП ──
    if data=="btn_horoscope":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=3)
        for s in ZODIAC_SIGNS: kb.add(types.InlineKeyboardButton(s,callback_data=f"zodiac_{s}"))
        bot.send_message(uid,"🌙 Выбери знак:",reply_markup=kb)
        return

    if data.startswith("zodiac_"):
        sign=data.replace("zodiac_",""); bot.answer_callback_query(call.id,"Читаю звёзды...")
        bot.send_chat_action(uid,"typing")
        try:
            answer=ask_ai(uid,f"Составь красивый гороскоп на сегодня для {sign}. Включи: общее, любовь, работа, совет дня. Без LaTeX.",
                custom_system="Ты астролог. Пиши красиво с эмодзи, оптимистично.")
            last_answer[uid]=answer
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🔄 Другой знак",callback_data="btn_horoscope"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            send_safe(uid,answer,kb=kb)
        except: bot.send_message(uid,"😔 Ошибка. Попробуй позже!")
        return

    # ── ВИКТОРИНА ──
    if data=="btn_quiz":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=2)
        for name,val in QUIZ_TOPICS.items(): kb.add(types.InlineKeyboardButton(name,callback_data=f"quiz_{val}"))
        kb.add(types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        st=quiz_state.get(uid,{})
        header=f"🏆 Счёт: {st.get('score',0)}/{st.get('total',0)}\n\n" if st.get("total") else ""
        bot.send_message(uid,f"{header}🧠 Выбери тему:",reply_markup=kb)
        return

    if data.startswith("quiz_") and not data.startswith("quiz_hint") and not data.startswith("quiz_ans") and not data.startswith("quiz_cor") and not data.startswith("quiz_wro"):
        topic=data.replace("quiz_",""); bot.answer_callback_query(call.id,"Готовлю вопрос...")
        bot.send_chat_action(uid,"typing")
        wait=bot.send_message(uid,"🎲 Придумываю вопрос...")
        try:
            r=requests.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization":f"Bearer {GROQ_KEY}","Content-Type":"application/json"},
                data=json.dumps({"model":"llama-3.3-70b-versatile","messages":[{"role":"user",
                    "content":f"Придумай вопрос для викторины на тему {topic}. Ответ ТОЛЬКО JSON без markdown: {{\"question\":\"...\",\"answer\":\"...\",\"hint\":\"...\",\"fun_fact\":\"...\"}}"}],
                    "max_tokens":300}),timeout=20)
            text_r=r.json()["choices"][0]["message"]["content"].strip()
            text_r=re.sub(r'```json|```','',text_r).strip()
            q=json.loads(text_r)
            if uid not in quiz_state: quiz_state[uid]={"score":0,"total":0}
            quiz_state[uid]["current"]=q; quiz_state[uid]["topic"]=topic; quiz_state[uid]["total"]+=1
            try: bot.delete_message(uid,wait.message_id)
            except: pass
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("💡 Подсказка",callback_data="quiz_hint"),
                   types.InlineKeyboardButton("✅ Ответ",callback_data="quiz_ans"),
                   types.InlineKeyboardButton("➡️ Следующий",callback_data=f"quiz_{topic}"),
                   types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            modes[uid]="quiz_mode"
            bot.send_message(uid,f"🧠 Вопрос #{quiz_state[uid]['total']} | Счёт: {quiz_state[uid]['score']}\n\n{q['question']}\n\nНапиши ответ или нажми кнопку 👇",reply_markup=kb)
        except Exception as e:
            log_event(f"Quiz error: {e}")
            try: bot.delete_message(uid,wait.message_id)
            except: pass
            bot.send_message(uid,"😔 Не смогла придумать вопрос. Попробуй другую тему!")
        return

    if data=="quiz_hint":
        bot.answer_callback_query(call.id); q=quiz_state.get(uid,{}).get("current",{})
        bot.send_message(uid,f"💡 Подсказка:\n\n{q.get('hint','Нет подсказки')}")
        return

    if data=="quiz_ans":
        bot.answer_callback_query(call.id); q=quiz_state.get(uid,{}).get("current",{}); topic=quiz_state.get(uid,{}).get("topic","random")
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("✅ Я знала!",callback_data="quiz_cor"),types.InlineKeyboardButton("❌ Не знала",callback_data="quiz_wro"),
               types.InlineKeyboardButton("➡️ Следующий",callback_data=f"quiz_{topic}"))
        modes[uid]="normal"
        bot.send_message(uid,f"✅ Ответ: {q.get('answer','?')}\n\n🌟 Факт: {q.get('fun_fact','')}",reply_markup=kb)
        return

    if data in("quiz_cor","quiz_wro"):
        if data=="quiz_cor":
            quiz_state.setdefault(uid,{})["score"]=quiz_state.get(uid,{}).get("score",0)+1
            bot.answer_callback_query(call.id,"🎉 Засчитано!")
        else: bot.answer_callback_query(call.id,"💪 Теперь знаешь!")
        s=quiz_state.get(uid,{}); bot.send_message(uid,f"Счёт: {s.get('score',0)}/{s.get('total',0)}")
        return

    # ── КРАСОТА ──
    if data=="btn_beauty":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("💡 Лайфхак",         callback_data="beauty_tip"),
            types.InlineKeyboardButton("🧴 Рутина ухода",    callback_data="beauty_routine"),
            types.InlineKeyboardButton("💇 Волосы",          callback_data="beauty_hair"),
            types.InlineKeyboardButton("💅 Ногти",           callback_data="beauty_nails"),
            types.InlineKeyboardButton("🏋️ Упражнения",      callback_data="beauty_fitness"),
            types.InlineKeyboardButton("🥗 Питание",         callback_data="beauty_nutrition"),
            types.InlineKeyboardButton("📋 Меню",            callback_data="btn_menu"),
        )
        bot.send_message(uid,"💄 Уход за собой — что тебя интересует?",reply_markup=kb)
        return

    if data.startswith("beauty_"):
        sub=data.replace("beauty_",""); bot.answer_callback_query(call.id,"Загружаю...")
        bot.send_chat_action(uid,"typing")
        prompts={"tip":"Дай один крутой бьюти-лайфхак. Кратко и практично.","routine":"Утренняя и вечерняя рутина ухода за лицом.","hair":"Советы по уходу за волосами дома.","nails":"Маникюр дома пошагово.","fitness":"5 упражнений для красивой фигуры дома без оборудования.","nutrition":"Правильное питание для красоты кожи и волос."}
        try:
            answer=ask_ai(uid,prompts.get(sub,"Дай совет по уходу за собой."),custom_system="Ты эксперт по красоте. Практичные советы.")
            last_answer[uid]=answer
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("💄 Ещё",callback_data="btn_beauty"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            send_safe(uid,answer,kb=kb)
        except: bot.send_message(uid,"😔 Ошибка!")
        return

    # ── ЛЮБОВНЫЕ ПИСЬМА ──
    if data=="btn_love":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("💌 Письмо",         callback_data="love_letter"),
            types.InlineKeyboardButton("💐 Признание",      callback_data="love_confession"),
            types.InlineKeyboardButton("🌹 Доброе утро",    callback_data="love_morning"),
            types.InlineKeyboardButton("🌙 Спокойной ночи", callback_data="love_night"),
            types.InlineKeyboardButton("💔 Извинение",      callback_data="love_sorry"),
            types.InlineKeyboardButton("📋 Меню",           callback_data="btn_menu"),
        )
        bot.send_message(uid,"💌 Что написать любимому?",reply_markup=kb)
        return

    if data.startswith("love_"):
        sub=data.replace("love_",""); bot.answer_callback_query(call.id,"Пишу...")
        bot.send_chat_action(uid,"typing")
        prompts={"letter":"Напиши красивое романтическое письмо любимому.","confession":"Напиши красивое признание в любви.","morning":"Романтическое доброе утро для любимого.","night":"Нежное спокойной ночи для любимого.","sorry":"Искреннее извинение для любимого."}
        try:
            answer=ask_ai(uid,prompts.get(sub,"Напиши романтическое сообщение."),custom_system="Ты романтичный поэт.")
            last_answer[uid]=answer
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🔄 Другой вариант",callback_data=data),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            send_safe(uid,answer,kb=kb)
        except: bot.send_message(uid,"😔 Ошибка!")
        return

    # ── МЕДИТАЦИЯ ──
    if data=="btn_meditation":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=1)
        for i,m in enumerate(MEDITATIONS): kb.add(types.InlineKeyboardButton(m["name"],callback_data=f"med_{i}"))
        kb.add(types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        bot.send_message(uid,"🧘 Выбери практику:",reply_markup=kb)
        return

    if data.startswith("med_"):
        idx=int(data.replace("med_","")); med=MEDITATIONS[idx]
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("🧘 Ещё",callback_data="btn_meditation"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        bot.send_message(uid,f"{med['name']}\n\n{med['text']}",reply_markup=kb)
        return

    # ── НАСТРОЕНИЕ ──
    if data=="btn_mood":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=4)
        kb.add(*[types.InlineKeyboardButton(e,callback_data=f"mood_{e}") for e in MOOD_EMOJIS])
        kb.add(types.InlineKeyboardButton("📈 История",callback_data="mood_hist"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        bot.send_message(uid,"Как себя чувствуешь? 🌈",reply_markup=kb)
        return

    if data.startswith("mood_") and data!="mood_hist":
        emoji=data.replace("mood_",""); now=datetime.now().strftime("%d.%m %H:%M")
        if uid not in mood_log: mood_log[uid]=[]
        mood_log[uid].append({"time":now,"mood":emoji,"name":MOOD_EMOJIS.get(emoji,"")})
        bot.answer_callback_query(call.id,f"Записала {emoji}")
        try:
            answer=ask_ai(uid,f"Настроение: {emoji} {MOOD_EMOJIS.get(emoji)}. Отреагируй тепло и с заботой.",
                custom_system="Ты заботливая подруга.")
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("📈 История",callback_data="mood_hist"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            send_safe(uid,answer,kb=kb)
        except: bot.send_message(uid,f"Записала {emoji} 📊")
        return

    if data=="mood_hist":
        bot.answer_callback_query(call.id); log=mood_log.get(uid,[])
        if not log: bot.send_message(uid,"📊 История пустая!"); return
        lines=[f"{e['time']} — {e['mood']} {e['name']}" for e in log[-10:]]
        bot.send_message(uid,"📊 Последние записи:\n\n"+"\n".join(lines))
        return

    # ── ПЕРЕВОДЧИК ──
    if data=="btn_translate":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=2)
        for name,val in LANGUAGES.items(): kb.add(types.InlineKeyboardButton(name,callback_data=f"trl_{val}"))
        kb.add(types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        bot.send_message(uid,"🌍 На какой язык переводим?",reply_markup=kb)
        return

    if data.startswith("trl_"):
        lang=data.replace("trl_",""); bot.answer_callback_query(call.id)
        modes[uid]=f"translate_{lang}"
        lang_name={v:k for k,v in LANGUAGES.items()}.get(lang,lang)
        bot.send_message(uid,f"✍️ Напиши текст для перевода на {lang_name}:")
        return

    # ── ПОГОДА/ВАЛЮТА ──
    if data=="btn_weather":
        bot.answer_callback_query(call.id); modes[uid]="weather_mode"
        bot.send_message(uid,"🌤 Напиши город:")
        return

    if data=="btn_currency":
        bot.answer_callback_query(call.id,"Загружаю...")
        bot.send_message(uid,get_currency())
        return

    # ── ПЕРЕСКАЗ ──
    if data=="btn_summarize":
        bot.answer_callback_query(call.id); modes[uid]="summarize_mode"
        bot.send_message(uid,"📖 Отправь текст — сделаю краткий пересказ!")
        return

    # ── РЕЦЕПТЫ ──
    if data=="btn_recipe":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🥘 По продуктам",   callback_data="recipe_custom"),
            types.InlineKeyboardButton("⚡ Быстрый",        callback_data="recipe_quick"),
            types.InlineKeyboardButton("🥗 Полезный",       callback_data="recipe_healthy"),
            types.InlineKeyboardButton("🎂 Десерт",         callback_data="recipe_dessert"),
            types.InlineKeyboardButton("🍳 Завтрак",        callback_data="recipe_breakfast"),
            types.InlineKeyboardButton("📋 Меню",           callback_data="btn_menu"),
        )
        bot.send_message(uid,"🍽 Что приготовим?",reply_markup=kb)
        return

    if data=="recipe_custom":
        bot.answer_callback_query(call.id); modes[uid]="recipe_mode"
        bot.send_message(uid,"🥘 Напиши что есть в холодильнике:")
        return

    if data.startswith("recipe_") and data!="recipe_custom":
        sub=data.replace("recipe_",""); bot.answer_callback_query(call.id,"Ищу рецепт...")
        bot.send_chat_action(uid,"typing")
        prompts={"quick":"Рецепт вкусного блюда за 15 минут.","healthy":"Рецепт полезного блюда.","dessert":"Простой десерт дома.","breakfast":"Вкусный завтрак."}
        try:
            answer=ask_ai(uid,prompts.get(sub,"Дай рецепт."),custom_system="Ты шеф-повар. Простые вкусные рецепты.")
            last_answer[uid]=answer
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🍽 Ещё",callback_data="btn_recipe"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            send_safe(uid,answer,kb=kb)
        except: bot.send_message(uid,"😔 Ошибка!")
        return

    # ── ПЛАНИРОВЩИК ──
    if data=="btn_planner":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🗓 План дня",        callback_data="plan_day"),
            types.InlineKeyboardButton("⏰ Утренняя рутина", callback_data="plan_morning"),
            types.InlineKeyboardButton("🌙 Вечерняя рутина", callback_data="plan_evening"),
            types.InlineKeyboardButton("📚 План учёбы",      callback_data="plan_study"),
            types.InlineKeyboardButton("📋 Меню",            callback_data="btn_menu"),
        )
        bot.send_message(uid,"🗓 Планировщик — что составим?",reply_markup=kb)
        return

    if data.startswith("plan_"):
        sub=data.replace("plan_",""); bot.answer_callback_query(call.id,"Составляю...")
        bot.send_chat_action(uid,"typing")
        prompts={"day":"Составь идеальный план дня по часам с 7:00 до 23:00.","morning":"Утренняя рутина на 1 час.","evening":"Вечерняя рутина для расслабления.","study":"Эффективный план учёбы на день."}
        try:
            answer=ask_ai(uid,prompts.get(sub,"Составь план дня."),custom_system="Ты коуч по продуктивности.")
            last_answer[uid]=answer
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🗓 Ещё",callback_data="btn_planner"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            send_safe(uid,answer,kb=kb)
        except: bot.send_message(uid,"😔 Ошибка!")
        return

    # ── СПИСОК ДЕЛ ──
    if data=="btn_todo":
        bot.answer_callback_query(call.id)
        todos=todo_list.get(uid,[]); done=sum(1 for t in todos if t["done"])
        kb=types.InlineKeyboardMarkup(row_width=1)
        for i,t in enumerate(todos):
            icon="✅" if t["done"] else "⬜"
            kb.add(types.InlineKeyboardButton(f"{icon} {t['text']}",callback_data=f"todo_toggle_{i}"))
        kb.add(types.InlineKeyboardButton("➕ Добавить",callback_data="todo_add"),
               types.InlineKeyboardButton("🗑 Очистить",callback_data="todo_clear"),
               types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        text=f"📝 Список дел ({done}/{len(todos)} ✅)" if todos else "📝 Список пустой — добавь первое дело!"
        bot.send_message(uid,text,reply_markup=kb)
        return

    if data=="todo_add": bot.answer_callback_query(call.id); modes[uid]="todo_mode"; bot.send_message(uid,"📝 Что добавить?"); return
    if data=="todo_clear": todo_list[uid]=[]; bot.answer_callback_query(call.id,"Очищено!"); bot.send_message(uid,"🗑 Список очищен!"); return
    if data.startswith("todo_toggle_"):
        idx=int(data.replace("todo_toggle_","")); todos=todo_list.get(uid,[])
        if idx<len(todos): todos[idx]["done"]=not todos[idx]["done"]
        bot.answer_callback_query(call.id,"✅" if todos[idx]["done"] else "⬜")
        try: bot.edit_message_reply_markup(uid,call.message.message_id,reply_markup=_todo_kb(uid))
        except: pass
        return

    # ── НАПОМИНАНИЯ ──
    if data=="btn_reminders":
        bot.answer_callback_query(call.id); rems=db.get_reminders(uid)
        kb=types.InlineKeyboardMarkup(row_width=1)
        for i,r in enumerate(rems):
            d="🔁" if r.get("daily") else "1️⃣"
            kb.add(types.InlineKeyboardButton(f"{d} {r['time']} — {r['text'][:30]}",callback_data=f"rem_del_{i}"))
        kb.add(types.InlineKeyboardButton("➕ Добавить",callback_data="rem_add"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        text=f"⏰ Напоминания ({len(rems)}) — нажми чтобы удалить:" if rems else "⏰ Нет напоминаний. Добавь первое!"
        bot.send_message(uid,text,reply_markup=kb)
        return

    if data=="rem_add": bot.answer_callback_query(call.id); modes[uid]="rem_mode"; bot.send_message(uid,"⏰ Формат: ЧЧ:ММ текст\nНапример: 18:00 выпить воду\nДля ежедневного: 08:00 зарядка каждый день"); return
    if data.startswith("rem_del_"):
        idx=int(data.replace("rem_del_","")); db.remove_reminder(uid,idx); bot.answer_callback_query(call.id,"✅ Удалено!")
        try: bot.edit_message_reply_markup(uid,call.message.message_id,reply_markup=_rem_kb(uid))
        except: pass
        return

    # ── ДНЕВНИК ──
    if data=="btn_notes":
        bot.answer_callback_query(call.id); notes=db.get_notes(uid)
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("✍️ Написать",callback_data="note_text"),
               types.InlineKeyboardButton("🎤 Голосом",callback_data="note_voice"),
               types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        if not notes: bot.send_message(uid,"📓 Дневник пустой. Добавь первую запись!",reply_markup=kb)
        else:
            lines=[f"📅 {n['date'][:10]}\n{n['text'][:200]}\n" for n in reversed(notes[-5:])]
            bot.send_message(uid,"📓 Последние записи:\n\n"+"\n".join(lines),reply_markup=kb)
        return

    if data=="note_text": bot.answer_callback_query(call.id); modes[uid]="note_mode"; bot.send_message(uid,"✍️ Напиши заметку:"); return
    if data=="note_voice": bot.answer_callback_query(call.id); modes[uid]="note_voice_mode"; bot.send_message(uid,"🎤 Запиши голосовое — сохраню как заметку!"); return
    if data=="btn_save_note":
        text=last_answer.get(uid,"")
        if not text: bot.answer_callback_query(call.id,"Нет текста!"); return
        db.add_note(uid,text[:500]); bot.answer_callback_query(call.id,"📓 Сохранено!")
        return

    # ── ПАМЯТЬ ──
    if data=="btn_memory":
        bot.answer_callback_query(call.id); mem=db.get_memory(uid)
        kb=types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("✏️ Имя",callback_data="mem_name"),
               types.InlineKeyboardButton("🎂 День рождения",callback_data="mem_birthday"),
               types.InlineKeyboardButton("🏙 Город",callback_data="mem_city"),
               types.InlineKeyboardButton("💫 Интересы",callback_data="mem_interests"),
               types.InlineKeyboardButton("📝 О себе",callback_data="mem_about"),
               types.InlineKeyboardButton("🗑 Очистить",callback_data="mem_clear"),
               types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        if not mem:
            bot.send_message(uid,"🧠 Память пустая\n\nРасскажи о себе — буду учитывать в разговоре!",reply_markup=kb)
        else:
            parts=[]
            for k,n in [("name","👤 Имя"),("age","🎂 Возраст"),("birthday","🎂 ДР"),("city","🏙 Город"),("interests","💫 Интересы"),("about","📝 О себе")]:
                if mem.get(k): parts.append(f"{n}: {mem[k]}")
            bot.send_message(uid,"🧠 Что я о тебе знаю:\n\n"+"\n".join(parts),reply_markup=kb)
        return

    if data.startswith("mem_") and data!="mem_clear":
        field=data.replace("mem_",""); bot.answer_callback_query(call.id); modes[uid]=f"mem_{field}"
        names={"name":"имя","birthday":"день рождения","city":"город","interests":"интересы","about":"немного о себе"}
        bot.send_message(uid,f"✏️ Напиши своё {names.get(field,field)}:")
        return

    if data=="mem_clear":
        db.r.delete(f"memory:{uid}"); bot.answer_callback_query(call.id,"🗑 Память очищена!")
        bot.send_message(uid,"🗑 Память очищена!")
        return

    # ── РЕФЕРАЛЫ ──
    if data=="btn_referral":
        bot.answer_callback_query(call.id); code=db.get_ref_code(uid)
        ref=db.r.get(f"referral:{uid}") or {}; link=f"https://t.me/{bot.get_me().username}?start={code}"
        kb=types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        bot.send_message(uid,
            f"🔗 Реферальная программа\n\nЗа каждую подругу — +7 дней!\n"
            f"Подруга получает: +3 дня бесплатно\n\n🔗 Ссылка:\n{link}\n\n"
            f"👥 Приглашено: {len(ref.get('invited',[]))}\n🎁 Бонусов: {ref.get('bonus_days',0)} дней",reply_markup=kb)
        return

    # ── РАЗНОЕ ──
    if data=="btn_photo_hint":
        bot.answer_callback_query(call.id)
        text_ph = "📸 Как отправить фото на анализ:\n\nПросто отправь фото в чат!\n\nЯ умею:\n• Решать задачи по фото\n• Читать текст на фото\n• Анализировать графики\n• Описывать что на фото\n\nДобавь подпись — отвечу точнее!"
        kb_ph = types.InlineKeyboardMarkup()
        kb_ph.add(types.InlineKeyboardButton("📋 Меню", callback_data="btn_menu"))
        bot.send_message(uid, text_ph, reply_markup=kb_ph)
        return

    if data=="btn_joke":
        bot.answer_callback_query(call.id,"😂"); bot.send_chat_action(uid,"typing")
        try:
            answer=ask_ai(uid,"Расскажи смешную короткую шутку по-русски.",custom_system="Ты юморист. Короткие смешные шутки.")
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("😂 Ещё",callback_data="btn_joke"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            bot.send_message(uid,answer,reply_markup=kb)
        except: bot.send_message(uid,"😔 Ошибка!")
        return

    if data=="btn_fact":
        bot.answer_callback_query(call.id,"🌟"); bot.send_chat_action(uid,"typing")
        try:
            answer=ask_ai(uid,"Расскажи один удивительный факт.",custom_system="Ты рассказываешь удивительные факты. Кратко и интересно.")
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🌟 Ещё",callback_data="btn_fact"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            bot.send_message(uid,f"🌟 Факт дня:\n\n{answer}",reply_markup=kb)
        except: bot.send_message(uid,"😔 Ошибка!")
        return

    if data=="btn_motivation":
        bot.answer_callback_query(call.id,"💪"); bot.send_chat_action(uid,"typing")
        try:
            answer=ask_ai(uid,"Дай мощную мотивационную речь для девушки.",custom_system="Ты мотивационный коуч.")
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("💪 Ещё",callback_data="btn_motivation"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            send_safe(uid,answer,kb=kb)
        except: bot.send_message(uid,"😔 Ошибка!")
        return

    if data=="btn_compliment":
        bot.answer_callback_query(call.id,"💕"); bot.send_message(uid,random.choice(COMPLIMENTS)); return

    if data=="btn_elaborate":
        text=last_answer.get(uid,"")
        if not text: bot.answer_callback_query(call.id,"Нет текста!"); return
        bot.answer_callback_query(call.id,"Расширяю...")
        bot.send_chat_action(uid,"typing")
        try:
            answer=ask_ai(uid,f"Расскажи об этом подробнее с примерами:\n{text}")
            last_answer[uid]=answer; send_safe(uid,answer,kb=after_kb())
        except: bot.send_message(uid,"😔 Ошибка!")
        return

    if data=="btn_explain_simple":
        text=last_answer.get(uid,"")
        if not text: bot.answer_callback_query(call.id,"Нет текста!"); return
        bot.answer_callback_query(call.id,"Объясняю проще...")
        bot.send_chat_action(uid,"typing")
        try:
            answer=ask_ai(uid,f"Объясни максимально просто, как подруга подруге, без сложных слов:\n{text}")
            last_answer[uid]=answer; send_safe(uid,answer,kb=after_kb())
        except: bot.send_message(uid,"😔 Ошибка!")
        return

    if data=="btn_voice_last":
        text=last_answer.get(uid,"")
        if not text: bot.answer_callback_query(call.id,"Нет текста!"); return
        if not VOICE_ENABLED: bot.answer_callback_query(call.id,"Голос недоступен!"); return
        bot.answer_callback_query(call.id,"🎵 Озвучиваю...")
        bot.send_chat_action(uid,"record_voice")
        try:
            from gtts import gTTS
            clean=re.sub(r'[*_`#•]','',text)[:500]
            tts=gTTS(text=clean,lang="ru",slow=False)
            tmp=tempfile.NamedTemporaryFile(suffix=".mp3",delete=False)
            tts.save(tmp.name)
            with open(tmp.name,"rb") as f: bot.send_voice(uid,f)
            os.unlink(tmp.name)
        except: bot.send_message(uid,"😔 Голос временно недоступен.")
        return

    # ── АДМИН ──
    if data in("adm_panel","adm_back","adm_stats","adm_users","adm_grant","adm_grant_time","adm_revoke","adm_block","adm_unblock","adm_broadcast") or data.startswith("adm_"):
        if not is_admin(u): bot.answer_callback_query(call.id,"❌ Нет доступа!"); return

    if data in("adm_panel","adm_back"):
        bot.answer_callback_query(call.id); a=db.get_analytics()
        bot.send_message(uid,
            f"👑 Админ-панель\n\n👥 Пользователей: {a['total_users']}\n"
            f"💎 Платных: {a['paid_users']}\n📊 DAU: {a['dau_today']}\n"
            f"✉️ Сообщений: {a['total_msgs']}\n🆕 За 7 дней: {a['new_week']}",reply_markup=admin_kb())
        return

    if data=="adm_stats":
        bot.answer_callback_query(call.id); a=db.get_analytics()
        top="\n".join(f"  {i+1}. {uid_s}: {st.get('total_msgs',0)} (@{st.get('username','')})" for i,(uid_s,st) in enumerate(a["top_users"]))
        bot.send_message(uid,
            f"📊 Аналитика\n\n👥 Всего: {a['total_users']}\n💎 Платных: {a['paid_users']}\n"
            f"🚫 Заблок: {a['blocked']}\n📈 DAU: {a['dau_today']}\n"
            f"✉️ Сообщений: {a['total_msgs']}\n🆕 За 7 дней: {a['new_week']}\n\n🏆 Топ:\n{top}",reply_markup=admin_kb())
        return

    if data=="adm_users":
        bot.answer_callback_query(call.id); paid=list(db.r.smembers("paid_uids"))
        if not paid: bot.send_message(uid,"Нет пользователей.",reply_markup=admin_kb()); return
        kb=types.InlineKeyboardMarkup(row_width=1)
        for pu in paid[:15]:
            st=db.r.get(f"stats:{pu}") or {}; un=st.get("username","")
            kb.add(types.InlineKeyboardButton(f"👤 {pu} @{un} | {db.sub_status(int(pu))}",callback_data=f"adm_user_{pu}"))
        kb.add(types.InlineKeyboardButton("🔙 Назад",callback_data="adm_panel"))
        bot.send_message(uid,f"👥 Платных: {len(paid)}",reply_markup=kb)
        return

    if data.startswith("adm_user_"):
        target=int(data.replace("adm_user_","")); bot.answer_callback_query(call.id)
        st=db.r.get(f"stats:{target}") or {}
        kb=types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("♾ Бессрочная",callback_data=f"adm_give_forever_{target}"),
               types.InlineKeyboardButton("🕐 На время",callback_data=f"adm_give_time_{target}"),
               types.InlineKeyboardButton("❌ Отозвать",callback_data=f"adm_do_revoke_{target}"),
               types.InlineKeyboardButton("🚫 Блок",callback_data=f"adm_do_block_{target}"),
               types.InlineKeyboardButton("✅ Разблок",callback_data=f"adm_do_unblock_{target}"),
               types.InlineKeyboardButton("🔙 Назад",callback_data="adm_users"))
        bot.send_message(uid,f"👤 {target} @{st.get('username','')}\nСтатус: {db.sub_status(target)}\nСообщений: {st.get('total_msgs',0)}",reply_markup=kb)
        return

    if data.startswith("adm_give_forever_"):
        target=int(data.replace("adm_give_forever_","")); db.set_user(target,None,plan="forever"); db.unblock(target)
        bot.answer_callback_query(call.id,"✅ Выдано!"); bot.send_message(uid,f"✅ Бессрочная выдана {target}")
        try: bot.send_message(target,"🎉 Тебе выдан бессрочный доступ! /start 🌸")
        except: pass
        return

    if data.startswith("adm_give_time_"):
        target=data.replace("adm_give_time_",""); bot.answer_callback_query(call.id)
        bot.send_message(uid,f"⏱ Выбери срок для {target}:",reply_markup=time_kb(target))
        return

    if re.match(r"adm_time_.+_\d+$",data):
        parts=data.split("_"); days=int(parts[-1]); target=int(parts[2])
        exp=datetime.now()+timedelta(days=days); db.set_user(target,exp,plan=f"{days}days"); db.unblock(target)
        bot.answer_callback_query(call.id,f"✅ {days} дней!"); bot.send_message(uid,f"✅ {target} — {days} дней до {exp.strftime('%d.%m.%Y')}")
        try: bot.send_message(target,f"🎉 Подписка на {days} дней! /start 🌸")
        except: pass
        return

    if data.startswith("adm_do_revoke_"):
        t=int(data.replace("adm_do_revoke_","")); db.remove_user(t); bot.answer_callback_query(call.id,"✅"); bot.send_message(uid,f"✅ Отозвано у {t}"); return
    if data.startswith("adm_do_block_"):
        t=int(data.replace("adm_do_block_","")); db.block(t); bot.answer_callback_query(call.id,"🚫"); bot.send_message(uid,f"🚫 Заблокирован {t}"); return
    if data.startswith("adm_do_unblock_"):
        t=int(data.replace("adm_do_unblock_","")); db.unblock(t); bot.answer_callback_query(call.id,"✅"); bot.send_message(uid,f"✅ Разблокирован {t}"); return

    if data=="adm_grant": bot.answer_callback_query(call.id); modes[uid]="adm_grant"; bot.send_message(uid,"➕ ID пользователя для бессрочной:"); return
    if data=="adm_grant_time": bot.answer_callback_query(call.id); modes[uid]="adm_grant_time"; bot.send_message(uid,"🕐 ID пользователя для подписки на время:"); return
    if data=="adm_revoke": bot.answer_callback_query(call.id); modes[uid]="adm_revoke"; bot.send_message(uid,"❌ ID для отзыва подписки:"); return
    if data=="adm_block": bot.answer_callback_query(call.id); modes[uid]="adm_block"; bot.send_message(uid,"🚫 ID для блокировки:"); return
    if data=="adm_unblock": bot.answer_callback_query(call.id); modes[uid]="adm_unblock"; bot.send_message(uid,"✅ ID для разблокировки:"); return
    if data=="adm_broadcast": bot.answer_callback_query(call.id); modes[uid]="adm_broadcast"; bot.send_message(uid,"📢 Напиши сообщение для рассылки:"); return

def _gen_img(chat_id,uid,prompt):
    wait=bot.send_message(chat_id,"🎨 Рисую... 10-30 сек ✨")
    try:
        img=generate_image(prompt)
        try: bot.delete_message(chat_id,wait.message_id)
        except: pass
        if img:
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🔄 Ещё вариант",callback_data=f"imagine_again_{quote(prompt[:50])}"),
                   types.InlineKeyboardButton("✍️ Новый запрос",callback_data="imagine_custom"),
                   types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            bot.send_photo(chat_id,img,caption=f"🎨 {prompt[:80]}",reply_markup=kb)
        else:
            kb=types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔄 Попробовать снова",callback_data=f"imagine_again_{quote(prompt[:50])}"),
                   types.InlineKeyboardButton("✍️ Другое описание",callback_data="imagine_custom"))
            bot.send_message(chat_id,"😔 Сервис картинок временно недоступен. Попробуй позже или измени описание.",reply_markup=kb)
    except Exception as e:
        log_event(f"Image error: {e}")
        try: bot.delete_message(chat_id,wait.message_id)
        except: pass
        bot.send_message(chat_id,"😔 Ошибка генерации. Попробуй позже.")

def _todo_kb(uid):
    kb=types.InlineKeyboardMarkup(row_width=1)
    for i,t in enumerate(todo_list.get(uid,[])):
        icon="✅" if t["done"] else "⬜"
        kb.add(types.InlineKeyboardButton(f"{icon} {t['text']}",callback_data=f"todo_toggle_{i}"))
    kb.add(types.InlineKeyboardButton("➕ Добавить",callback_data="todo_add"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
    return kb

def _rem_kb(uid):
    kb=types.InlineKeyboardMarkup(row_width=1)
    for i,r in enumerate(db.get_reminders(uid)):
        d="🔁" if r.get("daily") else "1️⃣"
        kb.add(types.InlineKeyboardButton(f"{d} {r['time']} — {r['text'][:30]}",callback_data=f"rem_del_{i}"))
    kb.add(types.InlineKeyboardButton("➕ Добавить",callback_data="rem_add"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
    return kb

# ── ТЕКСТОВЫЕ СООБЩЕНИЯ ──
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(msg):
    uid=msg.from_user.id; text=msg.text.strip(); u=msg.from_user.username or ""
    db.register_user(uid,u,msg.from_user.first_name or "")
    if text.startswith("/"): return
    m=modes.get(uid,"normal")
    if not check_and_count(msg): return
    bot.send_chat_action(msg.chat.id,"typing")

    # ── Спецрежимы ──
    if m=="imagine_mode":
        modes[uid]="normal"; _gen_img(msg.chat.id,uid,text); return

    if m=="note_mode":
        modes[uid]="normal"; db.add_note(uid,text)
        kb=types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📓 Дневник",callback_data="btn_notes"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
        bot.reply_to(msg,"📓 Заметка сохранена!",reply_markup=kb); return

    if m=="rem_mode":
        modes[uid]="normal"; parts=text.split(maxsplit=1)
        if len(parts)<2: bot.reply_to(msg,"❌ Формат: ЧЧ:ММ текст"); return
        t=parts[0]; txt=parts[1]; daily=txt.endswith("каждый день")
        if daily: txt=txt[:-len("каждый день")].strip()
        if not re.match(r"^\d{2}:\d{2}$",t): bot.reply_to(msg,"❌ Формат: ЧЧ:ММ"); return
        db.add_reminder(uid,txt,t,daily=daily)
        bot.reply_to(msg,f"✅ Напоминание:\n⏰ {t}{' 🔁' if daily else ''}\n📝 {txt}",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏰ Напоминания",callback_data="btn_reminders"))); return

    if m.startswith("mem_"):
        field=m.replace("mem_",""); modes[uid]="normal"
        db.save_memory(uid,field,text)
        names={"name":"имя","birthday":"день рождения","city":"город","interests":"интересы","about":"о себе","age":"возраст"}
        bot.reply_to(msg,f"✅ Запомнила: {names.get(field,field)} = {text} 💕",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🧠 Моя память",callback_data="btn_memory"))); return

    if m=="todo_mode":
        modes[uid]="normal"
        if uid not in todo_list: todo_list[uid]=[]
        todo_list[uid].append({"text":text,"done":False})
        bot.reply_to(msg,f"✅ Добавила: {text}",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📝 Список",callback_data="btn_todo"))); return

    if m=="weather_mode":
        modes[uid]="normal"; bot.reply_to(msg,get_weather(text)); return

    if m.startswith("translate_"):
        lang=m.replace("translate_",""); modes[uid]="normal"
        bot.send_chat_action(msg.chat.id,"typing")
        try:
            answer=ask_ai(uid,f"Переведи на {lang}, только перевод без пояснений: {text}",
                custom_system=f"Ты переводчик. Переводи точно на {lang}.")
            last_answer[uid]=answer
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🌍 Ещё",callback_data="btn_translate"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            bot.reply_to(msg,answer,reply_markup=kb)
        except: bot.reply_to(msg,"😔 Ошибка перевода!")
        return

    if m=="summarize_mode":
        modes[uid]="normal"
        bot.send_chat_action(msg.chat.id,"typing")
        try:
            answer=ask_ai(uid,f"Сделай краткий пересказ, выдели главное:\n\n{text}",
                custom_system="Ты эксперт по анализу текста. Пиши кратко и понятно.")
            last_answer[uid]=answer
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("📖 Ещё",callback_data="btn_summarize"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            bot.reply_to(msg,answer,reply_markup=kb)
        except: bot.reply_to(msg,"😔 Ошибка!")
        return

    if m=="recipe_mode":
        modes[uid]="normal"; text=f"У меня есть: {text}. Придумай вкусный рецепт пошагово."

    if m=="quiz_mode":
        modes[uid]="normal"; q=quiz_state.get(uid,{}).get("current",{}); topic=quiz_state.get(uid,{}).get("topic","random")
        correct=q.get("answer","").lower().strip(); user_ans=text.lower().strip()
        if correct and (correct in user_ans or user_ans in correct):
            quiz_state.setdefault(uid,{})["score"]=quiz_state.get(uid,{}).get("score",0)+1
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("➡️ Следующий",callback_data=f"quiz_{topic}"),types.InlineKeyboardButton("📋 Меню",callback_data="btn_menu"))
            bot.reply_to(msg,f"🎉 Правильно! Счёт: {quiz_state[uid]['score']}/{quiz_state[uid]['total']}\n\n🌟 {q.get('fun_fact','')}",reply_markup=kb)
        else:
            kb=types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("✅ Показать ответ",callback_data="quiz_ans"),types.InlineKeyboardButton("➡️ Следующий",callback_data=f"quiz_{topic}"))
            bot.reply_to(msg,"🤔 Не совсем... Попробуй ещё или посмотри ответ 👇",reply_markup=kb)
            modes[uid]="quiz_mode"
        return

    # ── Админ-режимы ──
    if m=="adm_grant":
        modes[uid]="normal"
        if not is_admin(u): return
        try:
            t=int(text); db.set_user(t,None,plan="forever"); db.unblock(t)
            bot.reply_to(msg,f"✅ Бессрочная выдана {t}",reply_markup=admin_kb())
            try: bot.send_message(t,"🎉 Бессрочный доступ! /start 🌸")
            except: pass
        except: bot.reply_to(msg,"❌ Неверный ID",reply_markup=admin_kb())
        return

    if m=="adm_grant_time":
        if not is_admin(u): return
        try:
            t=int(text); modes[uid]=f"adm_pick_time_{t}"
            bot.reply_to(msg,f"⏱ Выбери срок для {t}:",reply_markup=time_kb(str(t)))
        except: modes[uid]="normal"; bot.reply_to(msg,"❌ Неверный ID",reply_markup=admin_kb())
        return

    if m=="adm_revoke":
        modes[uid]="normal"
        if not is_admin(u): return
        try: t=int(text); db.remove_user(t); bot.reply_to(msg,f"✅ Отозвано у {t}",reply_markup=admin_kb())
        except: bot.reply_to(msg,"❌ Неверный ID",reply_markup=admin_kb())
        return

    if m=="adm_block":
        modes[uid]="normal"
        if not is_admin(u): return
        try:
            t=int(text); db.block(t); bot.reply_to(msg,f"🚫 Заблокирован {t}",reply_markup=admin_kb())
            try: bot.send_message(t,"🚫 Твой доступ заблокирован.")
            except: pass
        except: bot.reply_to(msg,"❌ Неверный ID",reply_markup=admin_kb())
        return

    if m=="adm_unblock":
        modes[uid]="normal"
        if not is_admin(u): return
        try:
            t=int(text); db.unblock(t); bot.reply_to(msg,f"✅ Разблокирован {t}",reply_markup=admin_kb())
            try: bot.send_message(t,"✅ Доступ восстановлен! /start 🌸")
            except: pass
        except: bot.reply_to(msg,"❌ Неверный ID",reply_markup=admin_kb())
        return

    if m=="adm_broadcast":
        modes[uid]="normal"
        if not is_admin(u): return
        sent=failed=0
        for target in db.r.smembers("paid_uids"):
            try: bot.send_message(int(target),f"📢 {text}"); sent+=1
            except: failed+=1
        bot.reply_to(msg,f"📢 Готово!\n✅ Доставлено: {sent}\n❌ Ошибок: {failed}",reply_markup=admin_kb())
        return

    # ── Обычный чат ──
    try:
        answer=ask_ai(uid,text)
        last_answer[uid]=answer
        send_safe(uid,answer,reply_to=msg,kb=after_kb())
    except requests.exceptions.Timeout:
        bot.reply_to(msg,"⏳ Долго думаю... Попробуй ещё раз 💤")
    except Exception as e:
        log_event(f"Chat error: {e}")
        if "429" in str(e) or "limit" in str(e).lower():
            bot.reply_to(msg,"⏳ Слишком много запросов. Подожди минуту!")
        else:
            bot.reply_to(msg,"😔 Что-то пошло не так. Попробуй /new")

print(f"👑 Лия v3.0 запущена!")
print(f"🤖 Модель: {MODEL_TEXT}")
print(f"👁 Фото: {MODEL_VISION}")
log_event("Bot started v3.0")
bot.infinity_polling(timeout=30,long_polling_timeout=20)