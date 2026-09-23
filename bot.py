import os, re, json, time, html as html_mod, asyncio, secrets, random, shutil
import urllib.request, urllib.parse, subprocess
from collections import Counter
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import (
    Message, FSInputFile, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, InputSticker, BotCommand, MenuButtonCommands,
    BusinessConnection,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from PIL import Image
import yt_dlp
from static_ffmpeg import run as static_ffmpeg_run

try:
    from gtts import gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN: raise ValueError("нет BOT_TOKEN")
STORAGE_CHAT_ID = os.environ.get("STORAGE_CHAT_ID")
OWNER_ID = 7752398574
OWNER_USERNAME = (os.environ.get("OWNER_USERNAME") or "vimbrix").lower().lstrip("@")
_env_id = os.environ.get("OWNER_ID")
if _env_id and _env_id.strip().isdigit(): OWNER_ID = int(_env_id)

DATA_FILE = "data.json"
KEY_LENGTH = 12
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
STT_SIZE = os.environ.get("STT_SIZE", "base")

_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
_session_kwargs = {"timeout": 60}
if _proxy: _session_kwargs["proxy"] = _proxy
session = AiohttpSession(**_session_kwargs)
bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

BOT_USERNAME_CACHE = None
STT_MODEL = None
TTT_GAMES = {}
UNO_GAMES = {}
MAFIA_GAMES = {}
RULETKA_LAST = {}
MUTED = {}
BUSINESS_CONNECTIONS = {}
WHISPER_TTL = 86400
HISTORY_LIMIT = 60
BTN_CREATE_KEY = "🔑 Создать ключ"
BTN_KEYS_STATUS = "📋 Статусы ключей"


class BotStates(StatesGroup):
    waiting_for_parts = State()
    waiting_for_media_type = State()
    waiting_for_cover = State()
    waiting_for_meta = State()
    waiting_for_duration = State()
    waiting_sticker_name = State()
    waiting_sticker_title = State()
    waiting_sticker_first = State()
    waiting_sticker_add_photo = State()
    waiting_uno_players = State()
    waiting_mafia_players = State()


os.makedirs("downloads", exist_ok=True)
os.makedirs("temp_photos", exist_ok=True)

FUNNY_REPLIES = [
    "Ержан, фу, нельзя, место!", "Дорогая, не лезь, оно тебя сожрёт",
    "Тебя в детстве не учили не нажимать куда попало?", "Это не твоё. Отойди.",
    "Руки убрал!", "Не для тебя писали.", "Читать чужие шёпоты — плохая примета.",
    "Здесь пусто. Серьёзно. Уходи.", "Кыш.", "А тебе кто разрешил?",
    "Тут был шёпот. Был. Ключевое слово — был.", "Не суй нос в чужой шёпот.",
    "Любопытной Варваре на базаре нос оторвали.", "Иди своей дорогой, путник.",
]

RULETKA_WIN = [
    "🍀 Повезло, повезло… не делай так больше, подумай о родных.",
    "😅 Щёлк — и пусто. В этот раз пронесло. Больше не рискуй.",
    "🎯 Ты выжил. Судьба дала тебе второй шанс, не трать его зря.",
    "💨 Курок щёлкнул вхолостую. Может, хватит испытывать удачу?",
    "🙏 Живой. Помолись и больше не садись за этот стол.",
]
RULETKA_LOSE = [
    "💀 Упс… ты умер. Не повезло.",
    "☠️ Бах! И всё. Ты умер.",
    "🪦 Ты проиграл. Увы.",
    "😵 Пуля нашла тебя. Ты умер.",
    "⚰️ Игра окончена. Ты умер.",
]

EN2RU = {
    'q':'й','w':'ц','e':'у','r':'к','t':'е','y':'н','u':'г','i':'ш','o':'щ','p':'з',
    '[':'х',']':'ъ','a':'ф','s':'ы','d':'в','f':'а','g':'п','h':'р','j':'о','k':'л',
    'l':'д',';':'ж',"'":'э','z':'я','x':'ч','c':'с','v':'м','b':'и','n':'т','m':'ь',
    ',':'б','.':'ю','/':'.','`':'ё',
    'Q':'Й','W':'Ц','E':'У','R':'К','T':'Е','Y':'Н','U':'Г','I':'Ш','O':'Щ','P':'З',
    '{':'Х','}':'Ъ','A':'Ф','S':'Ы','D':'В','F':'А','G':'П','H':'Р','J':'О','K':'Л',
    'L':'Д',':':'Ж','"':'Э','Z':'Я','X':'Ч','C':'С','V':'М','B':'И','N':'Т','M':'Ь',
    '<':'Б','>':'Ю','?':',','~':'Ё',
}
RU2EN = {v: k for k, v in EN2RU.items()}
STOPWORDS_RU = {"это","что","как","для","его","она","они","оно","если","или","уже","тоже","только","ещё","еще","очень","просто","было","были","быть","есть","надо","может","можно","нужно","когда","тогда","потом","здесь","там","также","меня","тебя","нас","вас","них","него","неё","нее","ему","ей","им","мне","бы","же","ли","да","нет","ну","вот","вон","ведь","хоть","весь","вся","всё","все","сам","сама","само","сами","один","одна","одно","два","три","пять","семь","этот","эта","эти","тот","та","те"}


def _empty_data():
    return {"keys": {}, "users": {}, "sticker_packs": {}, "whispers": {},
            "whisper_counter": 1, "business_owners": {}, "dead_bc": [],
            "business_chats": {}, "history": {}}


def _validate_data(d):
    if not isinstance(d, dict): return _empty_data()
    for k, v in _empty_data().items(): d.setdefault(k, v)
    return d


def load_data_from_file():
    for path in (DATA_FILE, DATA_FILE + ".bak"):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return _validate_data(json.load(f))
            except Exception as e:
                print(f"load_data_from_file {path}: {e}")
    return _empty_data()


def save_data_to_file(data):
    try:
        if os.path.exists(DATA_FILE):
            try: shutil.copy2(DATA_FILE, DATA_FILE + ".bak")
            except Exception: pass
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as e: print(f"save_data_to_file: {e}")


async def _tg_api_get(url, params=None, timeout=30):
    import aiohttp
    last_err = None
    for attempt in range(3):
        try:
            t = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get(url, params=params) as r: return await r.json()
        except Exception as e:
            last_err = e
            print(f"_tg_api_get {attempt+1}: {str(e)[:120]}")
            if attempt < 2: await asyncio.sleep(2)
    raise last_err or RuntimeError("get failed")


async def _tg_download_file(url, timeout=60):
    import aiohttp
    last_err = None
    for attempt in range(3):
        try:
            t = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get(url) as r: return await r.read()
        except Exception as e:
            last_err = e
            print(f"_tg_dl {attempt+1}: {str(e)[:120]}")
            if attempt < 2: await asyncio.sleep(2)
    raise last_err or RuntimeError("dl failed")


async def load_data_from_tg():
    if not STORAGE_CHAT_ID: return load_data_from_file()
    for attempt in range(4):
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
            resp = await _tg_api_get(url, params={"chat_id": STORAGE_CHAT_ID})
            if not resp.get("ok"): break
            pinned = resp["result"].get("pinned_message")
            if not pinned or "document" not in pinned: return load_data_from_file()
            file_id = pinned["document"]["file_id"]
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile"
            fdata = await _tg_api_get(url, params={"file_id": file_id})
            if not fdata.get("ok"): break
            file_path = fdata["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            content = await _tg_download_file(url)
            d = _validate_data(json.loads(content.decode("utf-8")))
            print(f"load_data_from_tg OK: keys={len(d['keys'])}, users={len(d['users'])}")
            save_data_to_file(d)
            return d
        except Exception as e:
            print(f"load_data_from_tg {attempt+1}: {str(e)[:120]}")
            if attempt < 3: await asyncio.sleep(3 * (attempt + 1))
    return load_data_from_file()


async def save_data_to_tg(data):
    save_data_to_file(data)
    if not STORAGE_CHAT_ID: return
    try:
        import aiohttp
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        t = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=t) as s:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
            form = aiohttp.FormData()
            form.add_field("chat_id", str(STORAGE_CHAT_ID))
            form.add_field("document", content, filename="data.json", content_type="application/json")
            form.add_field("disable_notification", "true")
            async with s.post(url, data=form) as r: resp = await r.json()
        if not resp.get("ok"): return
        msg_id = resp["result"]["message_id"]
        async with aiohttp.ClientSession(timeout=t) as s:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/pinChatMessage"
            async with s.post(url, data={"chat_id": STORAGE_CHAT_ID, "message_id": msg_id, "disable_notification": True}) as r:
                await r.json()
    except Exception as e: print(f"save_data_to_tg: {str(e)[:150]}")


DATA = _empty_data()


def save_data(data):
    save_data_to_file(data)
    if STORAGE_CHAT_ID:
        try: asyncio.create_task(save_data_to_tg(data))
        except RuntimeError: pass


def is_owner(user) -> bool:
    if not user: return False
    if OWNER_ID is not None and user.id == OWNER_ID: return True
    return (user.username or "").lower() == OWNER_USERNAME


async def get_bot_username() -> str:
    global BOT_USERNAME_CACHE
    if BOT_USERNAME_CACHE is None:
        me = await bot.get_me()
        BOT_USERNAME_CACHE = me.username
    return BOT_USERNAME_CACHE


def parse_duration(s):
    s = (s or "").lower().strip()
    total, matched = 0, False
    for m in re.finditer(r"(\d+)\s*([dhms])", s):
        n, u = int(m.group(1)), m.group(2)
        if u == "d": total += n * 86400
        elif u == "h": total += n * 3600
        elif u == "m": total += n * 60
        elif u == "s": total += n
        matched = True
    return total if matched and total > 0 else None


def format_duration(seconds):
    seconds = int(seconds)
    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, seconds = divmod(seconds, 60)
    parts = []
    if d: parts.append(f"{d} дн.")
    if h: parts.append(f"{h} ч.")
    if m: parts.append(f"{m} мин.")
    if not parts: parts.append(f"{seconds} сек.")
    return " ".join(parts)


def format_until(ts): return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


def user_status(user_id):
    u = DATA["users"].get(str(user_id))
    if not u: return "none"
    if u.get("permanent"): return "valid"
    if u.get("expires_at", 0) > time.time(): return "valid"
    return "expired"


def cleanup_expired_whispers():
    now = time.time()
    w = DATA.get("whispers", {})
    for wid in [wid for wid, wd in w.items() if wd.get("expires", 0) < now]: del w[wid]


def _is_peer_invalid(err): return "BUSINESS_PEER_INVALID" in str(err)
def _is_dead_bc(bc_id): return bc_id in DATA.get("dead_bc", [])


def _mark_bc_dead(bc_id, chat_id=None):
    if bc_id not in DATA["dead_bc"]:
        DATA["dead_bc"].append(bc_id)
        if len(DATA["dead_bc"]) > 100: DATA["dead_bc"] = DATA["dead_bc"][-100:]
    if chat_id and str(chat_id) in DATA.get("business_chats", {}): del DATA["business_chats"][str(chat_id)]
    if chat_id and chat_id in TTT_GAMES: TTT_GAMES.pop(chat_id, None)
    if chat_id: MUTED.pop(chat_id, None)


async def _notify_owner_bc_dead(bc_id):
    if _is_dead_bc(bc_id): return
    owner_id = None
    bc_entry = BUSINESS_CONNECTIONS.get(bc_id)
    if bc_entry and isinstance(bc_entry, dict): owner_id = bc_entry.get("user_id")
    if not owner_id: owner_id = DATA.get("business_owners", {}).get(bc_id)
    if not owner_id: owner_id = OWNER_ID
    _mark_bc_dead(bc_id); save_data(DATA)
    try:
        await bot.send_message(owner_id,
            "⚠️ <b>Бизнес-подключение устарело</b>\n\n"
            f"ID: <code>{html_mod.escape(bc_id[:24])}...</code>\n\n"
            "Переподключи бота в настройках Telegram Business.", parse_mode="HTML")
    except Exception as e: print(f"[bc] notify: {str(e)[:120]}")


async def _try_delete_command(message: Message, bc_id: str = None):
    try:
        if bc_id:
            await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
        else: await message.delete()
    except Exception as e: print(f"[del cmd] {str(e)[:120]}")


def _convert_layout(text):
    lat = sum(1 for c in text if c.isascii() and c.isalpha())
    cyr = sum(1 for c in text if 'а' <= c.lower() <= 'я' or c.lower() == 'ё')
    if lat == 0 and cyr == 0: return text, None
    if lat >= cyr: return "".join(EN2RU.get(c, c) for c in text), "en2ru"
    return "".join(RU2EN.get(c, c) for c in text), "ru2en"


def _summarize_messages(messages):
    if not messages: return "нет сообщений"
    texts = [m.get("text", "").strip() for m in messages
             if m.get("text") and not m["text"].startswith((".", "/")) and len(m["text"]) >= 3]
    if not texts: return "нет текста"
    words = [w for w in re.findall(r"[а-яё]{4,}|[a-z]{4,}", " ".join(texts).lower()) if w not in STOPWORDS_RU]
    top = Counter(words).most_common(8)
    top_str = ", ".join(w for w, _ in top) if top else "—"
    sentences = []
    for t in texts:
        for s in re.split(r"[.!?]+", t):
            s = s.strip()
            if len(s) > 25: sentences.append(s)
    sentences.sort(key=len, reverse=True)
    out = [f"📝 <b>Кратко:</b>", f"\n🔑 <b>Темы:</b> {html_mod.escape(top_str)}"]
    if sentences:
        out.append("\n💬 <b>Основное:</b>")
        for s in sentences[:3]: out.append(f"• {html_mod.escape(s[:180])}")
    out.append(f"\n<i>(проанализировано {len(texts)} сообщ.)</i>")
    return "\n".join(out)


# ============ STT ============
def get_stt_model():
    global STT_MODEL
    if STT_MODEL is None:
        print(f"Загружаю STT ({STT_SIZE})...")
        from faster_whisper import WhisperModel
        STT_MODEL = WhisperModel(STT_SIZE, device="cpu", compute_type="int8")
    return STT_MODEL


def convert_to_wav(src, dst):
    cmd = [FFMPEG_EXE_PATH, "-y", "-i", src, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0: raise RuntimeError(r.stderr[-200:] if r.stderr else "wav err")
    return dst


def transcribe_wav(wav):
    model = get_stt_model()
    segs, info = model.transcribe(wav, language="ru", beam_size=5, vad_filter=True,
                                  initial_prompt="Голосовое сообщение на русском языке.")
    return " ".join(s.text.strip() for s in segs).strip(), info.language


def _stt_pipeline(src, wav):
    convert_to_wav(src, wav); return transcribe_wav(wav)


async def _handle_stt(message: Message, file_id: str, ext_hint: str = ".ogg"):
    fi = await bot.get_file(file_id)
    src, wav = f"downloads/stt_{file_id}{ext_hint}", f"downloads/stt_{file_id}.wav"
    await bot.download_file(fi.file_path, src)
    status = await message.answer("📝 Слушаю...")
    try:
        loop = asyncio.get_event_loop()
        text, _ = await loop.run_in_executor(None, _stt_pipeline, src, wav)
        preview = (text if len(text) <= 3900 else text[:3900] + "...") or "🤷 Ничего не разобрал."
        await status.edit_text(f"📝 Расшифровка:\n\n{preview}")
    except Exception as e:
        try: await status.edit_text(f"😔 Ошибка: {str(e)[:250]}")
        except Exception: pass
    finally:
        for p in (src, wav):
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass


async def _handle_stt_bc(message: Message, file_id: str, ext_hint: str, bc_id: str):
    if _is_dead_bc(bc_id): return
    fi = await bot.get_file(file_id)
    src, wav = f"downloads/stt_{file_id}{ext_hint}", f"downloads/stt_{file_id}.wav"
    await bot.download_file(fi.file_path, src)
    try:
        status = await bot.send_message(message.chat.id, "📝 Слушаю...", business_connection_id=bc_id)
    except Exception as e: print(f"[stt bc] {e}"); return
    try:
        loop = asyncio.get_event_loop()
        text, _ = await loop.run_in_executor(None, _stt_pipeline, src, wav)
        preview = (f"📝 Расшифровка:\n\n{text[:3900]}" if text else "🤷 Ничего не разобрал.")
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status.message_id,
                                    text=preview, business_connection_id=bc_id)
    except Exception as e:
        print(f"bc stt: {str(e)[:150]}")
    finally:
        for p in (src, wav):
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass


def _tts_generate(text: str, out: str):
    tts = gTTS(text=text, lang="ru"); tts.save(out); return out


PASSTHROUGH = {"/whoami","/cancel","/start","/mykey","/help","/code","/whisper","/revoke","/play"}
DOT_CMDS = (".ttt",".mute",".unmute",".xo",".revoke",".switch",".sum",".tts",".ruletka",".rl","/uno","/mafia")


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, Message): return await handler(event, data)
        user = event.from_user
        if user is None: return await handler(event, data)
        text = (getattr(event, "text", None) or "").strip()
        cmd = text.split()[0].lower() if text else ""
        chat_id = event.chat.id if event.chat else None
        if chat_id and chat_id in MUTED and user.id in MUTED[chat_id]:
            if not is_owner(user):
                try: await event.delete()
                except Exception: pass
                try: await bot.send_message(chat_id, "🔇 МОЛЧАТЬ!!!")
                except Exception: pass
                return
        if cmd in PASSTHROUGH: return await handler(event, data)
        if text.startswith(DOT_CMDS): return await handler(event, data)
        if is_owner(user): return await handler(event, data)
        status = user_status(user.id)
        if status == "valid": return await handler(event, data)
        if status == "expired":
            DATA["users"].pop(str(user.id), None); save_data(DATA)
            try: await event.answer("⏰ Ключ истёк. Введи: /code")
            except Exception: pass
            return
        try: await event.answer("🔒 Доступ только по ключу.\n\n1. Получи у @vimbrix\n2. /code ТВОЙ_КЛЮЧ")
        except Exception: pass


async def try_activate_key(event, user, key):
    kd = DATA["keys"].get(key); uid = str(user.id)
    if not kd: await event.answer("❌ Нет такого ключа."); return
    if kd.get("used_by") is not None:
        if str(kd["used_by"]) == uid:
            u = DATA["users"].get(uid, {})
            if u.get("permanent"): await event.answer("😉 Уже активирован (навсегда).")
            else:
                exp = u.get("expires_at", 0); rem = max(0, exp - time.time())
                await event.answer(f"😉 Уже активирован.\n⏳ {format_duration(rem)}\n📅 До: {format_until(exp)}",
                                   parse_mode="HTML")
        else: await event.answer("⚠️ Ключ занят.")
        return
    now = time.time()
    perm = bool(kd.get("permanent")); dur = int(kd.get("duration", 0))
    kd["used_by"] = user.id; kd["activated_at"] = now
    entry = {"username": user.username or "", "key": key, "permanent": perm, "activated_at": now}
    if not perm: entry["expires_at"] = now + dur
    DATA["users"][uid] = entry; save_data(DATA)
    if perm: await event.answer("✅ <b>Ключ активирован!</b>\n♾ навсегда", parse_mode="HTML")
    else:
        await event.answer(f"✅ <b>Ключ активирован!</b>\n\n⏱ {format_duration(dur)}\n📅 До: {format_until(now + dur)}",
                           parse_mode="HTML")


def _cleanup_folder(folder):
    if os.path.exists(folder):
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            try:
                if os.path.isfile(p): os.remove(p)
            except Exception: pass


_cleanup_folder("downloads"); _cleanup_folder("temp_photos")
print("Временные папки очищены")

FFMPEG_EXE_PATH, FFPROBE_EXE_PATH = static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
FFMPEG_DIR = os.path.dirname(FFMPEG_EXE_PATH)
print(f"ffmpeg: {FFMPEG_EXE_PATH}")
print(f"Owner: @{OWNER_USERNAME}")

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
                      "\U0001F900-\U0001F9FF\U00002700-\U000027BF\U0001F1E6-\U0001F1FF"
                      "\U0001FA00-\U0001FAFF]+", flags=re.UNICODE)
DASH_RE = re.compile(r"\s*[-\u2013\u2014\u2212\u2015]\s*")


def clean_meta(s, max_len=64, strip_author=""):
    if not s: return ""
    s = str(s).strip()
    s = re.sub(r"https?://\S+", " ", s); s = re.sub(r"#\S+", " ", s); s = re.sub(r"@\S+", " ", s)
    s = re.sub(r"\btiktok\b", " ", s, flags=re.IGNORECASE); s = EMOJI_RE.sub(" ", s)
    s = " ".join(s.split())
    if strip_author:
        a = strip_author.strip().lower()
        for _ in range(3):
            if s.lower().startswith(a): s = s[len(a):].lstrip(" -|.,:;")
            else: break
    parts = DASH_RE.split(s, maxsplit=1)
    if len(parts) == 2 and len(parts[0].split()) >= 1 and len(parts[1].split()) >= 1:
        s = parts[0] if len(parts[0]) >= len(parts[1]) else parts[1]
    s = s.strip(" -|.,:;")
    return " ".join(s.split())[:max_len].strip()


def _download_direct(url, out_path, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        with open(out_path, "wb") as f:
            while True:
                c = r.read(65536)
                if not c: break
                f.write(c)
    return out_path


def tiktok_via_api(url, mode):
    api_url = f"https://tikwm.com/api/?url={urllib.parse.quote(url)}&hd=1"
    req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", errors="ignore"))
    if data.get("code") != 0: raise RuntimeError(f"TikTok: {data.get('msg')}")
    d = data["data"]; vid_id = d.get("id") or "tiktok"
    author = (d.get("author") or {}).get("unique_id") or ""
    title = clean_meta(d.get("title") or "tiktok", strip_author=author)
    duration = d.get("duration")
    if mode == "audio":
        mu = d.get("music")
        if not mu: raise RuntimeError("нет аудио")
        tmp = f"downloads/{vid_id}_raw"; _download_direct(mu, tmp)
        out = f"downloads/{vid_id}.mp3"
        r = subprocess.run([FFMPEG_EXE_PATH, "-y", "-i", tmp, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore")
        try: os.remove(tmp)
        except Exception: pass
        if r.returncode != 0: raise RuntimeError("ffmpeg err")
        return out, title, duration, None, None
    mu = d.get("hdplay") or d.get("play")
    if not mu: raise RuntimeError("нет видео")
    out = f"downloads/{vid_id}.mp4"; _download_direct(mu, out)
    return out, title, duration, None, None


def other_site_download(url, mode):
    opts = {"outtmpl": "downloads/%(id)s.%(ext)s", "quiet": True, "no_warnings": True,
            "noprogress": True, "noplaylist": True, "ffmpeg_location": FFMPEG_DIR}
    if mode == "audio":
        opts.update({"format": "bestaudio/best",
                     "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]})
    else: opts.update({"format": "bestvideo*+bestaudio/best", "merge_output_format": "mp4"})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True); fn = ydl.prepare_filename(info)
    base, _ = os.path.splitext(fn)
    cands = ([base + ".mp3", fn + ".mp3"] if mode == "audio"
             else [fn, base + ".mp4", base + ".mkv", base + ".webm"])
    final = next((p for p in cands if os.path.exists(p)), None)
    if not final: raise FileNotFoundError(cands)
    title = clean_meta(info.get("title") or "media")
    performer = clean_meta(info.get("artist") or info.get("uploader") or info.get("channel") or "", 64) or "Unknown"
    return final, title, info.get("duration"), performer, info.get("thumbnail")


def split_image(image_path, total_parts):
    img = Image.open(image_path)
    if img.mode != "RGB": img = img.convert("RGB")
    cols, rows = 3, total_parts // cols
    w, h = img.size
    nw, nh = (w // cols) * cols, (h // rows) * rows
    if nw != w or nh != h: img = img.resize((nw, nh), Image.LANCZOS); w, h = img.size
    pw, ph = w // cols, h // rows
    files = []
    for row in range(rows):
        for col in range(cols):
            crop = img.crop((col*pw, row*ph, col*pw+pw, row*ph+ph))
            fn = f"temp_photos/part_{row}_{col}.png"
            crop.save(fn, "PNG", optimize=False, compress_level=1); files.append(fn)
    return files


def calculate_auto_parts(image_path):
    img = Image.open(image_path); w, h = img.size; ratio = h / w
    return 9 if ratio > 1.5 else (6 if ratio > 1.0 else 3)


def apply_tags(ap, cp, title, perf):
    out = os.path.splitext(ap)[0] + "_tagged.mp3"
    cmd = [FFMPEG_EXE_PATH, "-y", "-i", ap]
    if cp and os.path.exists(cp):
        cmd += ["-i", cp, "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg",
                "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    else: cmd += ["-map", "0:a"]
    cmd += ["-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3",
            "-metadata", f"title={title}", "-metadata", f"artist={perf}", out]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0: raise RuntimeError("ffmpeg err")
    return out


def get_duration(path):
    try:
        r = subprocess.run([FFPROBE_EXE_PATH, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", path],
                           capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception: return None


# ============ КЛЮЧИ ============
def owner_reply_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CREATE_KEY),
                                          KeyboardButton(text=BTN_KEYS_STATUS)]],
                               resize_keyboard=True, is_persistent=True)


def _generate_key():
    for _ in range(200):
        k = "".join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_LENGTH))
        if k not in DATA["keys"]: return k
    raise RuntimeError("key gen fail")


def _key_status_text(kd):
    if kd.get("used_by"):
        u = DATA["users"].get(str(kd["used_by"]), {})
        uname = html_mod.escape(u.get("username") or f"id{kd['used_by']}")
        if u.get("permanent"): return f"♾ активирован @{uname}"
        exp = u.get("expires_at", 0)
        if exp > time.time():
            return f"⏳ @{uname} — {format_duration(max(0, exp - time.time()))} (до {format_until(exp)})"
        return f"❌ @{uname} — истёк"
    if kd.get("permanent"): return "♾ свободен"
    return f"⏳ {format_duration(kd.get('duration', 0))}, свободен"


def _render_keys_list():
    keys = DATA["keys"]
    if not keys: return "🗂 Пока ни одного ключа нет.", None
    free = [(k, v) for k, v in keys.items() if not v.get("used_by")]
    used = [(k, v) for k, v in keys.items() if v.get("used_by")]
    lines = [f"🗂 <b>Ключи</b> (всего: {len(keys)})\n"]
    if free:
        lines.append(f"<b>Свободные ({len(free)}):</b>")
        for k, kd in free: lines.append(f"<code>{html_mod.escape(k)}</code> — {_key_status_text(kd)}")
        lines.append("")
    if used:
        lines.append(f"<b>Активированные ({len(used)}):</b>")
        for k, kd in used: lines.append(f"<code>{html_mod.escape(k)}</code> — {_key_status_text(kd)}")
    b = InlineKeyboardBuilder()
    for k in keys.keys(): b.button(text=f"🗑 {k}", callback_data=f"kdel:{k}")
    b.adjust(2)
    return "\n".join(lines), b.as_markup()


async def _send_keys_list(message):
    try:
        text, kb = _render_keys_list()
        if kb is None: await message.answer(text, parse_mode="HTML")
        else: await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        try:
            plain = re.sub(r"<[^>]+>", "", text)
            if kb is None: await message.answer(plain)
            else: await message.answer(plain, reply_markup=kb)
        except Exception: pass


async def _show_create_key_menu(message):
    b = InlineKeyboardBuilder()
    b.button(text="♾ Навсегда", callback_data="kc:perm")
    b.button(text="⏳ На время", callback_data="kc:temp")
    b.adjust(1)
    await message.answer("❓ Какой ключ?", reply_markup=b.as_markup())


def _revoke_by_user_id(uid):
    uid = str(uid); u = DATA["users"].pop(uid, None)
    if not u: return None
    key = u.get("key"); kd = False
    if key and key in DATA["keys"]: del DATA["keys"][key]; kd = True
    return {"uid": uid, "username": u.get("username") or "", "key": key, "key_deleted": kd}


def _revoke_by_username(un):
    un = un.lower().lstrip("@")
    for uid, ud in list(DATA["users"].items()):
        if (ud.get("username") or "").lower() == un: return _revoke_by_user_id(uid)
    return None


def _revoke_by_keycode(code):
    code = code.strip().upper(); kd = DATA["keys"].get(code)
    if not kd: return None
    ub = kd.get("used_by"); del DATA["keys"][code]
    ur = False
    if ub and str(ub) in DATA["users"]: del DATA["users"][str(ub)]; ur = True
    return {"key": code, "used_by": ub, "user_removed": ur}


@dp.message(lambda m: m.text is not None and BTN_CREATE_KEY in m.text)
async def btn_create_key(message):
    if not is_owner(message.from_user): return
    await _show_create_key_menu(message)


@dp.message(lambda m: m.text is not None and BTN_KEYS_STATUS in m.text)
async def btn_keys_status(message):
    if not is_owner(message.from_user): return
    await _send_keys_list(message)


@dp.message(F.text == "/keys")
async def cmd_keys(message):
    if not is_owner(message.from_user): return
    await _send_keys_list(message)


@dp.message(F.text == "/newkey")
async def cmd_newkey(message):
    if not is_owner(message.from_user): return
    await _show_create_key_menu(message)


@dp.callback_query(F.data == "kc:perm")
async def cb_kc_perm(cb):
    if not is_owner(cb.from_user): await cb.answer("Не твоя кнопка", show_alert=True); return
    key = _generate_key()
    DATA["keys"][key] = {"permanent": True, "duration": 0, "created_at": time.time(), "used_by": None}
    save_data(DATA)
    await cb.message.edit_text(f"♾ <b>Ключ создан:</b>\n\n<code>{key}</code>\n\n/code {key}", parse_mode="HTML")
    await cb.answer("Готово")


@dp.callback_query(F.data == "kc:temp")
async def cb_kc_temp(cb, state):
    if not is_owner(cb.from_user): await cb.answer("Не твоя", show_alert=True); return
    await cb.message.edit_text("⏳ Формат: <code>1d 2h 30m</code>", parse_mode="HTML")
    await state.set_state(BotStates.waiting_for_duration); await cb.answer()


@dp.message(BotStates.waiting_for_duration)
async def process_duration(message, state):
    try:
        if not is_owner(message.from_user): return
        dur = parse_duration(message.text or "")
        if not dur: await message.answer("🤔 Пример: <code>1d 2h 30m</code>", parse_mode="HTML"); return
        key = _generate_key()
        DATA["keys"][key] = {"permanent": False, "duration": dur, "created_at": time.time(), "used_by": None}
        save_data(DATA)
        await message.answer(f"⏳ <b>Ключ:</b> <code>{key}</code>\n⏱ {format_duration(dur)}\n/code {key}", parse_mode="HTML")
    finally: await state.clear()


@dp.callback_query(F.data.startswith("kdel:"))
async def cb_key_delete(cb):
    if not is_owner(cb.from_user): await cb.answer("Не твоя", show_alert=True); return
    code = cb.data.split(":", 1)[1].strip().upper()
    info = _revoke_by_keycode(code)
    if not info: await cb.answer("Уже удалён", show_alert=True)
    else:
        save_data(DATA)
        msg = f"✅ Ключ {code} удалён."
        if info["used_by"] and info["user_removed"]: msg += "\n🔓 Доступ снят."
        await cb.answer(msg, show_alert=True)
    try:
        text, kb = _render_keys_list()
        if kb is None: await cb.message.edit_text(text, parse_mode="HTML")
        else: await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception: pass


async def _do_revoke(message, arg=None):
    if not is_owner(message.from_user): await message.answer("Только владелец."); return
    if message.reply_to_message and message.reply_to_message.from_user:
        tgt = message.reply_to_message.from_user
        if is_owner(tgt): await message.answer("Владельца не трогаем."); return
        info = _revoke_by_user_id(tgt.id)
        if info: save_data(DATA); await message.answer(f"✅ Отозван.\nКлюч: {info['key'] or '—'}")
        else: await message.answer("🤷 Нет доступа.")
        return
    if not arg:
        await message.answer("📖 <code>/revoke CODE|@user|id</code>", parse_mode="HTML"); return
    arg = arg.strip()
    info = _revoke_by_keycode(arg)
    if info: save_data(DATA); await message.answer(f"✅ Ключ <code>{info['key']}</code> удалён.", parse_mode="HTML"); return
    if arg.isdigit():
        info = _revoke_by_user_id(arg)
        if info: save_data(DATA); await message.answer(f"✅ {info['uid']} отозван."); return
    info = _revoke_by_username(arg.lstrip("@"))
    if info: save_data(DATA); await message.answer(f"✅ @{info['username']} отозван."); return
    await message.answer("🤷 Не найдено.")


@dp.message(F.text == "/revoke")
async def cmd_revoke(message):
    await _try_delete_command(message)
    parts = (message.text or "").split(maxsplit=1)
    await _do_revoke(message, parts[1] if len(parts) >= 2 else None)


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith(".revoke")))
async def dot_revoke(message):
    await _try_delete_command(message)
    parts = (message.text or "").split(maxsplit=1)
    await _do_revoke(message, parts[1] if len(parts) >= 2 else None)


# ============ РУЛЕТКА ============
@dp.message(F.text.func(lambda t: t and t.strip().lower() in (".ruletka", ".rl", "рулетка", "русская рулетка")))
async def cmd_ruletka(message):
    await _try_delete_command(message)
    win = random.random() < 5/6
    if win:
        await message.answer(random.choice(RULETKA_WIN))
    else:
        await message.answer(random.choice(RULETKA_LOSE))


# ============ SWITCH ============
@dp.message(F.text.func(lambda t: t and t.strip().lower() == ".switch"))
async def cmd_switch(message):
    await _try_delete_command(message)
    if not message.reply_to_message:
        await message.answer("⚠️ Ответь <code>.switch</code> на сообщение с опечаткой.", parse_mode="HTML"); return
    src_text = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not src_text.strip(): await message.answer("⚠️ Нет текста."); return
    converted, direction = _convert_layout(src_text)
    if not direction or converted == src_text: await message.answer("🤷 Нечего менять."); return
    await message.answer(f"🔁 <b>Исправлено ({direction}):</b>\n\n{html_mod.escape(converted)}",
                         parse_mode="HTML", reply_to_message_id=message.reply_to_message.message_id)


# ============ TTS ============
@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith(".tts")))
async def cmd_tts(message):
    await _try_delete_command(message)
    if not HAS_GTTS: await message.answer("❌ gTTS не установлен."); return
    text = ""
    if message.reply_to_message: text = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
    else:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1: text = parts[1].strip()
    if not text: await message.answer("⚠️ <code>.tts текст</code> или реплаем.", parse_mode="HTML"); return
    if len(text) > 500: text = text[:500]
    status = await message.answer("🔊 Генерирую...")
    out = f"downloads/tts_{int(time.time())}.mp3"
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _tts_generate, text, out)
        await message.answer_voice(voice=FSInputFile(out, filename="voice.mp3"), caption="🔊 Готово.")
        try: await status.delete()
        except Exception: pass
    except Exception as e:
        try: await status.edit_text(f"❌ {str(e)[:200]}")
        except Exception: pass
    finally:
        if os.path.exists(out):
            try: os.remove(out)
            except Exception: pass


# ============ ГОЛОСОВЫЕ ============
@dp.message(F.voice)
async def process_voice(message, state):
    await state.set_state(None); await _handle_stt(message, message.voice.file_id, ".ogg")


@dp.message(F.video_note)
async def process_video_note(message, state):
    await state.set_state(None); await _handle_stt(message, message.video_note.file_id, ".mp4")


# ============ КРЕСТИКИ-НОЛИКИ (с реваншем) ============
TTT_WIN_LINES = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]


def ttt_board_text(game):
    board = game["board"]
    x_name = game["names"].get("x") or "?"
    o_name = game["names"].get("o") or "ждём игрока..."
    def cell(i):
        v = board[i]
        return "❌" if v == "X" else ("⭕" if v == "O" else "▫️")
    grid = (f"{cell(0)} {cell(1)} {cell(2)}\n{cell(3)} {cell(4)} {cell(5)}\n{cell(6)} {cell(7)} {cell(8)}")
    if game.get("finished"):
        footer = game.get("footer", "")
        if game.get("rematch_votes"):
            votes = game["rematch_votes"]
            n1 = html_mod.escape(game["names"]["x"]); n2 = html_mod.escape(game["names"].get("o") or "?")
            vx = "✅" if game["x_id"] in votes else "⏳"
            vo = "✅" if game.get("o_id") in votes else "⏳"
            footer += f"\n\n🔄 Реванш:\n{vx} {n1}\n{vo} {n2}"
    else:
        footer = f"Ход: {'❌' if game['turn'] == 'x' else '⭕'}"
    return (f"❌⭕ <b>Крестики-нолики</b> ❌⭕\n\n❌ {html_mod.escape(x_name)}\n⭕ {html_mod.escape(o_name)}\n\n"
            f"{grid}\n\n{footer}")


def ttt_keyboard(game):
    board = game["board"]
    b = InlineKeyboardBuilder()
    if not game.get("o_id") and not game.get("finished"):
        b.button(text="🙋 Присоединиться", callback_data="ttt_join"); return b.as_markup()
    row = []
    for i in range(9):
        if board[i] is None and not game.get("finished"):
            row.append(InlineKeyboardButton(text="⬜", callback_data=f"ttt_move:{i}"))
        else:
            row.append(InlineKeyboardButton(text="❌" if board[i] == "X" else ("⭕" if board[i] == "O" else "⬜"),
                                            callback_data="ttt_noop"))
        if len(row) == 3: b.row(*row); row = []
    if game.get("finished"):
        b.row(InlineKeyboardButton(text="🔄 Предложить реванш", callback_data="ttt_rematch"))
    else:
        b.row(InlineKeyboardButton(text="🏳️ Сдаться", callback_data="ttt_reset"))
    return b.as_markup()


def ttt_check_winner(board):
    for a, b, c in TTT_WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]: return board[a]
    if all(c is not None for c in board): return "draw"
    return None


async def _ttt_refresh(old_message, game, bc_id=None):
    chat_id = old_message.chat.id
    text = ttt_board_text(game); kb = ttt_keyboard(game)
    if bc_id:
        if _is_dead_bc(bc_id): return
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=old_message.message_id,
                                        text=text, reply_markup=kb, parse_mode="HTML",
                                        business_connection_id=bc_id)
        except Exception as e:
            if _is_peer_invalid(e): await _notify_owner_bc_dead(bc_id)
    else:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=old_message.message_id,
                                        text=text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            try: await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
            except Exception: pass


@dp.message(F.text.func(lambda t: t and t.strip().lower() == ".ttt"))
async def cmd_ttt(message):
    await _try_delete_command(message)
    chat_id = message.chat.id
    if chat_id in TTT_GAMES and not TTT_GAMES[chat_id].get("finished"):
        await message.answer("⚠️ Игра уже идёт."); return
    user = message.from_user
    name = user.full_name or (f"@{user.username}" if user.username else "Игрок")
    TTT_GAMES[chat_id] = {"board": [None]*9, "x_id": user.id, "o_id": None, "turn": "x",
                          "names": {"x": name, "o": None}, "finished": False, "footer": "", "rematch_votes": []}
    await message.answer(ttt_board_text(TTT_GAMES[chat_id]), reply_markup=ttt_keyboard(TTT_GAMES[chat_id]), parse_mode="HTML")


@dp.callback_query(F.data == "ttt_join")
async def cb_ttt_join(cb):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game: await cb.answer("Не найдена", show_alert=True); return
    if game["finished"] or game.get("o_id"): await cb.answer("Занято", show_alert=True); return
    if cb.from_user.id == game["x_id"]: await cb.answer("Ты уже ❌", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["o_id"] = u.id; game["names"]["o"] = name
    await _ttt_refresh(cb.message, game, game.get("bc_id")); await cb.answer("Погнали!")


@dp.callback_query(F.data.startswith("ttt_move:"))
async def cb_ttt_move(cb):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game or game["finished"]: await cb.answer("Недоступно", show_alert=True); return
    if not game.get("o_id"): await cb.answer("Ждём игрока", show_alert=True); return
    turn = game["turn"]; expected = game["x_id"] if turn == "x" else game["o_id"]
    if cb.from_user.id != expected: await cb.answer("Не твой ход", show_alert=True); return
    try: idx = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Ошибка", show_alert=True); return
    if idx < 0 or idx > 8 or game["board"][idx] is not None: await cb.answer("Занято", show_alert=True); return
    game["board"][idx] = "X" if turn == "x" else "O"
    w = ttt_check_winner(game["board"])
    if w == "X": game["finished"] = True; game["footer"] = f"🏆 Победа! ❌ {html_mod.escape(game['names']['x'])}"; game["rematch_votes"] = []
    elif w == "O": game["finished"] = True; game["footer"] = f"🏆 Победа! ⭕ {html_mod.escape(game['names']['o'])}"; game["rematch_votes"] = []
    elif w == "draw": game["finished"] = True; game["footer"] = "🤝 Ничья!"; game["rematch_votes"] = []
    else: game["turn"] = "o" if turn == "x" else "x"
    await _ttt_refresh(cb.message, game, game.get("bc_id")); await cb.answer()


@dp.callback_query(F.data == "ttt_reset")
async def cb_ttt_reset(cb):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game: await cb.answer("Не найдена", show_alert=True); return
    if cb.from_user.id not in (game["x_id"], game.get("o_id")): await cb.answer("Только игроки", show_alert=True); return
    game["board"] = [None]*9; game["turn"] = "x"; game["finished"] = False
    game["footer"] = ""; game["rematch_votes"] = []
    await _ttt_refresh(cb.message, game, game.get("bc_id")); await cb.answer("Поле очищено")


@dp.callback_query(F.data == "ttt_rematch")
async def cb_ttt_rematch(cb):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game or not game.get("finished"): await cb.answer("Недоступно", show_alert=True); return
    uid = cb.from_user.id
    if uid not in (game["x_id"], game.get("o_id")): await cb.answer("Только игроки", show_alert=True); return
    votes = game.setdefault("rematch_votes", [])
    if uid not in votes: votes.append(uid)
    x_id, o_id = game["x_id"], game.get("o_id")
    if x_id in votes and o_id in votes:
        # стартует тот кто предложил первым (первый в списке)
        first = votes[0]
        game["board"] = [None]*9
        game["finished"] = False
        game["footer"] = ""
        game["rematch_votes"] = []
        game["turn"] = "x" if first == x_id else "o"
        await _ttt_refresh(cb.message, game, game.get("bc_id"))
        await cb.answer("Реванш!")
    else:
        await _ttt_refresh(cb.message, game, game.get("bc_id"))
        await cb.answer("Ждём второго игрока")


@dp.callback_query(F.data == "ttt_noop")
async def cb_ttt_noop(cb): await cb.answer()


# ============ UNO ============
UNO_COLORS = ["🔴", "🔵", "🟢", "🟡"]
UNO_COLOR_NAMES = {"🔴": "красный", "🔵": "синий", "🟢": "зелёный", "🟡": "жёлтый"}
UNO_NUM_EMOJI = {0:"0️⃣",1:"1️⃣",2:"2️⃣",3:"3️⃣",4:"4️⃣",5:"5️⃣",6:"6️⃣",7:"7️⃣",8:"8️⃣",9:"9️⃣"}


def _make_uno_deck():
    deck = []
    for c in UNO_COLORS:
        deck.append({"color": c, "type": "num", "num": 0})
        for _ in range(2):
            for n in range(1, 10): deck.append({"color": c, "type": "num", "num": n})
            deck.append({"color": c, "type": "skip"})
            deck.append({"color": c, "type": "reverse"})
            deck.append({"color": c, "type": "draw2"})
    for _ in range(4):
        deck.append({"color": "🌈", "type": "wild"})
        deck.append({"color": "🌈", "type": "wild4"})
    random.shuffle(deck)
    return deck


def _uno_card_label(card):
    if card["type"] == "num": return f"{card['color']} {UNO_NUM_EMOJI.get(card['num'], str(card['num']))}"
    if card["type"] == "skip": return f"{card['color']} 🚫"
    if card["type"] == "reverse": return f"{card['color']} 🔄"
    if card["type"] == "draw2": return f"{card['color']} +2"
    if card["type"] == "wild": return "🌈 Wild"
    if card["type"] == "wild4": return "🌈 +4"
    return "?"


def _uno_can_play(card, top, current_color):
    if card["color"] == "🌈": return True
    if card["color"] == current_color: return True
    if card["type"] == "num" and top["type"] == "num" and card["num"] == top["num"]: return True
    if card["type"] != "num" and card["type"] == top["type"]: return True
    return False


def _uno_deck_text(game, player_id):
    hand = game["hands"].get(str(player_id), [])
    top = game["top"]
    cur = game.get("current_color", top["color"])
    txt = (f"🃏 <b>Твой ход!</b>\n\n"
           f"Верхняя карта: {_uno_card_label(top)}\n"
           f"Текущий цвет: {cur} {UNO_COLOR_NAMES.get(cur, '')}\n\n"
           f"<b>Твоя рука ({len(hand)}):</b>\n")
    for i, c in enumerate(hand):
        txt += f"{i+1}. {_uno_card_label(c)}\n"
    return txt


def _uno_hand_kb(game, player_id, bc_id=None):
    hand = game["hands"].get(str(player_id), [])
    top = game["top"]; cur = game.get("current_color", top["color"])
    b = InlineKeyboardBuilder()
    row = []
    for i, c in enumerate(hand):
        playable = _uno_can_play(c, top, cur)
        label = _uno_card_label(c)
        marker = "" if playable else "·"
        row.append(InlineKeyboardButton(text=f"{marker}{label}", callback_data=f"uno_play:{i}"))
        if len(row) == 4: b.row(*row); row = []
    if row: b.row(*row)
    b.row(InlineKeyboardButton(text="🃏 Взять карту", callback_data="uno_draw"))
    return b.as_markup()


def _uno_group_kb(game):
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📣 Уно!", callback_data="uno_shout"),
          InlineKeyboardButton(text="🃏 Взять карту", callback_data="uno_group_draw"))
    return b.as_markup()


def _uno_lobby_text(game):
    players = game["players"]
    lines = ["🎴 <b>Набор в игру Уно</b>\n"]
    if players:
        names = ", ".join(html_mod.escape(p["name"]) for p in players)
        lines.append(f"👥 Игроки ({len(players)}): {names}")
    else:
        lines.append("👥 Пока никого. Жми «Присоединиться»!")
    if game.get("no_timer"):
        lines.append("\n⏸ Таймер отключён. Когда все зашли — админ пишет <code>/play</code>")
    else:
        lines.append(f"\n⏱ Начало автоматически через {game.get('time_left', 60)} сек (или раньше, если 4+ игрока)")
    lines.append("\nМинимум 2 игрока (в группе — 2, в ЛС — 2).")
    return "\n".join(lines)


def _uno_lobby_kb(game):
    b = InlineKeyboardBuilder()
    b.button(text="🙋 Присоединиться", callback_data="uno_join")
    return b.as_markup()


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith("/uno")))
async def cmd_uno(message):
    await _try_delete_command(message)
    chat_id = message.chat.id
    args = (message.text or "").split()
    no_timer = "notimer" in [a.lower() for a in args[1:]]

    if message.chat.type == "private":
        # ЛС — 1 на 1 с ботом
        await _start_uno_ls(message)
        return

    if chat_id in UNO_GAMES:
        await message.answer("⚠️ Игра уже создаётся/идёт в этом чате."); return
    user = message.from_user
    name = user.full_name or (f"@{user.username}" if user.username else "Игрок")
    UNO_GAMES[chat_id] = {
        "mode": "group", "players": [{"id": user.id, "name": name}],
        "bc_id": message.business_connection_id,
        "no_timer": no_timer, "time_left": 60, "started": False,
    }
    game = UNO_GAMES[chat_id]
    msg = await message.answer(_uno_lobby_text(game), reply_markup=_uno_lobby_kb(game), parse_mode="HTML")
    game["lobby_msg_id"] = msg.message_id
    game["lobby_chat_id"] = chat_id
    if not no_timer:
        asyncio.create_task(_uno_lobby_timer(chat_id))


async def _start_uno_ls(message):
    """ЛС: юзер против бота."""
    chat_id = message.chat.id
    if chat_id in UNO_GAMES:
        await message.answer("⚠️ Игра уже идёт. /cancel чтобы сбросить."); return
    deck = _make_uno_deck()
    uid = message.from_user.id
    name = message.from_user.full_name or "Ты"
    game = {
        "mode": "ls", "players": [{"id": uid, "name": name}, {"id": 0, "name": "🤖 Бот"}],
        "hands": {str(uid): [deck.pop() for _ in range(7)], "0": [deck.pop() for _ in range(7)]},
        "deck": deck, "top": None, "current_color": None, "turn_idx": 0,
        "direction": 1, "started": True, "chat_id": chat_id, "bc_id": None,
        "bot_msg_id": None, "dir_switch": 1,
    }
    # первая карта
    while True:
        first = deck.pop()
        if first["type"] == "num": break
        deck.insert(0, first)
    game["top"] = first; game["current_color"] = first["color"]
    UNO_GAMES[chat_id] = game
    await _send_uno_ls_hand(message, game)


async def _send_uno_ls_hand(message, game, edit_msg=None):
    uid = message.chat.id
    hand = game["hands"][str(uid)]
    txt = _uno_deck_text(game, uid)
    kb = _uno_hand_kb(game, uid)
    if edit_msg:
        try: await bot.edit_message_text(chat_id=uid, message_id=edit_msg.message_id, text=txt, reply_markup=kb, parse_mode="HTML")
        except Exception: await bot.send_message(uid, txt, reply_markup=kb, parse_mode="HTML")
    else:
        await bot.send_message(uid, txt, reply_markup=kb, parse_mode="HTML")


async def _uno_lobby_timer(chat_id):
    try:
        for i in range(60, 0, -5):
            await asyncio.sleep(5)
            game = UNO_GAMES.get(chat_id)
            if not game or game.get("started"): return
            game["time_left"] = i - 5
            if game["time_left"] <= 0: break
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                            text=_uno_lobby_text(game),
                                            reply_markup=_uno_lobby_kb(game), parse_mode="HTML")
            except Exception: pass
        game = UNO_GAMES.get(chat_id)
        if game and not game.get("started") and len(game["players"]) >= 2:
            await _uno_start_group(chat_id)
        elif game and not game.get("started"):
            try: await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                             text="❌ Недостаточно игроков (нужно 2+). Игра отменена.")
            except Exception: pass
            UNO_GAMES.pop(chat_id, None)
    except Exception as e: print(f"[uno timer] {e}")


async def _uno_start_group(chat_id):
    game = UNO_GAMES.get(chat_id)
    if not game or game.get("started"): return
    bc_id = game.get("bc_id")
    deck = _make_uno_deck()
    game["hands"] = {str(p["id"]): [deck.pop() for _ in range(7)] for p in game["players"]}
    game["deck"] = deck
    while True:
        first = deck.pop()
        if first["type"] == "num": break
        deck.insert(0, first)
    game["top"] = first; game["current_color"] = first["color"]
    game["turn_idx"] = 0; game["direction"] = 1; game["started"] = True

    # уведомляем группу
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                    text="🎴 <b>Уно началось!</b>", parse_mode="HTML")
    except Exception: pass

    # рассылаем руки
    for p in game["players"]:
        try:
            hand = game["hands"][str(p["id"])]
            txt = (f"🎴 <b>Уно началось!</b>\n\n"
                   f"Верхняя карта: {_uno_card_label(game['top'])}\n"
                   f"Текущий цвет: {game['current_color']}\n\n"
                   f"<b>Твоя рука ({len(hand)}):</b>\n")
            for i, c in enumerate(hand): txt += f"{i+1}. {_uno_card_label(c)}\n"
            sent = await bot.send_message(p["id"], txt, reply_markup=_uno_hand_kb(game, p["id"]), parse_mode="HTML")
            game["hands"][str(p["id"])] = hand
            game.setdefault("hand_msg_ids", {})[str(p["id"])] = sent.message_id
        except Exception as e:
            print(f"[uno dm {p['id']}] {str(e)[:150]}")
            try: await bot.send_message(chat_id, f"⚠️ Не смог написать @{p['name']} в ЛС. Он должен сначала написать боту /start.",
                                        business_connection_id=bc_id)
            except Exception: pass

    # сообщение в группе с кнопками
    cur_p = game["players"][0]
    group_text = (f"🎴 <b>Уно</b>\n\n"
                  f"Верхняя карта: {_uno_card_label(game['top'])}\n"
                  f"Цвет: {game['current_color']}\n\n"
                  f"👤 Ход: <b>{html_mod.escape(cur_p['name'])}</b>\n"
                  f"У него {len(game['hands'][str(cur_p['id'])])} карт.\n\n"
                  f"Жми свою карту в ЛС у бота 👆")
    try:
        msg = await bot.send_message(chat_id, group_text, reply_markup=_uno_group_kb(game),
                                      parse_mode="HTML", business_connection_id=bc_id)
        game["group_msg_id"] = msg.message_id
    except Exception as e: print(f"[uno group msg] {e}")


@dp.callback_query(F.data == "uno_join")
async def cb_uno_join(cb):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or game.get("started"): await cb.answer("Игра уже идёт", show_alert=True); return
    uid = cb.from_user.id
    if any(p["id"] == uid for p in game["players"]): await cb.answer("Ты уже в игре", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["players"].append({"id": uid, "name": name})
    try: await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                      text=_uno_lobby_text(game), reply_markup=_uno_lobby_kb(game), parse_mode="HTML")
    except Exception: pass
    await cb.answer("Зашёл!")
    if len(game["players"]) >= 4 and not game.get("no_timer"):
        await _uno_start_group(chat_id)


@dp.message(F.text == "/play")
async def cmd_play(message):
    await _try_delete_command(message)
    if not is_owner(message.from_user): return
    chat_id = message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or game.get("started"): await message.answer("Нет активного лобби."); return
    if len(game["players"]) < 2: await message.answer("⚠️ Нужно минимум 2 игрока."); return
    await _uno_start_group(chat_id)


@dp.callback_query(F.data.startswith("uno_play:"))
async def cb_uno_play(cb):
    # из ЛС
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game: await cb.answer("Игра не найдена", show_alert=True); return
    try: idx = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Ошибка", show_alert=True); return
    uid = cb.from_user.id
    # ход?
    if game["mode"] == "ls":
        cur_id = game["players"][game["turn_idx"]]["id"]
        if uid != cur_id: await cb.answer("Не твой ход", show_alert=True); return
    else:
        cur_id = game["players"][game["turn_idx"]]["id"]
        if uid != cur_id: await cb.answer("Сейчас не твой ход", show_alert=True); return
    hand = game["hands"].get(str(uid), [])
    if idx < 0 or idx >= len(hand): await cb.answer("Нет такой карты", show_alert=True); return
    card = hand[idx]
    top = game["top"]; cur_color = game.get("current_color", top["color"])
    if not _uno_can_play(card, top, cur_color): await cb.answer("Эту карту нельзя", show_alert=True); return
    # сбрасываем
    hand.pop(idx)
    game["top"] = card

    bc_id = game.get("bc_id")
    # wild — попросим цвет в ЛС
    if card["type"] in ("wild", "wild4"):
        b = InlineKeyboardBuilder()
        for c in UNO_COLORS: b.button(text=c, callback_data=f"uno_color:{c}")
        b.adjust(4)
        try:
            await bot.send_message(uid, "🎨 Выбери цвет:", reply_markup=b.as_markup())
        except Exception: pass
        game["pending_color_player"] = uid
        # обновим руку
        await _uno_update_hand_dm(uid, game)
        await cb.answer("Выбери цвет в ЛС")
        return

    game["current_color"] = card["color"]
    await _uno_after_play(chat_id, uid, card, bc_id)
    await cb.answer()


async def _uno_update_hand_dm(uid, game, msg_id=None):
    hand = game["hands"].get(str(uid), [])
    txt = (f"🃏 <b>Твоя рука ({len(hand)}):</b>\n\n"
           f"Верхняя: {_uno_card_label(game['top'])}\nЦвет: {game.get('current_color','?')}\n\n")
    for i, c in enumerate(hand): txt += f"{i+1}. {_uno_card_label(c)}\n"
    kb = _uno_hand_kb(game, uid)
    if msg_id:
        try: await bot.edit_message_text(chat_id=uid, message_id=msg_id, text=txt, reply_markup=kb, parse_mode="HTML")
        except Exception: pass
    else:
        try: await bot.send_message(uid, txt, reply_markup=kb, parse_mode="HTML")
        except Exception: pass


async def _uno_after_play(chat_id, uid, card, bc_id):
    game = UNO_GAMES.get(chat_id)
    if not game: return
    # проверка победы
    if not game["hands"].get(str(uid)):
        game["finished"] = True
        winner_name = next((p["name"] for p in game["players"] if p["id"] == uid), "?")
        text = f"🏆 <b>{html_mod.escape(winner_name)} выиграл в Уно!</b>"
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML", business_connection_id=bc_id)
        except Exception: pass
        UNO_GAMES.pop(chat_id, None)
        return

    # эффект карты
    if card["type"] == "skip":
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
    elif card["type"] == "reverse":
        game["direction"] *= -1
    elif card["type"] == "draw2":
        nxt = (game["turn_idx"] + game["direction"]) % len(game["players"])
        nxt_id = game["players"][nxt]["id"]
        for _ in range(2):
            if game["deck"]:
                game["hands"][str(nxt_id)].append(game["deck"].pop())
        # уведомим в ЛС
        try:
            await bot.send_message(nxt_id, "😬 Ты получил +2 карты!")
            await _uno_update_hand_dm(nxt_id, game)
        except Exception: pass
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
    elif card["type"] == "wild4":
        nxt = (game["turn_idx"] + game["direction"]) % len(game["players"])
        nxt_id = game["players"][nxt]["id"]
        for _ in range(4):
            if game["deck"]:
                game["hands"][str(nxt_id)].append(game["deck"].pop())
        try:
            await bot.send_message(nxt_id, "😬 Ты получил +4 карты!")
            await _uno_update_hand_dm(nxt_id, game)
        except Exception: pass
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
    else:
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])

    # уведомление в группу
    cur_p = game["players"][game["turn_idx"]]
    group_text = (f"🎴 <b>Уно</b>\n\n"
                  f"{html_mod.escape(next((p['name'] for p in game['players'] if p['id'] == uid), '?'))} "
                  f"сбросил {_uno_card_label(card)}\n"
                  f"Цвет: {game['current_color']}\n\n"
                  f"👤 Ход: <b>{html_mod.escape(cur_p['name'])}</b>")
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=game.get("group_msg_id"),
                                     text=group_text, reply_markup=_uno_group_kb(game),
                                     parse_mode="HTML", business_connection_id=bc_id)
    except Exception:
        try:
            msg = await bot.send_message(chat_id, group_text, reply_markup=_uno_group_kb(game),
                                          parse_mode="HTML", business_connection_id=bc_id)
            game["group_msg_id"] = msg.message_id
        except Exception: pass

    # уведомление текущему игроку в ЛС
    try:
        await bot.send_message(cur_p["id"], f"👆 Твой ход! Верхняя: {_uno_card_label(game['top'])}")
    except Exception: pass
    await _uno_update_hand_dm(cur_p["id"], game)

    # если в ЛС и ход бота — бот сходит
    if game["mode"] == "ls" and cur_p["id"] == 0:
        await asyncio.sleep(1.5)
        await _uno_bot_move(chat_id)


async def _uno_bot_move(chat_id):
    game = UNO_GAMES.get(chat_id)
    if not game: return
    hand = game["hands"].get("0", [])
    top = game["top"]; cur_color = game.get("current_color", top["color"])
    playable = [(i, c) for i, c in enumerate(hand) if _uno_can_play(c, top, cur_color)]
    if playable:
        i, card = random.choice(playable)
        hand.pop(i); game["top"] = card
        if card["color"] == "🌈":
            game["current_color"] = random.choice(UNO_COLORS)
        else: game["current_color"] = card["color"]
        await _uno_update_hand_dm(chat_id, game)  # юзеру
        await _uno_after_play(chat_id, 0, card, None)
    else:
        # берёт карту
        if game["deck"]:
            hand.append(game["deck"].pop())
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
        try: await bot.send_message(chat_id, "🤖 Бот взял карту.")
        except Exception: pass
        await _uno_update_hand_dm(chat_id, game)


@dp.callback_query(F.data.startswith("uno_color:"))
async def cb_uno_color(cb):
    color = cb.data.split(":", 1)[1]
    uid = cb.from_user.id
    # находим игру где этот игрок ждёт цвет
    target = None
    for cid, g in UNO_GAMES.items():
        if g.get("pending_color_player") == uid:
            target = (cid, g); break
    if not target: await cb.answer("Не актуально", show_alert=True); return
    cid, game = target
    game["current_color"] = color
    game.pop("pending_color_player", None)
    await cb.answer(f"Цвет: {color}")
    try: await cb.message.edit_text(f"🎨 Цвет выбран: {color}")
    except Exception: pass
    card = game["top"]
    await _uno_after_play(cid, uid, card, game.get("bc_id"))


@dp.callback_query(F.data == "uno_draw")
async def cb_uno_draw(cb):
    uid = cb.from_user.id
    # ЛС — это ход юзера
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game: await cb.answer("Игра не найдена", show_alert=True); return
    cur_id = game["players"][game["turn_idx"]]["id"]
    if uid != cur_id: await cb.answer("Не твой ход", show_alert=True); return
    if not game["deck"]: await cb.answer("Колода пуста", show_alert=True); return
    game["hands"][str(uid)].append(game["deck"].pop())
    await _uno_update_hand_dm(uid, game, cb.message.message_id)
    await cb.answer("Взял карту")


@dp.callback_query(F.data == "uno_group_draw")
async def cb_uno_group_draw(cb):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or not game.get("started"): await cb.answer("Нет игры", show_alert=True); return
    uid = cb.from_user.id
    cur_id = game["players"][game["turn_idx"]]["id"]
    if uid != cur_id: await cb.answer("Сейчас не твой ход", show_alert=True); return
    if not game["deck"]: await cb.answer("Колода пуста", show_alert=True); return
    card = game["deck"].pop()
    game["hands"][str(uid)].append(card)
    await cb.answer(f"Взял {_uno_card_label(card)}")
    try: await bot.send_message(uid, f"🃏 Взял: {_uno_card_label(card)}")
    except Exception: pass
    await _uno_update_hand_dm(uid, game)


@dp.callback_query(F.data == "uno_shout")
async def cb_uno_shout(cb):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or not game.get("started"): await cb.answer("Нет игры", show_alert=True); return
    uid = cb.from_user.id
    if uid not in [p["id"] for p in game["players"]]: await cb.answer("Ты не игрок", show_alert=True); return
    hand = game["hands"].get(str(uid), [])
    if len(hand) == 1:
        await cb.answer("📣 УНО!", show_alert=True)
        try:
            name = next((p["name"] for p in game["players"] if p["id"] == uid), "?")
            await bot.send_message(chat_id, f"📣 <b>{html_mod.escape(name)}</b> крикнул УНО!",
                                    parse_mode="HTML", business_connection_id=game.get("bc_id"))
        except Exception: pass
    elif len(hand) > 1:
        await cb.answer("Отставить! Уно можно крикнуть только с 1 картой.", show_alert=True)
    else:
        await cb.answer("У тебя 0 карт, ты уже победил!", show_alert=True)


# ============ ХЕНДЛЕРЫ ОБЩИЕ ============
@dp.message(F.text == "/whoami")
async def cmd_whoami(message):
    u = message.from_user
    await message.answer(f"ID: {u.id}\nUsername: @{html_mod.escape(u.username or 'нет')}\n"
                          f"Имя: {html_mod.escape(u.full_name)}\n\n"
                          f"Ты владелец? {'ДА' if is_owner(u) else 'НЕТ'}", parse_mode="HTML")


@dp.message(F.text == "/start")
async def cmd_start(message):
    bot_uname = await get_bot_username()
    if is_owner(message.from_user):
        text = ("👋 Привет!\n\n"
                "📷 Фото — режу на 3×N\n🎬 TikTok — видео/MP3\n🎵 Аудио — теги\n"
                "🎤 Голосовое — в текст\n🎨 Стикерпаки\n"
                "🎲 .ruletka — русская рулетка\n"
                "🔁 .switch — раскладка (реплай)\n"
                "🔊 .tts текст — озвучка\n"
                "🎴 /uno — Уно\n"
                f"🤫 Шёпот — <code>@{bot_uname} текст @username</code>\n\n"
                "👑 Админ-панель включена.")
        await message.answer(text, reply_markup=owner_reply_kb(), parse_mode="HTML")
    else:
        text = ("👋 Привет!\n\n📷 Фото — 3×N\n🎬 TikTok — видео/MP3\n🎵 Аудио — теги\n"
                "🎤 Голосовое — в текст\n🎨 Стикерпаки\n🎲 .ruletka\n🔁 .switch\n🔊 .tts\n🎴 /uno\n"
                f"🤫 Шёпот — <code>@{bot_uname} текст @username</code>\n\n"
                "🔑 /code ТВОЙ_КЛЮЧ")
        await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "/help")
async def cmd_help(message):
    bot_uname = await get_bot_username()
    await message.answer(
        "📖 <b>Что умею:</b>\n\n"
        "🎤 Голосовое — расшифровка\n"
        "🎲 <code>.ruletka</code> — русская рулетка\n"
        "🔁 <code>.switch</code> — раскладка (реплай)\n"
        "🔊 <code>.tts текст</code> — озвучка\n"
        "🎴 <code>/uno</code> — Уно\n"
        "🎨 <code>/stickers</code> — стикерпаки\n"
        "🔑 <code>/mykey</code> — статус ключа\n\n"
        f"🤫 Шёпот: <code>@{bot_uname} текст @username</code>", parse_mode="HTML")


@dp.message(F.text.startswith("/code"))
async def cmd_code(message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2: await message.answer("⚠️ /code ТВОЙ_КЛЮЧ"); return
    key = parts[1].strip().strip("`").strip().upper()
    if len(key) != KEY_LENGTH or not key.isalnum():
        await message.answer("❌ Странный ключ."); return
    await try_activate_key(message, message.from_user, key)


@dp.message(F.text == "/mykey")
async def cmd_mykey(message):
    u = DATA["users"].get(str(message.from_user.id))
    if not u: await message.answer("🤷 Нет ключа."); return
    if u.get("permanent"): await message.answer(f"🔑 <code>{html_mod.escape(u.get('key'))}</code>\n♾ навсегда", parse_mode="HTML")
    else:
        exp = u.get("expires_at", 0); rem = max(0, exp - time.time())
        await message.answer(f"🔑 <code>{html_mod.escape(u.get('key'))}</code>\n"
                              f"⏳ {format_duration(rem)}\n📅 До: {format_until(exp)}", parse_mode="HTML")


@dp.message(F.text == "/cancel")
async def cmd_cancel(message, state):
    data = await state.get_data()
    for k in ("audio_path", "cover_path", "photo_path"):
        p = data.get(k)
        if p and os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
    await state.clear()
    chat_id = message.chat.id
    UNO_GAMES.pop(chat_id, None)
    MAFIA_GAMES.pop(chat_id, None)
    await message.answer("👌")


@dp.message(F.text == "/whisper")
async def cmd_whisper(message):
    bot_uname = await get_bot_username()
    await message.answer(f"🤫 <code>@{bot_uname} текст @username</code>", parse_mode="HTML")


# ============ ШЁПОТ ============
def _next_whisper_id():
    c = int(DATA.get("whisper_counter", 1)); DATA["whisper_counter"] = c + 1; return c


@dp.inline_query()
async def inline_whisper(query):
    text = (query.query or "").strip()
    bot_uname = (await get_bot_username()).lower()
    if not text:
        result = InlineQueryResultArticle(id="h1", title="🤫 Напиши: текст @username",
            description="Например: привет @vimbrix",
            input_message_content=InputTextMessageContent(message_text=f"🤫 <code>@{bot_uname} текст @username</code>", parse_mode="HTML"))
        await query.answer([result], cache_time=0, is_personal=True); return
    mentions = [m for m in re.finditer(r"@(\w+)", text) if m.group(1).lower() != bot_uname]
    if not mentions:
        result = InlineQueryResultArticle(id="h2", title="🤫 Добавь @username",
            description="Например: привет @vimbrix",
            input_message_content=InputTextMessageContent(message_text=f"🤫 <code>@{bot_uname} текст @username</code>", parse_mode="HTML"))
        await query.answer([result], cache_time=0, is_personal=True); return
    tm = mentions[-1]; tuser = tm.group(1); wtext = text[:tm.start()].strip()
    if not wtext:
        result = InlineQueryResultArticle(id="h3", title="🤫 Что шептать?",
            description="Напиши текст", input_message_content=InputTextMessageContent(message_text="🤫 Добавь текст."))
        await query.answer([result], cache_time=0, is_personal=True); return
    tid = None
    for uid, ud in DATA["users"].items():
        if (ud.get("username") or "").lower() == tuser.lower(): tid = int(uid); break
    wid = _next_whisper_id()
    DATA["whispers"][str(wid)] = {"target_id": tid, "target_name": tuser, "text": wtext,
        "from_id": query.from_user.id, "from_name": query.from_user.full_name, "expires": time.time() + WHISPER_TTL}
    save_data(DATA)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Прочитать", callback_data=f"whisper:{wid}")],
        [InlineKeyboardButton(text="Как?", callback_data="whisper_help")]])
    result = InlineQueryResultArticle(id=str(wid), title=f"🤫 Шёпот для @{tuser}",
        description=wtext[:60],
        input_message_content=InputTextMessageContent(message_text=f"🔒 Секретное для @{tuser}"),
        reply_markup=kb)
    await query.answer([result], cache_time=0, is_personal=True)


@dp.callback_query(F.data.startswith("whisper:"))
async def cb_whisper(cb):
    try: wid = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Сломался", show_alert=True); return
    w = DATA["whispers"].get(str(wid))
    if not w: await cb.answer("Улетел.", show_alert=True); return
    if w.get("expires", 0) < time.time():
        DATA["whispers"].pop(str(wid), None); save_data(DATA); await cb.answer("Устарел.", show_alert=True); return
    is_target = (w.get("target_id") is not None and cb.from_user.id == w["target_id"]) or \
                ((cb.from_user.username or "").lower() == (w.get("target_name") or "").lower())
    is_sender = cb.from_user.id == w.get("from_id")
    if is_sender:
        txt = w["text"]
        if len(txt) <= 180: await cb.answer(f"📤 Твой для @{w['target_name']}:\n\n{txt}", show_alert=True)
        else:
            try: await bot.send_message(cb.from_user.id, f"📤 {txt}"); await cb.answer("📩 В личку.", show_alert=True)
            except Exception: await cb.answer(f"📤 {txt[:180]}...", show_alert=True)
    elif is_target:
        txt = w["text"]
        if len(txt) <= 180: await cb.answer(f"🤫 {txt}", show_alert=True)
        else:
            try: await bot.send_message(cb.from_user.id, f"🤫 от {w['from_name']}:\n\n{txt}"); await cb.answer("📩 В личку.", show_alert=True)
            except Exception: await cb.answer(f"🤫 {txt[:180]}...", show_alert=True)
    else: await cb.answer(random.choice(FUNNY_REPLIES), show_alert=True)


@dp.callback_query(F.data == "whisper_help")
async def cb_whisper_help(cb):
    bot_uname = await get_bot_username()
    await cb.answer(f"🤫 Напиши:\n@{bot_uname} текст @username", show_alert=True)


# ============ СТИКЕРПАКИ ============
@dp.message(F.text == "/stickers")
async def cmd_stickers(message, state):
    await state.set_state(None)
    b = InlineKeyboardBuilder()
    b.button(text="➕ Создать", callback_data="st_create")
    b.button(text="📎 Добавить", callback_data="st_add")
    b.button(text="📂 Мои паки", callback_data="st_list")
    b.adjust(1)
    await message.answer("🎨 <b>Стикерпаки</b>", reply_markup=b.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "st_create")
async def cb_st_create(cb, state):
    bot_uname = await get_bot_username()
    await cb.message.edit_text(f"📝 Короткое имя (латиница).\nПак: <code>имя_by_{bot_uname}</code>", parse_mode="HTML")
    await state.set_state(BotStates.waiting_sticker_name); await cb.answer()


@dp.message(BotStates.waiting_sticker_name)
async def process_sticker_name(message, state):
    short = (message.text or "").strip().lower()
    if not validate_pack_shortname(short): await message.answer("⚠️ Только латиница, цифры, _"); return
    bot_uname = await get_bot_username(); full = f"{short}_by_{bot_uname}"
    if len(full) > 64: await message.answer("⚠️ Слишком длинное."); return
    await state.update_data(sticker_short=short, sticker_full=full)
    await state.set_state(BotStates.waiting_sticker_title)
    await message.answer("📛 Название пака:")


@dp.message(BotStates.waiting_sticker_title)
async def process_sticker_title(message, state):
    title = (message.text or "").strip()
    if not title or len(title) > 64: await message.answer("⚠️ 1-64"); return
    await state.update_data(sticker_title=title)
    await state.set_state(BotStates.waiting_sticker_first)
    await message.answer("🖼 Пришли первую картинку 512×512.")


def photo_to_webp_sticker(src, dst):
    img = Image.open(src).convert("RGBA"); w, h = img.size; side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    canvas = canvas.resize((512, 512), Image.LANCZOS)
    canvas.save(dst, "WEBP", quality=95, method=6); return dst


def validate_pack_shortname(name):
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,50}$", name))


@dp.message(BotStates.waiting_sticker_first, F.photo)
async def process_sticker_first(message, state):
    data = await state.get_data(); full = data.get("sticker_full"); title = data.get("sticker_title")
    if not full or not title: await message.answer("⚠️ Заново: /stickers"); await state.clear(); return
    p = message.photo[-1]; fi = await bot.get_file(p.file_id)
    src = f"temp_photos/{p.file_id}.jpg"; dst = f"temp_photos/sticker_{p.file_id}.webp"
    await bot.download_file(fi.file_path, src)
    status = await message.answer("🎨 Готовлю...")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, photo_to_webp_sticker, src, dst)
        with open(dst, "rb") as f: sb = f.read()
        if len(sb) > 512 * 1024: raise RuntimeError(">512КБ")
        await bot.create_new_sticker_set(user_id=message.from_user.id, name=full, title=title,
            stickers=[InputSticker(sticker=BufferedInputFile(sb, filename="s.webp"), format="static", emoji_list=["😀"])])
        uid = str(message.from_user.id)
        DATA["sticker_packs"].setdefault(uid, []).append({"name": full, "title": title, "created_at": time.time()})
        save_data(DATA)
        await status.edit_text(f"✅ Пак создан!\n\n📛 {html_mod.escape(title)}\n🔗 https://t.me/addstickers/{full}")
    except Exception as e:
        try: await status.edit_text(f"❌ {str(e)[:300]}")
        except Exception: pass
    finally:
        for f in (src, dst):
            if os.path.exists(f):
                try: os.remove(f)
                except Exception: pass
        await state.clear()


@dp.message(BotStates.waiting_sticker_first)
async def wrong_sticker_first(message): await message.answer("🖼 Нужна картинка.")


@dp.callback_query(F.data == "st_list")
async def cb_st_list(cb):
    packs = DATA["sticker_packs"].get(str(cb.from_user.id), [])
    if not packs: await cb.message.edit_text("📂 Нет паков."); await cb.answer(); return
    lines = ["📂 Твои паки:\n"] + [f"• {html_mod.escape(p['title'])}\n  https://t.me/addstickers/{p['name']}" for p in packs]
    await cb.message.edit_text("\n".join(lines), parse_mode="HTML"); await cb.answer()


@dp.callback_query(F.data == "st_add")
async def cb_st_add(cb, state):
    packs = DATA["sticker_packs"].get(str(cb.from_user.id), [])
    if not packs: await cb.message.edit_text("📂 Нет паков."); await cb.answer(); return
    b = InlineKeyboardBuilder()
    for i, p in enumerate(packs): b.button(text=p["title"], callback_data=f"st_pick:{i}")
    b.adjust(1)
    await cb.message.edit_text("📎 В какой пак?", reply_markup=b.as_markup()); await cb.answer()


@dp.callback_query(F.data.startswith("st_pick:"))
async def cb_st_pick(cb, state):
    try: idx = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Ошибка", show_alert=True); return
    packs = DATA["sticker_packs"].get(str(cb.from_user.id), [])
    if idx < 0 or idx >= len(packs): await cb.answer("Нет", show_alert=True); return
    await state.update_data(add_pack=packs[idx]["name"])
    await state.set_state(BotStates.waiting_sticker_add_photo)
    await cb.message.edit_text(f"📎 {html_mod.escape(packs[idx]['title'])}\n\nПришли картинку.", parse_mode="HTML")
    await cb.answer()


@dp.message(BotStates.waiting_sticker_add_photo, F.photo)
async def process_sticker_add(message, state):
    data = await state.get_data(); pname = data.get("add_pack")
    if not pname: await message.answer("⚠️ Заново"); await state.clear(); return
    p = message.photo[-1]; fi = await bot.get_file(p.file_id)
    src = f"temp_photos/{p.file_id}.jpg"; dst = f"temp_photos/sticker_{p.file_id}.webp"
    await bot.download_file(fi.file_path, src)
    status = await message.answer("🎨 Готовлю...")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, photo_to_webp_sticker, src, dst)
        with open(dst, "rb") as f: sb = f.read()
        if len(sb) > 512 * 1024: raise RuntimeError(">512КБ")
        await bot.add_sticker_to_set(user_id=message.from_user.id, name=pname,
            sticker=InputSticker(sticker=BufferedInputFile(sb, filename="s.webp"), format="static", emoji_list=["😀"]))
        await status.edit_text("✅ Добавлен.")
    except Exception as e:
        try: await status.edit_text(f"❌ {str(e)[:300]}")
        except Exception: pass
    finally:
        for f in (src, dst):
            if os.path.exists(f):
                try: os.remove(f)
                except Exception: pass
        await state.clear()


@dp.message(BotStates.waiting_sticker_add_photo)
async def wrong_sticker_add(message): await message.answer("🖼 Нужна картинка.")


# ============ АУДИО ТЕГИ ============
@dp.message(F.audio)
async def process_audio_for_tag(message, state):
    a = message.audio
    old = await state.get_data()
    for k in ("audio_path", "cover_path"):
        p = old.get(k)
        if p and os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
    fi = await bot.get_file(a.file_id)
    ext = os.path.splitext(a.file_name or "audio.mp3")[1] or ".mp3"
    lp = f"downloads/tag_{a.file_id}{ext}"
    await bot.download_file(fi.file_path, lp)
    await state.update_data(audio_path=lp)
    await state.set_state(BotStates.waiting_for_cover)
    await message.answer("🎨 Кидай обложку.")


@dp.message(BotStates.waiting_for_cover, F.photo)
async def process_cover(message, state):
    p = message.photo[-1]; fi = await bot.get_file(p.file_id)
    cp = f"downloads/cover_{p.file_id}.jpg"
    await bot.download_file(fi.file_path, cp)
    await state.update_data(cover_path=cp)
    await state.set_state(BotStates.waiting_for_meta)
    await message.answer("✍️ Формат: Название | Исполнитель")


@dp.message(BotStates.waiting_for_cover)
async def wrong_cover(message): await message.answer("📷 Нужна картинка.")


@dp.message(BotStates.waiting_for_meta)
async def process_meta(message, state):
    t = (message.text or "").strip()
    if "|" not in t: await message.answer("⚠️ Формат: Название | Исполнитель"); return
    title, perf = [x.strip() for x in t.split("|", 1)]
    if not title: await message.answer("⚠️ Название пустое"); return
    if not perf: perf = "Unknown"
    data = await state.get_data(); ap = data.get("audio_path"); cp = data.get("cover_path")
    if not ap or not os.path.exists(ap): await message.answer("🤷 Аудио пропало."); await state.clear(); return
    status = await message.answer("🎧 Ставлю метки..."); out = None
    try:
        loop = asyncio.get_event_loop()
        out = await loop.run_in_executor(None, apply_tags, ap, cp, title, perf)
        dur = await loop.run_in_executor(None, get_duration, out)
        kw = {"audio": FSInputFile(out, filename=f"{title}.mp3"), "title": title,
              "performer": perf, "caption": "✅ Готово."}
        if dur: kw["duration"] = int(dur)
        if cp and os.path.exists(cp): kw["thumbnail"] = FSInputFile(cp)
        await message.answer_audio(**kw)
        try: await status.delete()
        except Exception: pass
    except Exception as e:
        try: await status.edit_text(f"❌ {str(e)[:250]}")
        except Exception: pass
    finally:
        for p in (ap, cp, out):
            if p and os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        await state.clear()


# ============ ФОТО НАРЕЗКА ============
@dp.message(F.document & F.document.mime_type.startswith("image/"))
async def process_photo_document(message, state):
    d = message.document; fi = await bot.get_file(d.file_id)
    ext = os.path.splitext(d.file_name or "img.png")[1] or ".png"
    lp = f"temp_photos/{d.file_id}{ext}"
    await bot.download_file(fi.file_path, lp)
    await state.update_data(photo_path=lp); await state.set_state(BotStates.waiting_for_parts)
    b = InlineKeyboardBuilder()
    b.button(text="🎲 Авто", callback_data="auto_split"); b.button(text="📎 Оригинал", callback_data="send_original")
    await message.answer("✂️ На сколько кусков? (кратно 3)", reply_markup=b.as_markup())


@dp.message(F.photo)
async def process_photo(message, state):
    p = message.photo[-1]; fi = await bot.get_file(p.file_id)
    lp = f"temp_photos/{p.file_id}.jpg"
    await bot.download_file(fi.file_path, lp)
    await state.update_data(photo_path=lp); await state.set_state(BotStates.waiting_for_parts)
    b = InlineKeyboardBuilder()
    b.button(text="🎲 Авто", callback_data="auto_split"); b.button(text="📎 Оригинал", callback_data="send_original")
    await message.answer("⚠️ Фото сжато TG. Пришли <b>как файл</b> для качества.\n\n✂️ На сколько?",
                         reply_markup=b.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "auto_split", BotStates.waiting_for_parts)
async def auto_split(cb, state):
    d = await state.get_data(); pp = d.get("photo_path")
    if not pp or not os.path.exists(pp): await cb.answer("Файл потерялся", show_alert=True); await state.clear(); return
    await cb.message.edit_reply_markup(reply_markup=None)
    loop = asyncio.get_event_loop()
    n = await loop.run_in_executor(None, calculate_auto_parts, pp)
    await cb.message.answer(f"🎲 {n} кусков."); await execute_splitting(cb.message, state, pp, n); await cb.answer()


@dp.callback_query(F.data == "send_original", BotStates.waiting_for_parts)
async def send_original(cb, state):
    d = await state.get_data(); pp = d.get("photo_path")
    if not pp or not os.path.exists(pp): await cb.answer("Нет", show_alert=True); await state.clear(); return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer_document(FSInputFile(pp, filename=os.path.basename(pp)), caption="📎 Оригинал.")
    await state.clear(); await cb.answer()


@dp.message(BotStates.waiting_for_parts)
async def process_parts(message, state):
    if not message.text or not message.text.isdigit(): await message.answer("❗ Цифру."); return
    n = int(message.text)
    if n < 3 or n % 3 != 0: await message.answer("⚠️ Кратно 3."); return
    d = await state.get_data(); await execute_splitting(message, state, d.get("photo_path"), n)


async def execute_splitting(msg_obj, state, pp, n):
    st = await msg_obj.answer("🔪 Режу...")
    try:
        loop = asyncio.get_event_loop()
        parts = await loop.run_in_executor(None, split_image, pp, n)
        def sk(p):
            m = re.search(r'part_(\d+)_(\d+)', p)
            return (int(m.group(1)), int(m.group(2))) if m else (999, 999)
        parts = sorted(parts, key=sk); total = len(parts)
        for i, pf in enumerate(parts):
            if not os.path.exists(pf): continue
            await msg_obj.answer_photo(photo=FSInputFile(pf, filename=f"{i+1:02d}.png"), caption=f"{i+1}/{total}")
            await asyncio.sleep(0.4)
        for pf in parts:
            if os.path.exists(pf):
                try: os.remove(pf)
                except Exception: pass
        await st.delete(); await msg_obj.answer(f"✅ Готово! {total}")
    except Exception as e: await msg_obj.answer(f"❌ {e}")
    finally:
        if os.path.exists(pp):
            try: os.remove(pp)
            except Exception: pass
        await state.clear()


# ============ ССЫЛКИ ============
@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def ask_type(message, state):
    url = message.text.strip()
    if "youtube.com" in url or "youtu.be" in url:
        await message.answer("⚠️ YouTube не работает."); return
    await state.update_data(download_url=url); await state.set_state(BotStates.waiting_for_media_type)
    b = InlineKeyboardBuilder()
    b.button(text="🎵 MP3", callback_data="get_audio"); b.button(text="🎬 MP4", callback_data="get_video")
    await message.answer("❓ Что вытащить?", reply_markup=b.as_markup())


@dp.callback_query(F.data.in_({"get_audio", "get_video"}), BotStates.waiting_for_media_type)
async def process_download(cb, state):
    d = await state.get_data(); url = d.get("download_url"); mode = cb.data
    await cb.message.edit_reply_markup(reply_markup=None)
    status = await cb.message.answer("⏳ Качаю..."); await cb.answer()
    loop = asyncio.get_event_loop()
    is_tk = "tiktok.com" in url or "vm.tiktok.com" in url or "vt.tiktok.com" in url
    fn = tp = None
    try:
        if is_tk:
            try: await status.edit_text("📥 TikTok...")
            except Exception: pass
            fn, title, dur, _, _ = await loop.run_in_executor(None, tiktok_via_api, url, mode)
        else:
            try: await status.edit_text("📥 Качаю...")
            except Exception: pass
            fn, title, dur, _, thumb_url = await loop.run_in_executor(None, other_site_download, url, mode)
            if thumb_url and mode == "get_audio":
                try:
                    tp = f"downloads/thumb_{abs(hash(url)) % 10**8}.jpg"
                    req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=15) as r: data = r.read()
                    raw = tp + ".raw"
                    with open(raw, "wb") as f: f.write(data)
                    Image.open(raw).convert("RGB").save(tp, "JPEG", quality=90); os.remove(raw)
                except Exception: tp = None
        if not fn or not os.path.exists(fn): raise FileNotFoundError("не скачалось")
        if mode == "get_audio":
            kw = {"audio": FSInputFile(fn, filename=f"{title}.mp3"), "title": title, "caption": "🎵 Готово."}
            if dur: kw["duration"] = int(dur)
            if tp and os.path.exists(tp): kw["thumbnail"] = FSInputFile(tp)
            await cb.message.answer_audio(**kw)
        else:
            await cb.message.answer_video(video=FSInputFile(fn, filename=f"{title}.mp4"),
                                          caption="🎬 Готово.", duration=int(dur) if dur else None)
        try: await status.delete()
        except Exception: pass
    except Exception as e:
        try: await status.edit_text(f"😔 {str(e)[:250]}")
        except Exception: pass
    finally:
        for p in (fn, tp):
            if p and os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        await state.clear()


# ============ МУТ ============
def _target_from_msg(message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    text = message.text or ""; m = re.search(r"@(\w+)", text)
    if m:
        un = m.group(1).lower()
        for uid, ud in DATA["users"].items():
            if (ud.get("username") or "").lower() == un:
                return type("U", (), {"id": int(uid), "username": un, "full_name": f"@{un}"})()
    return None


@dp.message(F.text.func(lambda t: t and t.strip().lower() == ".mute"))
async def cmd_mute(message):
    await _try_delete_command(message)
    if not is_owner(message.from_user): await message.answer("Только владелец."); return
    target = _target_from_msg(message)
    if not target: await message.answer("Кого? <code>.mute</code> реплаем.", parse_mode="HTML"); return
    if target.id == message.from_user.id: await message.answer("Себя?"); return
    if is_owner(target): await message.answer("Владельца нельзя."); return
    MUTED.setdefault(message.chat.id, set()).add(target.id)
    name = target.full_name or (f"@{target.username}" if target.username else str(target.id))
    await message.answer(f"🔇 <b>МОЛЧАТЬ!!!</b> {html_mod.escape(name)}", parse_mode="HTML")


@dp.message(F.text.func(lambda t: t and t.strip().lower() == ".unmute"))
async def cmd_unmute(message):
    await _try_delete_command(message)
    if not is_owner(message.from_user): await message.answer("Только владелец."); return
    target = _target_from_msg(message)
    if not target: await message.answer("Реплаем."); return
    ms = MUTED.get(message.chat.id, set())
    if target.id in ms:
        ms.discard(target.id); name = target.full_name or str(target.id)
        await message.answer(f"Так и быть, говори, {html_mod.escape(name)}.")
    else: await message.answer("Он не в муте.")


# ============ БИЗНЕС ============
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    try:
        if connection.is_enabled:
            BUSINESS_CONNECTIONS[connection.id] = {"user_id": connection.user.id, "is_enabled": True}
            DATA.setdefault("business_owners", {})[connection.id] = connection.user.id
            if connection.id in DATA.get("dead_bc", []):
                DATA["dead_bc"] = [x for x in DATA["dead_bc"] if x != connection.id]
            save_data(DATA)
            print(f"Business подключён: {connection.id} -> user {connection.user.id}")
            try:
                await bot.send_message(connection.user.id,
                    "✅ Бот подключён.\n\n<b>Команды в бизнес-чате:</b>\n"
                    "• <code>.ttt</code> — крестики-нолики\n"
                    "• <code>.mute</code> / <code>.unmute</code>\n"
                    "• <code>.switch</code> — раскладка (реплай)\n"
                    "• <code>.sum</code> — пересказ переписки\n"
                    "• <code>.tts текст</code> — озвучка\n"
                    "• <code>.ruletka</code> — рулетка\n"
                    "• <code>.revoke</code> — отозвать\n"
                    "• <code>/uno</code> — Уно\n\n"
                    "Голосовые расшифровываются автоматически.", parse_mode="HTML")
            except Exception as e: print(f"business notify: {str(e)[:150]}")
        else:
            BUSINESS_CONNECTIONS.pop(connection.id, None)
            DATA.get("business_owners", {}).pop(connection.id, None)
            save_data(DATA)
            for cid, g in list(TTT_GAMES.items()):
                if g.get("bc_id") == connection.id: TTT_GAMES.pop(cid, None)
    except Exception as e: print(f"business_conn: {str(e)[:150]}")


@dp.business_message()
async def bc_main(message: Message):
    try:
        chat_id = message.chat.id; user = message.from_user; bc_id = message.business_connection_id
        if not user or not bc_id or _is_dead_bc(bc_id): return
        DATA.setdefault("business_chats", {})[str(chat_id)] = bc_id
        owner_id = BUSINESS_CONNECTIONS.get(bc_id, {}).get("user_id") or DATA.get("business_owners", {}).get(bc_id)
        is_out = (owner_id is not None and user.id == owner_id)
        text = message.text or message.caption or ""
        if text.startswith(".") or text.startswith("/"): text = ""
        if text:
            hist = DATA.setdefault("history", {}).setdefault(str(chat_id), [])
            hist.append({"from_id": user.id, "from_name": user.full_name or "",
                         "text": text[:400], "ts": time.time(), "is_out": is_out})
            if len(hist) > HISTORY_LIMIT: DATA["history"][str(chat_id)] = hist[-HISTORY_LIMIT:]
            if len(hist) % 5 == 0: save_data(DATA)
        if chat_id in MUTED and user.id in MUTED[chat_id]:
            if is_owner(user): return
            try: await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
            except Exception as e:
                if _is_peer_invalid(e): await _notify_owner_bc_dead(bc_id)
                return
            try: await bot.send_message(chat_id, "🔇 МОЛЧАТЬ!!!", business_connection_id=bc_id)
            except Exception as e:
                if _is_peer_invalid(e): await _notify_owner_bc_dead(bc_id)
    except Exception as e: print(f"bc_main: {str(e)[:150]}")


@dp.business_message(F.voice)
async def bc_voice(message):
    bc = message.business_connection_id
    if not bc or _is_dead_bc(bc): return
    await _handle_stt_bc(message, message.voice.file_id, ".ogg", bc)


@dp.business_message(F.video_note)
async def bc_vn(message):
    bc = message.business_connection_id
    if not bc or _is_dead_bc(bc): return
    await _handle_stt_bc(message, message.video_note.file_id, ".mp4", bc)


@dp.business_message(F.text.func(lambda t: t and t.strip().lower() == ".ttt"))
async def bc_ttt(message):
    try:
        chat_id = message.chat.id; bc = message.business_connection_id
        await _try_delete_command(message, bc)
        if _is_dead_bc(bc): return
        DATA.setdefault("business_chats", {})[str(chat_id)] = bc
        if chat_id in TTT_GAMES and not TTT_GAMES[chat_id].get("finished"):
            try: await bot.send_message(chat_id, "⚠️ Игра уже идёт.", business_connection_id=bc)
            except Exception as e:
                if _is_peer_invalid(e): await _notify_owner_bc_dead(bc)
            return
        u = message.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
        TTT_GAMES[chat_id] = {"board": [None]*9, "x_id": u.id, "o_id": None, "turn": "x",
                               "names": {"x": name, "o": None}, "finished": False, "footer": "",
                               "bc_id": bc, "rematch_votes": []}
        await bot.send_message(chat_id, ttt_board_text(TTT_GAMES[chat_id]),
                                reply_markup=ttt_keyboard(TTT_GAMES[chat_id]),
                                parse_mode="HTML", business_connection_id=bc)
    except Exception as e:
        print(f"bc_ttt: {str(e)[:150]}")
        if _is_peer_invalid(e): await _notify_owner_bc_dead(message.business_connection_id)


@dp.business_message(F.text.func(lambda t: t and t.strip().lower() == ".mute"))
async def bc_mute(message):
    try:
        bc = message.business_connection_id
        await _try_delete_command(message, bc)
        if _is_dead_bc(bc): return
        DATA.setdefault("business_chats", {})[str(message.chat.id)] = bc
        if not is_owner(message.from_user):
            try: await bot.send_message(message.chat.id, "Только владелец.", business_connection_id=bc)
            except Exception as e:
                if _is_peer_invalid(e): await _notify_owner_bc_dead(bc)
            return
        tgt = _target_from_msg(message)
        if not tgt:
            try: await bot.send_message(message.chat.id, "Реплаем <code>.mute</code>", parse_mode="HTML", business_connection_id=bc)
            except Exception: pass
            return
        MUTED.setdefault(message.chat.id, set()).add(tgt.id)
        name = tgt.full_name or str(tgt.id)
        try: await bot.send_message(message.chat.id, f"🔇 <b>МОЛЧАТЬ!!!</b> {html_mod.escape(name)}",
                                     parse_mode="HTML", business_connection_id=bc)
        except Exception as e:
            if _is_peer_invalid(e): await _notify_owner_bc_dead(bc)
    except Exception as e: print(f"bc_mute: {str(e)[:150]}")


@dp.business_message(F.text.func(lambda t: t and t.strip().lower() == ".unmute"))
async def bc_unmute(message):
    try:
        bc = message.business_connection_id
        await _try_delete_command(message, bc)
        if _is_dead_bc(bc): return
        DATA.setdefault("business_chats", {})[str(message.chat.id)] = bc
        if not is_owner(message.from_user): return
        tgt = _target_from_msg(message)
        if not tgt: return
        ms = MUTED.get(message.chat.id, set())
        if tgt.id in ms:
            ms.discard(tgt.id)
            name = tgt.full_name or str(tgt.id)
            try: await bot.send_message(message.chat.id, f"Так и быть, говори, {html_mod.escape(name)}.", business_connection_id=bc)
            except Exception as e:
                if _is_peer_invalid(e): await _notify_owner_bc_dead(bc)
        else:
            try: await bot.send_message(message.chat.id, "Не в муте.", business_connection_id=bc)
            except Exception: pass
    except Exception as e: print(f"bc_unmute: {str(e)[:150]}")


@dp.business_message(F.text.func(lambda t: t and t.strip().lower() == ".switch"))
async def bc_switch(message):
    try:
        bc = message.business_connection_id
        await _try_delete_command(message, bc)
        if _is_dead_bc(bc): return
        if not message.reply_to_message:
            try: await bot.send_message(message.chat.id, "⚠️ Ответь реплаем.", business_connection_id=bc)
            except Exception: pass
            return
        src = message.reply_to_message.text or message.reply_to_message.caption or ""
        if not src.strip(): return
        conv, dr = _convert_layout(src)
        if not dr or conv == src: return
        try:
            await bot.send_message(message.chat.id, f"🔁 <b>Исправлено:</b>\n\n{html_mod.escape(conv)}",
                                    parse_mode="HTML", reply_to_message_id=message.reply_to_message.message_id,
                                    business_connection_id=bc)
        except Exception as e:
            if _is_peer_invalid(e): await _notify_owner_bc_dead(bc)
    except Exception as e: print(f"bc_switch: {str(e)[:150]}")


@dp.business_message(F.text.func(lambda t: t and t.strip().lower() == ".sum"))
async def bc_sum(message):
    try:
        bc = message.business_connection_id
        await _try_delete_command(message, bc)
        if _is_dead_bc(bc): return
        chat_id = message.chat.id
        DATA.setdefault("business_chats", {})[str(chat_id)] = bc
        hist = DATA.get("history", {}).get(str(chat_id), [])
        if not hist:
            try: await bot.send_message(chat_id, "📝 Пока нечего пересказывать.", business_connection_id=bc)
            except Exception: pass
            return
        tgt_id = None
        if message.reply_to_message and message.reply_to_message.from_user:
            tgt_id = message.reply_to_message.from_user.id
        owner_id = BUSINESS_CONNECTIONS.get(bc, {}).get("user_id") or DATA.get("business_owners", {}).get(bc)
        filt = [m for m in hist if not m.get("is_out") and (tgt_id is None or m.get("from_id") == tgt_id)]
        if not filt: filt = hist[-30:]
        filt = filt[-30:]
        summary = _summarize_messages(filt)
        header = ""
        if tgt_id:
            n = next((m.get("from_name") for m in filt if m.get("from_id") == tgt_id), "")
            if n: header = f"💬 <b>{html_mod.escape(n)}:</b>\n\n"
        try:
            await bot.send_message(chat_id, header + summary, parse_mode="HTML", business_connection_id=bc)
        except Exception as e:
            if _is_peer_invalid(e): await _notify_owner_bc_dead(bc)
        save_data(DATA)
    except Exception as e: print(f"bc_sum: {str(e)[:150]}")


@dp.business_message(F.text.func(lambda t: t and t.strip().lower().startswith(".tts")))
async def bc_tts(message):
    try:
        bc = message.business_connection_id
        text = ""
        if message.reply_to_message: text = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
        if not text:
            parts = (message.text or "").split(maxsplit=1)
            if len(parts) > 1: text = parts[1].strip()
        await _try_delete_command(message, bc)
        if _is_dead_bc(bc): return
        DATA.setdefault("business_chats", {})[str(message.chat.id)] = bc
        if not HAS_GTTS or not text: return
        if len(text) > 500: text = text[:500]
        out = f"downloads/tts_{int(time.time())}.mp3"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _tts_generate, text, out)
            await bot.send_voice(chat_id=message.chat.id, voice=FSInputFile(out, filename="v.mp3"),
                                  caption="🔊", business_connection_id=bc)
        except Exception as e:
            print(f"bc tts: {str(e)[:150]}")
        finally:
            if os.path.exists(out):
                try: os.remove(out)
                except Exception: pass
    except Exception as e: print(f"bc_tts: {str(e)[:150]}")


@dp.business_message(F.text.func(lambda t: t and t.strip().lower() in (".ruletka", ".rl")))
async def bc_ruletka(message):
    try:
        bc = message.business_connection_id
        await _try_delete_command(message, bc)
        if _is_dead_bc(bc): return
        win = random.random() < 5/6
        try:
            await bot.send_message(message.chat.id, random.choice(RULETKA_WIN if win else RULETKA_LOSE),
                                    business_connection_id=bc)
        except Exception as e:
            if _is_peer_invalid(e): await _notify_owner_bc_dead(bc)
    except Exception as e: print(f"bc_ruletka: {str(e)[:150]}")


@dp.business_message(F.text.func(lambda t: t and t.strip().lower() == ".revoke"))
async def bc_revoke(message):
    try:
        bc = message.business_connection_id
        await _try_delete_command(message, bc)
        if _is_dead_bc(bc): return
        if not is_owner(message.from_user): return
        parts = (message.text or "").split(maxsplit=1)
        arg = parts[1] if len(parts) >= 2 else None
        # упрощённо: сначала реплай
        if message.reply_to_message and message.reply_to_message.from_user:
            tgt = message.reply_to_message.from_user
            if is_owner(tgt): return
            info = _revoke_by_user_id(tgt.id)
            if info:
                save_data(DATA)
                try: await bot.send_message(message.chat.id, "✅ Отозван.", business_connection_id=bc)
                except Exception: pass
            return
        if arg:
            info = _revoke_by_keycode(arg)
            if info:
                save_data(DATA)
                try: await bot.send_message(message.chat.id, f"✅ Ключ {arg} удалён.", business_connection_id=bc)
                except Exception: pass
                return
    except Exception as e: print(f"bc_revoke: {str(e)[:150]}")


# ============ ЗАПУСК ============
async def keep_alive():
    while True:
        try:
            await asyncio.sleep(240)
            me = await bot.get_me()
            print(f"keep-alive: @{me.username}")
        except Exception as e: print(f"keep-alive: {str(e)[:120]}")


async def set_bot_commands():
    cmds = [
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="code", description="Активировать ключ"),
        BotCommand(command="mykey", description="Мой ключ"),
        BotCommand(command="stickers", description="Стикерпаки"),
        BotCommand(command="whisper", description="Как отправить шёпот"),
        BotCommand(command="uno", description="Уно (в ЛС — с ботом, в группе — с людьми)"),
        BotCommand(command="play", description="Старт Уно в группе"),
        BotCommand(command="mafia", description="Мафия"),
        BotCommand(command="cancel", description="Отмена"),
    ]
    try: await bot.set_my_commands(cmds)
    except Exception as e: print(f"cmds: {e}")


async def set_bot_menu():
    try: await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e: print(f"menu: {e}")


dp.message.middleware(AccessMiddleware())


async def main():
    global DATA
    DATA = await load_data_from_tg()
    cleanup_expired_whispers()
    print(f"keys: {len(DATA['keys'])}, users: {len(DATA['users'])}")
    await set_bot_commands()
    await set_bot_menu()
    asyncio.create_task(keep_alive())
    print("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): print("stopped")
