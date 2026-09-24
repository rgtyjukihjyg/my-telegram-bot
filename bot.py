import os, re, json, time, html as html_mod, asyncio, secrets, random, shutil
import urllib.request, urllib.parse, subprocess
from aiohttp import web
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
    print("[tts] gTTS не установлен")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN: raise ValueError("нет BOT_TOKEN")
STORAGE_CHAT_ID = os.environ.get("STORAGE_CHAT_ID")
OWNER_ID = 7752398574
OWNER_USERNAME = (os.environ.get("OWNER_USERNAME") or "vimbrix").lower().lstrip("@")
_eid = os.environ.get("OWNER_ID")
if _eid and _eid.strip().isdigit(): OWNER_ID = int(_eid)
DATA_FILE = "data.json"
KEY_LENGTH = 12
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
STT_SIZE = os.environ.get("STT_SIZE", "small")
PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
WHISPER_TTL = 86400
HISTORY_LIMIT = 60
UNO_LOBBY_TIMEOUT = 60
UNO_LOBBY_TICK = 10
BTN_CREATE_KEY = "🔑 Создать ключ"
BTN_KEYS_STATUS = "📋 Статусы ключей"

_skw = {"timeout": 120}
if PROXY: _skw["proxy"] = PROXY
session = AiohttpSession(**_skw)
bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

BOT_USERNAME_CACHE = None
STT_MODEL = None
TTT_GAMES = {}
UNO_GAMES = {}
MUTED = {}
BUSINESS_CONNECTIONS = {}


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


os.makedirs("downloads", exist_ok=True)
os.makedirs("temp_photos", exist_ok=True)

FUNNY_REPLIES = ["Ержан, фу, нельзя, место!", "Дорогая, не лезь, оно тебя сожрёт",
    "Тебя в детстве не учили не нажимать куда попало?", "Это не твоё. Отойди.",
    "Руки убрал!", "Не для тебя писали.", "Кыш.", "А тебе кто разрешил?"]
RULETKA_WIN = ["🍀 Повезло, повезло… не делай так больше.",
    "😅 Щёлк — и пусто. Пронесло.", "🎯 Ты выжил.",
    "💨 Курок щёлкнул вхолостую."]
RULETKA_LOSE = ["💀 Упс… ты умер. Не повезло.",
    "☠️ Бах! И всё.", "🪦 Проиграл.", "😵 Пуля нашла тебя."]

EN2RU = {'q':'й','w':'ц','e':'у','r':'к','t':'е','y':'н','u':'г','i':'ш','o':'щ','p':'з',
    '[':'х',']':'ъ','a':'ф','s':'ы','d':'в','f':'а','g':'п','h':'р','j':'о','k':'л',
    'l':'д',';':'ж',"'":'э','z':'я','x':'ч','c':'с','v':'м','b':'и','n':'т','m':'ь',
    ',':'б','.':'ю','/':'.','`':'ё'}
RU2EN = {v: k for k, v in EN2RU.items()}

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
    "\U0001F900-\U0001F9FF\U00002700-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U0001FA00-\U0001FAFF]+", flags=re.UNICODE)
DASH_RE = re.compile(r"\s*[-\u2013\u2014\u2212\u2015]\s*")
DOT_SPACES_RE = re.compile(r"\s+")


def _normalize_dot(t):
    if not t: return ""
    return DOT_SPACES_RE.sub("", t.strip().lower())


def dot_starts(*cmds):
    def check(t):
        if not t: return False
        s = _normalize_dot(t)
        return any(s.startswith(c) for c in cmds)
    return check


def _empty_data():
    return {"keys": {}, "users": {}, "sticker_packs": {}, "whispers": {},
            "whisper_counter": 1, "business_owners": {}, "dead_bc": [],
            "business_chats": {}, "history": {}, "user_settings": {}}


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
            except Exception as e: print(f"load {path}: {e}")
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
    except Exception as e: print(f"save: {e}")


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
            print(f"load OK: keys={len(d['keys'])}, users={len(d['users'])}")
            save_data_to_file(d)
            return d
        except Exception as e:
            print(f"load tg {attempt+1}: {str(e)[:120]}")
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
            async with s.post(url, data={"chat_id": STORAGE_CHAT_ID, "message_id": msg_id,
                                          "disable_notification": True}) as r: await r.json()
    except Exception as e: print(f"save tg: {str(e)[:150]}")


DATA = _empty_data()


def save_data(data=None):
    d = data if data is not None else DATA
    save_data_to_file(d)
    if STORAGE_CHAT_ID:
        try: asyncio.create_task(save_data_to_tg(d))
        except RuntimeError: pass


def is_owner(user):
    if not user: return False
    if OWNER_ID is not None and user.id == OWNER_ID: return True
    return (user.username or "").lower() == OWNER_USERNAME


async def get_bot_username():
    global BOT_USERNAME_CACHE
    if BOT_USERNAME_CACHE is None:
        me = await bot.get_me(); BOT_USERNAME_CACHE = me.username
    return BOT_USERNAME_CACHE


def parse_duration(s):
    s = (s or "").lower().strip(); total = 0; matched = False
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


def _is_peer_invalid(err): return "BUSINESS_PEER_INVALID" in str(err)
def _is_dead_bc(bc_id): return bc_id in DATA.get("dead_bc", [])


async def _try_delete_command(message, bc_id=None):
    try:
        if bc_id:
            await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
        else: await message.delete()
    except Exception: pass


def _convert_layout(text):
    lat = sum(1 for c in text if c.isascii() and c.isalpha())
    cyr = sum(1 for c in text if 'а' <= c.lower() <= 'я' or c.lower() == 'ё')
    if lat == 0 and cyr == 0: return text, None
    if lat >= cyr: return "".join(EN2RU.get(c, c) for c in text), "en2ru"
    return "".join(RU2EN.get(c, c) for c in text), "ru2en"


def clean_meta(s, max_len=64, strip_author=""):
    if not s: return ""
    s = str(s).strip()
    s = re.sub(r"https?://\S+", " ", s); s = re.sub(r"#\S+", " ", s); s = re.sub(r"@\S+", " ", s)
    s = re.sub(r"\btiktok\b", " ", s, flags=re.IGNORECASE); s = EMOJI_RE.sub(" ", s)
    s = " ".join(s.split())
    return " ".join(s.strip(" -|.,:;").split())[:max_len].strip()


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
    if data.get("code") != 0: raise RuntimeError("TikTok err")
    d = data["data"]; vid_id = d.get("id") or "tiktok"
    title = clean_meta(d.get("title") or "tiktok")
    duration = d.get("duration")
    if mode == "audio":
        mu = d.get("music")
        if not mu: raise RuntimeError("нет аудио")
        tmp = f"downloads/{vid_id}_raw"; _download_direct(mu, tmp)
        out = f"downloads/{vid_id}.mp3"
        r = subprocess.run([FFMPEG_EXE_PATH, "-y", "-i", tmp, "-vn", "-c:a", "libmp3lame",
                            "-b:a", "192k", out], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
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
                     "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                                          "preferredquality": "192"}]})
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


FFMPEG_EXE_PATH, FFPROBE_EXE_PATH = static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
FFMPEG_DIR = os.path.dirname(FFMPEG_EXE_PATH)
print(f"ffmpeg: {FFMPEG_EXE_PATH}")
for folder in ("downloads", "temp_photos"):
    if os.path.exists(folder):
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            try:
                if os.path.isfile(p): os.remove(p)
            except Exception: pass


def get_stt_model():
    global STT_MODEL
    if STT_MODEL is None:
        print(f"STT ({STT_SIZE})...")
        from faster_whisper import WhisperModel
        STT_MODEL = WhisperModel(STT_SIZE, device="cpu", compute_type="int8")
    return STT_MODEL


def convert_to_wav(src, dst):
    cmd = [FFMPEG_EXE_PATH, "-y", "-i", src, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0: raise RuntimeError("wav err")
    return dst


def transcribe_wav(wav):
    model = get_stt_model()
    segs, info = model.transcribe(wav, language="ru", beam_size=5, vad_filter=True,
        condition_on_previous_text=False, temperature=0.0,
        initial_prompt="Голосовое сообщение на русском.")
    return " ".join(s.text.strip() for s in segs).strip(), info.language


def _stt_pipeline(src, wav):
    convert_to_wav(src, wav); return transcribe_wav(wav)


async def _handle_stt(message, file_id, ext_hint=".ogg"):
    fi = await bot.get_file(file_id)
    src, wav = f"downloads/stt_{file_id}{ext_hint}", f"downloads/stt_{file_id}.wav"
    await bot.download_file(fi.file_path, src)
    status = await message.answer("📝 Слушаю...")
    try:
        loop = asyncio.get_event_loop()
        text, _ = await loop.run_in_executor(None, _stt_pipeline, src, wav)
        preview = (text[:3900] if len(text) <= 3900 else text[:3900] + "...") or "🤷 Не разобрал."
        await status.edit_text(f"📝 Расшифровка:\n\n{preview}")
    except Exception as e:
        try: await status.edit_text(f"😔 {str(e)[:250]}")
        except Exception: pass
    finally:
        for p in (src, wav):
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass


def _tts_generate(text, out):
    gTTS(text=text, lang="ru").save(out); return out


ALWAYS_FREE = {"/start", "/help", "/code", "/mykey", "/whoami", "/whisper", "/cancel", "/uno", "/play"}


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, Message): return await handler(event, data)
        user = event.from_user
        if user is None: return await handler(event, data)
        chat_id = event.chat.id if event.chat else None
        if chat_id and chat_id in MUTED and user.id in MUTED[chat_id]:
            if not is_owner(user):
                try: await event.delete()
                except Exception: pass
                try: await bot.send_message(chat_id, "🔇 МОЛЧАТЬ!!!")
                except Exception: pass
                return
        text = (getattr(event, "text", None) or "").strip()
        if not text:
            return await handler(event, data)
        norm = _normalize_dot(text)
        if norm.startswith("."):
            return await handler(event, data)
        cmd = text.split()[0].lower() if text else ""
        if cmd in ALWAYS_FREE: return await handler(event, data)
        if is_owner(user): return await handler(event, data)
        status = user_status(user.id)
        if status == "valid": return await handler(event, data)
        if status == "expired":
            DATA["users"].pop(str(user.id), None); save_data(DATA)
            try: await event.answer("⏰ Ключ истёк. /code")
            except Exception: pass
            return
        try: await event.answer("🔒 Доступ только по ключу.\n\n/code ТВОЙ_КЛЮЧ")
        except Exception: pass


async def try_activate_key(event, user, key):
    kd = DATA["keys"].get(key); uid = str(user.id)
    if not kd: await event.answer("❌ Нет такого ключа."); return
    if kd.get("used_by") is not None:
        if str(kd["used_by"]) == uid:
            u = DATA["users"].get(uid, {})
            if u.get("permanent"): await event.answer("😉 Уже активирован.")
            else:
                exp = u.get("expires_at", 0); rem = max(0, exp - time.time())
                await event.answer(f"😉 Уже активирован.\n⏳ {format_duration(rem)}", parse_mode="HTML")
        else: await event.answer("⚠️ Ключ занят.")
        return
    now = time.time(); perm = bool(kd.get("permanent")); dur = int(kd.get("duration", 0))
    kd["used_by"] = user.id; kd["activated_at"] = now
    entry = {"username": user.username or "", "key": key, "permanent": perm, "activated_at": now}
    if not perm: entry["expires_at"] = now + dur
    DATA["users"][uid] = entry; save_data(DATA)
    if perm: await event.answer("✅ <b>Ключ активирован!</b>\n♾ навсегда", parse_mode="HTML")
    else: await event.answer(f"✅ <b>Ключ активирован!</b>\n⏱ {format_duration(dur)}", parse_mode="HTML")


def owner_reply_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CREATE_KEY),
                                          KeyboardButton(text=BTN_KEYS_STATUS)]],
                               resize_keyboard=True, is_persistent=True)


def _generate_key():
    for _ in range(200):
        k = "".join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_LENGTH))
        if k not in DATA["keys"]: return k
    raise RuntimeError("key fail")


def _key_status_text(kd):
    if kd.get("used_by"):
        u = DATA["users"].get(str(kd["used_by"]), {})
        uname = html_mod.escape(u.get("username") or f"id{kd['used_by']}")
        if u.get("permanent"): return f"♾ @{uname}"
        exp = u.get("expires_at", 0)
        if exp > time.time(): return f"⏳ @{uname} — {format_duration(max(0, exp - time.time()))}"
        return f"❌ @{uname} — истёк"
    if kd.get("permanent"): return "♾ свободен"
    return f"⏳ {format_duration(kd.get('duration', 0))}, свободен"


def _render_keys_list():
    keys = DATA["keys"]
    if not keys: return "🗂 Нет ключей.", None
    lines = [f"🗂 <b>Ключи</b> (всего: {len(keys)})\n"]
    b = InlineKeyboardBuilder()
    for k, kd in keys.items():
        lines.append(f"<code>{html_mod.escape(k)}</code> — {_key_status_text(kd)}")
        b.button(text=f"🗑 {k}", callback_data=f"kdel:{k}")
    b.adjust(2)
    return "\n".join(lines), b.as_markup()


async def _send_keys_list(message):
    try:
        text, kb = _render_keys_list()
        if kb is None: await message.answer(text, parse_mode="HTML")
        else: await message.answer(text, reply_markup=kb, parse_mode="HTML")
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
    key = u.get("key")
    if key and key in DATA["keys"]: del DATA["keys"][key]
    return {"uid": uid, "username": u.get("username") or "", "key": key}


def _revoke_by_keycode(code):
    code = code.strip().upper(); kd = DATA["keys"].get(code)
    if not kd: return None
    ub = kd.get("used_by"); del DATA["keys"][code]
    if ub and str(ub) in DATA["users"]: del DATA["users"][str(ub)]
    return {"key": code, "used_by": ub}


@dp.message(lambda m: m.text is not None and BTN_CREATE_KEY in m.text)
async def btn_create_key(message):
    if not is_owner(message.from_user): return
    await _show_create_key_menu(message)


@dp.message(lambda m: m.text is not None and BTN_KEYS_STATUS in m.text)
async def btn_keys_status(message):
    if not is_owner(message.from_user): return
    await _send_keys_list(message)


@dp.message(F.text == "/newkey")
async def cmd_newkey(message):
    if not is_owner(message.from_user): return
    await _show_create_key_menu(message)


@dp.callback_query(F.data == "kc:perm")
async def cb_kc_perm(cb):
    if not is_owner(cb.from_user): await cb.answer("Не твоя", show_alert=True); return
    key = _generate_key()
    DATA["keys"][key] = {"permanent": True, "duration": 0, "created_at": time.time(), "used_by": None}
    save_data(DATA)
    await cb.message.edit_text(f"♾ <b>Ключ:</b> <code>{key}</code>", parse_mode="HTML")
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
        await message.answer(f"⏳ <b>Ключ:</b> <code>{key}</code>\n⏱ {format_duration(dur)}", parse_mode="HTML")
    finally: await state.clear()


@dp.callback_query(F.data.startswith("kdel:"))
async def cb_key_delete(cb):
    if not is_owner(cb.from_user): await cb.answer("Не твоя", show_alert=True); return
    code = cb.data.split(":", 1)[1].strip().upper()
    _revoke_by_keycode(code); save_data(DATA)
    try:
        text, kb = _render_keys_list()
        if kb is None: await cb.message.edit_text(text, parse_mode="HTML")
        else: await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception: pass
    await cb.answer("Удалён")


async def _do_revoke(message, arg=None):
    if not is_owner(message.from_user): return
    if message.reply_to_message and message.reply_to_message.from_user:
        tgt = message.reply_to_message.from_user
        if is_owner(tgt): return
        info = _revoke_by_user_id(tgt.id)
        if info: save_data(DATA); await message.answer("✅ Отозван.")
        return
    if not arg: return
    arg = arg.strip()
    info = _revoke_by_keycode(arg)
    if info: save_data(DATA); await message.answer("✅ Ключ удалён."); return
    if arg.isdigit():
        info = _revoke_by_user_id(arg)
        if info: save_data(DATA); await message.answer("✅ Отозван."); return


@dp.message(F.text.func(dot_starts(".ruletka", ".rl")))
async def cmd_ruletka(message):
    await _try_delete_command(message)
    win = random.random() < 5/6
    await message.answer(random.choice(RULETKA_WIN if win else RULETKA_LOSE))


@dp.message(F.text.func(dot_starts("рулетка")))
async def cmd_ruletka2(message):
    await _try_delete_command(message)
    win = random.random() < 5/6
    await message.answer(random.choice(RULETKA_WIN if win else RULETKA_LOSE))


@dp.message(F.voice)
async def process_voice(message, state):
    await state.set_state(None); await _handle_stt(message, message.voice.file_id, ".ogg")


@dp.message(F.video_note)
async def process_video_note(message, state):
    await state.set_state(None); await _handle_stt(message, message.video_note.file_id, ".mp4")


@dp.message(F.text.func(dot_starts(".switch")))
async def cmd_switch(message):
    if message.chat.type in ("group", "supergroup", "channel"): return
    await _try_delete_command(message)
    if not message.reply_to_message:
        await message.answer("⚠️ Ответь <code>.switch</code> на сообщение.", parse_mode="HTML"); return
    src = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not src.strip(): await message.answer("⚠️ Нет текста."); return
    conv, dr = _convert_layout(src)
    if not dr or conv == src: await message.answer("🤷 Нечего менять."); return
    await message.answer(f"🔁 <b>Исправлено:</b>\n\n{html_mod.escape(conv)}", parse_mode="HTML",
                          reply_to_message_id=message.reply_to_message.message_id)


@dp.message(F.text.func(dot_starts(".tts")))
async def cmd_tts(message):
    if message.chat.type in ("group", "supergroup", "channel"): return
    await _try_delete_command(message)
    if not HAS_GTTS: await message.answer("❌ gTTS не установлен."); return
    text = ""
    if message.reply_to_message:
        text = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
    else:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1: text = parts[1].strip()
    if not text: await message.answer("⚠️ <code>.tts текст</code>", parse_mode="HTML"); return
    if len(text) > 500: text = text[:500]
    status = await message.answer("🔊 Генерирую...")
    out = f"downloads/tts_{int(time.time())}.mp3"
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _tts_generate, text, out)
        await message.answer_voice(voice=FSInputFile(out, filename="voice.mp3"), caption="🔊")
        try: await status.delete()
        except Exception: pass
    except Exception as e:
        try: await status.edit_text(f"❌ {str(e)[:200]}")
        except Exception: pass
    finally:
        if os.path.exists(out):
            try: os.remove(out)
            except Exception: pass


def _target_from_msg(message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    return None


@dp.message(F.text.func(dot_starts(".unmute")))
async def cmd_unmute(message):
    await _try_delete_command(message)
    if not is_owner(message.from_user): return
    target = _target_from_msg(message)
    if not target: return
    ms = MUTED.get(message.chat.id, set())
    if target.id in ms:
        ms.discard(target.id)
        await message.answer("Так и быть, говори.")


@dp.message(F.text.func(dot_starts(".mute")))
async def cmd_mute(message):
    await _try_delete_command(message)
    if not is_owner(message.from_user): return
    target = _target_from_msg(message)
    if not target:
        await message.answer("Реплаем <code>.mute</code>", parse_mode="HTML"); return
    if target.id == message.from_user.id or is_owner(target): return
    MUTED.setdefault(message.chat.id, set()).add(target.id)
    name = target.full_name or str(target.id)
    await message.answer(f"🔇 <b>МОЛЧАТЬ!!!</b> {html_mod.escape(name)}", parse_mode="HTML")


HELP_TEXT = ("📖 <b>Что умею:</b>\n\n"
    "🎲 <code>.ruletka</code> — рулетка\n"
    "❌⭕ <code>.ttt</code> — крестики-нолики\n"
    "🎴 <code>.uno</code> — Уно в ЛС (с ботом)\n"
    "🎴 <code>/uno</code> — Уно в группе\n"
    "🔁 <code>.switch</code> — раскладка (ЛС)\n"
    "🔊 <code>.tts текст</code> — озвучка (ЛС)\n"
    "🎤 голосовое — расшифровка\n"
    "📷 фото — на куски (ЛС)\n"
    "🎬 ссылка — видео/MP3 (ЛС)\n"
    "🎨 <code>/stickers</code> — стикерпаки\n"
    "🔑 <code>/mykey</code> — ключ\n")


@dp.message(F.text.func(lambda t: _normalize_dot(t) in ("/help", ".help")))
async def cmd_help(message): await message.answer(HELP_TEXT, parse_mode="HTML")


@dp.message(F.text == "/start")
async def cmd_start(message):
    if is_owner(message.from_user):
        await message.answer(HELP_TEXT + "\n👑 Админ-панель.", reply_markup=owner_reply_kb(), parse_mode="HTML")
    else:
        await message.answer(HELP_TEXT + "\n🔑 /code ТВОЙ_КЛЮЧ", parse_mode="HTML")


@dp.message(F.text == "/whoami")
async def cmd_whoami(message):
    u = message.from_user
    await message.answer(f"ID: {u.id}\nТы владелец? {'ДА' if is_owner(u) else 'НЕТ'}")


@dp.message(F.text.startswith("/code"))
async def cmd_code(message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2: await message.answer("⚠️ /code КЛЮЧ"); return
    await try_activate_key(message, message.from_user, parts[1].strip().strip("`").strip().upper())


@dp.message(F.text == "/mykey")
async def cmd_mykey(message):
    u = DATA["users"].get(str(message.from_user.id))
    if not u: await message.answer("🤷 Нет ключа."); return
    if u.get("permanent"): await message.answer(f"🔑 <code>{html_mod.escape(u.get('key'))}</code>\n♾ навсегда", parse_mode="HTML")
    else:
        exp = u.get("expires_at", 0); rem = max(0, exp - time.time())
        await message.answer(f"🔑 <code>{html_mod.escape(u.get('key'))}</code>\n⏳ {format_duration(rem)}", parse_mode="HTML")


@dp.message(F.text == "/cancel")
async def cmd_cancel(message, state):
    await state.clear()
    UNO_GAMES.pop(message.chat.id, None)
    TTT_GAMES.pop(message.chat.id, None)
    await message.answer("👌")


@dp.message(F.text == "/whisper")
async def cmd_whisper(message):
    uname = await get_bot_username()
    await message.answer(f"🤫 <code>@{uname} текст @username</code>", parse_mode="HTML")


@dp.message(F.document & F.document.mime_type.startswith("image/"))
async def process_photo_document(message, state):
    if message.chat.type in ("group", "supergroup", "channel"): return
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
    if message.chat.type in ("group", "supergroup", "channel"): return
    p = message.photo[-1]; fi = await bot.get_file(p.file_id)
    lp = f"temp_photos/{p.file_id}.jpg"
    await bot.download_file(fi.file_path, lp)
    await state.update_data(photo_path=lp); await state.set_state(BotStates.waiting_for_parts)
    b = InlineKeyboardBuilder()
    b.button(text="🎲 Авто", callback_data="auto_split"); b.button(text="📎 Оригинал", callback_data="send_original")
    await message.answer("⚠️ Пришли <b>как файл</b>.\n✂️ На сколько?", reply_markup=b.as_markup(), parse_mode="HTML")


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
        try: await st.delete()
        except Exception: pass
        await msg_obj.answer(f"✅ Готово! {total}")
    except Exception as e: await msg_obj.answer(f"❌ {e}")
    finally:
        if os.path.exists(pp):
            try: os.remove(pp)
            except Exception: pass
        await state.clear()


@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def ask_type(message, state):
    if message.chat.type in ("group", "supergroup", "channel"): return
    url = message.text.strip()
    if "youtube.com" in url or "youtu.be" in url:
        await message.answer("⚠️ YouTube не работает."); return
    await state.update_data(download_url=url); await state.set_state(BotStates.waiting_for_media_type)
    b = InlineKeyboardBuilder()
    b.button(text="🎵 MP3", callback_data="get_audio"); b.button(text="🎬 MP4", callback_data="get_video")
    await message.answer("❓ Что?", reply_markup=b.as_markup())


@dp.callback_query(F.data.in_({"get_audio", "get_video"}), BotStates.waiting_for_media_type)
async def process_download(cb, state):
    d = await state.get_data(); url = d.get("download_url"); mode = cb.data
    await cb.message.edit_reply_markup(reply_markup=None)
    status = await cb.message.answer("⏳ Качаю..."); await cb.answer()
    loop = asyncio.get_event_loop()
    is_tk = "tiktok.com" in url or "vm.tiktok.com" in url or "vt.tiktok.com" in url
    fn = None
    try:
        if is_tk:
            fn, title, dur, _, _ = await loop.run_in_executor(None, tiktok_via_api, url, mode)
        else:
            fn, title, dur, _, _ = await loop.run_in_executor(None, other_site_download, url, mode)
        if not fn or not os.path.exists(fn): raise FileNotFoundError("не скачалось")
        if mode == "get_audio":
            kw = {"audio": FSInputFile(fn, filename=f"{title}.mp3"), "title": title, "caption": "🎵"}
            if dur: kw["duration"] = int(dur)
            await cb.message.answer_audio(**kw)
        else:
            await cb.message.answer_video(video=FSInputFile(fn, filename=f"{title}.mp4"),
                                          caption="🎬", duration=int(dur) if dur else None)
        try: await status.delete()
        except Exception: pass
    except Exception as e:
        try: await status.edit_text(f"😔 {str(e)[:250]}")
        except Exception: pass
    finally:
        if fn and os.path.exists(fn):
            try: os.remove(fn)
            except Exception: pass
        await state.clear()


@dp.message(F.text == "/stickers")
async def cmd_stickers(message, state):
    if message.chat.type in ("group", "supergroup", "channel"): return
    b = InlineKeyboardBuilder()
    b.button(text="➕ Создать", callback_data="st_create")
    b.button(text="📎 Добавить", callback_data="st_add")
    b.button(text="📂 Мои", callback_data="st_list")
    b.adjust(1)
    await message.answer("🎨 <b>Стикерпаки</b>", reply_markup=b.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "st_create")
async def cb_st_create(cb, state):
    if cb.message.chat.type != "private": await cb.answer("Только ЛС", show_alert=True); return
    uname = await get_bot_username()
    await cb.message.edit_text(f"📝 Имя (латиница). Пак: <code>имя_by_{uname}</code>", parse_mode="HTML")
    await state.set_state(BotStates.waiting_sticker_name); await cb.answer()


@dp.message(BotStates.waiting_sticker_name)
async def process_sticker_name(message, state):
    short = (message.text or "").strip().lower()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,50}$", short):
        await message.answer("⚠️ Только латиница/цифры/_"); return
    uname = await get_bot_username(); full = f"{short}_by_{uname}"
    await state.update_data(sticker_full=full)
    await state.set_state(BotStates.waiting_sticker_title)
    await message.answer("📛 Название:")


@dp.message(BotStates.waiting_sticker_title)
async def process_sticker_title(message, state):
    title = (message.text or "").strip()
    if not title: await message.answer("⚠️ Не пустое."); return
    await state.update_data(sticker_title=title)
    await state.set_state(BotStates.waiting_sticker_first)
    await message.answer("🖼 Картинку.")


def photo_to_webp_sticker(src, dst):
    img = Image.open(src).convert("RGBA"); w, h = img.size; side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    canvas = canvas.resize((512, 512), Image.LANCZOS)
    canvas.save(dst, "WEBP", quality=95, method=6); return dst


@dp.message(BotStates.waiting_sticker_first, F.photo)
async def process_sticker_first(message, state):
    data = await state.get_data(); full = data.get("sticker_full"); title = data.get("sticker_title")
    if not full or not title: await message.answer("⚠️ /stickers"); await state.clear(); return
    p = message.photo[-1]; fi = await bot.get_file(p.file_id)
    src = f"temp_photos/{p.file_id}.jpg"; dst = f"temp_photos/sticker_{p.file_id}.webp"
    await bot.download_file(fi.file_path, src)
    status = await message.answer("🎨 Готовлю...")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, photo_to_webp_sticker, src, dst)
        with open(dst, "rb") as f: sb = f.read()
        await bot.create_new_sticker_set(user_id=message.from_user.id, name=full, title=title,
            stickers=[InputSticker(sticker=BufferedInputFile(sb, filename="s.webp"),
                                    format="static", emoji_list=["😀"])])
        uid = str(message.from_user.id)
        DATA["sticker_packs"].setdefault(uid, []).append({"name": full, "title": title})
        save_data(DATA)
        await status.edit_text(f"✅ Пак создан!\n🔗 https://t.me/addstickers/{full}")
    except Exception as e:
        try: await status.edit_text(f"❌ {str(e)[:300]}")
        except Exception: pass
    finally:
        for f in (src, dst):
            if os.path.exists(f):
                try: os.remove(f)
                except Exception: pass
        await state.clear()


@dp.callback_query(F.data == "st_list")
async def cb_st_list(cb):
    if cb.message.chat.type != "private": await cb.answer("Только ЛС", show_alert=True); return
    packs = DATA["sticker_packs"].get(str(cb.from_user.id), [])
    if not packs: await cb.message.edit_text("📂 Нет паков."); await cb.answer(); return
    lines = ["📂 Паки:\n"] + [f"• {html_mod.escape(p['title'])}\n  https://t.me/addstickers/{p['name']}" for p in packs]
    await cb.message.edit_text("\n".join(lines), parse_mode="HTML"); await cb.answer()


@dp.callback_query(F.data == "st_add")
async def cb_st_add(cb, state):
    if cb.message.chat.type != "private": await cb.answer("Только ЛС", show_alert=True); return
    packs = DATA["sticker_packs"].get(str(cb.from_user.id), [])
    if not packs: await cb.message.edit_text("📂 Нет паков."); await cb.answer(); return
    b = InlineKeyboardBuilder()
    for i, p in enumerate(packs): b.button(text=p["title"], callback_data=f"st_pick:{i}")
    b.adjust(1)
    await cb.message.edit_text("📎 В какой?", reply_markup=b.as_markup()); await cb.answer()


@dp.callback_query(F.data.startswith("st_pick:"))
async def cb_st_pick(cb, state):
    if cb.message.chat.type != "private": await cb.answer("Только ЛС", show_alert=True); return
    try: idx = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Ошибка", show_alert=True); return
    packs = DATA["sticker_packs"].get(str(cb.from_user.id), [])
    if idx < 0 or idx >= len(packs): await cb.answer("Нет", show_alert=True); return
    await state.update_data(add_pack=packs[idx]["name"])
    await state.set_state(BotStates.waiting_sticker_add_photo)
    await cb.message.edit_text(f"📎 {html_mod.escape(packs[idx]['title'])}", parse_mode="HTML")
    await cb.answer()


@dp.message(BotStates.waiting_sticker_add_photo, F.photo)
async def process_sticker_add(message, state):
    data = await state.get_data(); pname = data.get("add_pack")
    if not pname: await message.answer("⚠️ /stickers"); await state.clear(); return
    p = message.photo[-1]; fi = await bot.get_file(p.file_id)
    src = f"temp_photos/{p.file_id}.jpg"; dst = f"temp_photos/sticker_{p.file_id}.webp"
    await bot.download_file(fi.file_path, src)
    status = await message.answer("🎨 Готовлю...")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, photo_to_webp_sticker, src, dst)
        with open(dst, "rb") as f: sb = f.read()
        await bot.add_sticker_to_set(user_id=message.from_user.id, name=pname,
            sticker=InputSticker(sticker=BufferedInputFile(sb, filename="s.webp"),
                                  format="static", emoji_list=["😀"]))
        await status.edit_text("✅ Добавлен.")
    except Exception as e:
        try: await status.edit_text(f"❌ {str(e)[:250]}")
        except Exception: pass
    finally:
        for f in (src, dst):
            if os.path.exists(f):
                try: os.remove(f)
                except Exception: pass
        await state.clear()
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
        [InlineKeyboardButton(text="👁 Прочитать", callback_data=f"whisper:{wid}")]])
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
    is_target = (w.get("target_id") is not None and cb.from_user.id == w["target_id"]) or \
                ((cb.from_user.username or "").lower() == (w.get("target_name") or "").lower())
    is_sender = cb.from_user.id == w.get("from_id")
    if is_sender:
        txt = w["text"]
        if len(txt) <= 180: await cb.answer(f"📤 @{w['target_name']}:\n\n{txt}", show_alert=True)
        else:
            try: await bot.send_message(cb.from_user.id, f"📤 {txt}"); await cb.answer("📩")
            except Exception: await cb.answer(f"📤 {txt[:180]}...", show_alert=True)
    elif is_target:
        txt = w["text"]
        if len(txt) <= 180: await cb.answer(f"🤫 {txt}", show_alert=True)
        else:
            try: await bot.send_message(cb.from_user.id, f"🤫 от {w['from_name']}:\n\n{txt}"); await cb.answer("📩")
            except Exception: await cb.answer(f"🤫 {txt[:180]}...", show_alert=True)
    else: await cb.answer(random.choice(FUNNY_REPLIES), show_alert=True)


# ============ КРЕСТИКИ-НОЛИКИ ============
TTT_WIN_LINES = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]


def _ttt_text(game):
    board = game["board"]
    def cell(i):
        v = board[i]
        return "❌" if v == "X" else ("⭕" if v == "O" else "▫️")
    grid = f"{cell(0)} {cell(1)} {cell(2)}\n{cell(3)} {cell(4)} {cell(5)}\n{cell(6)} {cell(7)} {cell(8)}"
    footer = game.get("footer", "") if game.get("finished") else f"Ход: {'❌' if game['turn']=='x' else '⭕'}"
    return f"❌⭕ <b>Крестики-нолики</b>\n\n❌ {html_mod.escape(game['names'].get('x','?'))}\n⭕ {html_mod.escape(game['names'].get('o','ждём...'))}\n\n{grid}\n\n{footer}"


def _ttt_kb(game):
    board = game["board"]; b = InlineKeyboardBuilder()
    if not game.get("o_id") and not game.get("finished"):
        b.button(text="🙋 Присоединиться", callback_data="ttt_join"); return b.as_markup()
    row = []
    for i in range(9):
        if board[i] is None and not game.get("finished"):
            row.append(InlineKeyboardButton(text="⬜", callback_data=f"ttt_move:{i}"))
        else:
            row.append(InlineKeyboardButton(text="❌" if board[i]=="X" else ("⭕" if board[i]=="O" else "⬜"), callback_data="ttt_noop"))
        if len(row) == 3: b.row(*row); row = []
    b.row(InlineKeyboardButton(text="🏳️ Сдаться", callback_data="ttt_reset"))
    return b.as_markup()


@dp.message(F.text.func(dot_starts(".ttt")))
async def cmd_ttt(message):
    print(f"[ttt] HIT chat={message.chat.id} text={message.text!r}")
    await _try_delete_command(message)
    chat_id = message.chat.id
    if chat_id in TTT_GAMES and not TTT_GAMES[chat_id].get("finished"):
        await message.answer("⚠️ Игра уже идёт."); return
    u = message.from_user
    name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    TTT_GAMES[chat_id] = {"board": [None]*9, "x_id": u.id, "o_id": None, "turn": "x",
                          "names": {"x": name, "o": None}, "finished": False, "footer": "",
                          "bc_id": message.business_connection_id}
    await message.answer(_ttt_text(TTT_GAMES[chat_id]), reply_markup=_ttt_kb(TTT_GAMES[chat_id]), parse_mode="HTML")


@dp.callback_query(F.data == "ttt_join")
async def cb_ttt_join(cb):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game: await cb.answer("Не найдена", show_alert=True); return
    if game["finished"] or game.get("o_id"): await cb.answer("Занято", show_alert=True); return
    if cb.from_user.id == game["x_id"]: await cb.answer("Ты уже ❌", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["o_id"] = u.id; game["names"]["o"] = name
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=cb.message.message_id,
                                     text=_ttt_text(game), reply_markup=_ttt_kb(game), parse_mode="HTML")
    except Exception: pass
    await cb.answer("Погнали!")


@dp.callback_query(F.data.startswith("ttt_move:"))
async def cb_ttt_move(cb):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game or game["finished"]: await cb.answer("Недоступно", show_alert=True); return
    if not game.get("o_id"): await cb.answer("Ждём игрока", show_alert=True); return
    turn = game["turn"]; expected = game["x_id"] if turn == "x" else game["o_id"]
    if cb.from_user.id != expected: await cb.answer("Не твой ход", show_alert=True); return
    try: idx = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Ошибка", show_alert=True); return
    if idx < 0 or idx > 8 or game["board"][idx] is not None:
        await cb.answer("Занято", show_alert=True); return
    game["board"][idx] = "X" if turn == "x" else "O"
    w = None
    for a, b2, c in TTT_WIN_LINES:
        if game["board"][a] and game["board"][a] == game["board"][b2] == game["board"][c]:
            w = game["board"][a]; break
    if not w and all(c is not None for c in game["board"]): w = "draw"
    if w == "X": game["finished"] = True; game["footer"] = f"🏆 Победа! ❌ {html_mod.escape(game['names']['x'])}"
    elif w == "O": game["finished"] = True; game["footer"] = f"🏆 Победа! ⭕ {html_mod.escape(game['names']['o'])}"
    elif w == "draw": game["finished"] = True; game["footer"] = "🤝 Ничья!"
    else: game["turn"] = "o" if turn == "x" else "x"
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=cb.message.message_id,
                                     text=_ttt_text(game), reply_markup=_ttt_kb(game), parse_mode="HTML")
    except Exception: pass
    await cb.answer()


@dp.callback_query(F.data == "ttt_reset")
async def cb_ttt_reset(cb):
    chat_id = cb.message.chat.id
    if chat_id in TTT_GAMES: TTT_GAMES.pop(chat_id, None)
    try: await cb.message.delete()
    except Exception: pass
    await cb.answer("Отменено")


@dp.callback_query(F.data == "ttt_noop")
async def cb_ttt_noop(cb): await cb.answer()


# ============ УНО ============
UNO_COLORS = ["🔴", "🔵", "🟢", "🟡"]
COLOR_NAMES = {"🔴": "красный", "🔵": "синий", "🟢": "зелёный", "🟡": "жёлтый", "🌈": "wild"}
NUM_EMOJI = {0:"0️⃣",1:"1️⃣",2:"2️⃣",3:"3️⃣",4:"4️⃣",5:"5️⃣",6:"6️⃣",7:"7️⃣",8:"8️⃣",9:"9️⃣"}
DIFF_LABELS = {"easy": "🟢 Легко", "medium": "🟡 Средне", "hard": "🔴 Сложно"}


def _uno_diff(uid):
    return DATA.get("user_settings", {}).get(str(uid), {}).get("uno_difficulty", "medium")


def _uno_set_diff(uid, d):
    DATA.setdefault("user_settings", {}).setdefault(str(uid), {})["uno_difficulty"] = d
    save_data(DATA)


def _uno_deck():
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
    random.shuffle(deck); return deck


def _uno_label(c):
    t = c["type"]
    if t == "num": return f"{c['color']} {NUM_EMOJI.get(c['num'], str(c['num']))}"
    if t == "skip": return f"{c['color']} 🚫"
    if t == "reverse": return f"{c['color']} 🔄"
    if t == "draw2": return f"{c['color']} +2"
    if t == "wild": return "🌈"
    if t == "wild4": return "🌈 +4"
    return "?"


def _uno_can(card, top, cur):
    if card["color"] == "🌈": return True
    if card["color"] == cur: return True
    if card["type"] == "num" and top["type"] == "num" and card["num"] == top["num"]: return True
    if card["type"] != "num" and card["type"] == top["type"]: return True
    return False


def _uno_start_round(game):
    deck = _uno_deck()
    game["hands"] = {str(p["id"]): [deck.pop() for _ in range(7)] for p in game["players"]}
    game["deck"] = deck
    while True:
        first = deck.pop()
        if first["type"] == "num": break
        deck.insert(0, first)
    game["top"] = first; game["current_color"] = first["color"]
    game["turn_idx"] = 0; game["direction"] = 1
    game["started"] = True; game["finished"] = False


def _uno_group_text(game):
    top = game["top"]; cur = game["current_color"]
    turn_p = game["players"][game["turn_idx"]]
    lines = [f"🎴 <b>Уно</b>\n", f"🎯 Верхняя: {_uno_label(top)}", f"🎨 Цвет: {cur} {COLOR_NAMES.get(cur,'')}\n", "👥 Игроки:"]
    for p in game["players"]:
        cnt = len(game["hands"].get(str(p["id"]), []))
        mark = "👈 " if p["id"] == turn_p["id"] else ""
        bot_tag = " 🤖" if p.get("is_bot") else ""
        lines.append(f"{mark}{html_mod.escape(p['name'])}{bot_tag}: <b>{cnt}</b>")
    lines.append(f"\n⏭ Ход: <b>{html_mod.escape(turn_p['name'])}</b>")
    lines.append("\n👆 Карты — в ЛС у бота.")
    return "\n".join(lines)


def _uno_group_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🎴 Взять карту", callback_data="uno_grp_draw"),
          InlineKeyboardButton(text="📢 Уно!", callback_data="uno_shout"))
    b.row(InlineKeyboardButton(text="🏳️ Отмена", callback_data="uno_surrender"))
    return b.as_markup()


def _uno_dm_text(game, uid):
    hand = game["hands"].get(str(uid), [])
    top = game["top"]; cur = game["current_color"]
    is_turn = game["players"][game["turn_idx"]]["id"] == uid
    txt = f"🃏 <b>Твоя рука ({len(hand)}):</b>\nВерхняя: {_uno_label(top)}\nЦвет: {cur}\n\n"
    txt += "✅ <b>Твой ход!</b>\n" if is_turn else f"⏳ Ждём: {html_mod.escape(game['players'][game['turn_idx']]['name'])}\n"
    return txt


def _uno_dm_kb(game, uid):
    hand = game["hands"].get(str(uid), [])
    top = game["top"]; cur = game["current_color"]
    is_turn = game["players"][game["turn_idx"]]["id"] == uid
    b = InlineKeyboardBuilder(); row = []
    for i, c in enumerate(hand):
        ok = _uno_can(c, top, cur) and is_turn
        row.append(InlineKeyboardButton(text=_uno_label(c), callback_data=f"uno_play:{i}" if ok else "uno_noop"))
        if len(row) == 4: b.row(*row); row = []
    if row: b.row(*row)
    if is_turn: b.row(InlineKeyboardButton(text="🃏 Взять карту", callback_data="uno_dm_draw"))
    return b.as_markup()


async def _uno_send_dm(uid, game):
    try: await bot.send_message(uid, _uno_dm_text(game, uid), reply_markup=_uno_dm_kb(game, uid), parse_mode="HTML")
    except Exception as e: print(f"[uno dm {uid}] {str(e)[:120]}")


async def _uno_refresh_group(game, chat_id):
    text = _uno_group_text(game); kb = _uno_group_kb()
    msg_id = game.get("group_msg_id")
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            m = await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
            game["group_msg_id"] = m.message_id
        except Exception: pass


async def _uno_after(game, chat_id, uid, card):
    n = len(game["players"])
    if card["type"] == "skip":
        game["turn_idx"] = (game["turn_idx"] + 2 * game["direction"]) % n
    elif card["type"] == "reverse":
        game["direction"] *= -1
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % n
    elif card["type"] in ("draw2", "wild4"):
        cnt = 2 if card["type"] == "draw2" else 4
        nxt = (game["turn_idx"] + game["direction"]) % n
        nxt_id = game["players"][nxt]["id"]
        for _ in range(cnt):
            if game["deck"]: game["hands"][str(nxt_id)].append(game["deck"].pop())
        game["turn_idx"] = nxt
    else:
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % n

    if not game["hands"].get(str(uid)):
        winner = next((p["name"] for p in game["players"] if p["id"] == uid), "?")
        target = game["chat_id"] if game["mode"] == "ls" else chat_id
        try: await bot.send_message(target, f"🏆 <b>{html_mod.escape(winner)} выиграл!</b>", parse_mode="HTML")
        except Exception: pass
        UNO_GAMES.pop(game["chat_id"] if game["mode"] == "ls" else chat_id, None); return

    if game["mode"] == "group": await _uno_refresh_group(game, chat_id)
    for p in game["players"]:
        if p.get("is_bot"): continue
        await _uno_send_dm(p["id"], game)
    cur = game["players"][game["turn_idx"]]
    if cur.get("is_bot"):
        await asyncio.sleep(1.5); await _uno_bot(game, chat_id)


async def _uno_bot(game, chat_id):
    if game.get("finished"): return
    bp = game["players"][game["turn_idx"]]
    if not bp.get("is_bot"): return
    hand = game["hands"].get("0", [])
    top = game["top"]; cur = game["current_color"]; diff = bp.get("difficulty", "medium")
    playable = [(i, c) for i, c in enumerate(hand) if _uno_can(c, top, cur)]
    target = game["chat_id"] if game["mode"] == "ls" else chat_id
    if not playable:
        if game["deck"]: hand.append(game["deck"].pop())
        game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
        try: await bot.send_message(target, "🤖 Бот взял карту.")
        except Exception: pass
        for p in game["players"]:
            if p.get("is_bot"): continue
            await _uno_send_dm(p["id"], game)
        # после сброса бот сразу не ходит, ход перешёл к игроку
        return
    if diff == "easy": pick = random.choice(playable)
    elif diff == "medium":
        normals = [(i, c) for i, c in playable if c["color"] != "🌈"]
        pick = random.choice(normals if normals else playable)
    else:
        cc = {}
        for c in hand:
            if c["color"] != "🌈": cc[c["color"]] = cc.get(c["color"], 0) + 1
        score = []
        for i, c in playable:
            s = 100 if c["type"]=="wild4" else 80 if c["type"]=="draw2" else 60 if c["type"]=="skip" else 50 if c["type"]=="reverse" else 40 if c["type"]=="wild" else 10 + cc.get(c["color"],0)
            score.append((s, i, c))
        score.sort(reverse=True); pick = (score[0][1], score[0][2])
    idx, card = pick
    hand.pop(idx); game["top"] = card
    if card["color"] == "🌈":
        cc = {}
        for c in hand:
            if c["color"] != "🌈": cc[c["color"]] = cc.get(c["color"], 0) + 1
        game["current_color"] = max(cc, key=cc.get) if (diff == "hard" and cc) else random.choice(UNO_COLORS)
    else: game["current_color"] = card["color"]
    try:
        sfx = f" → {game['current_color']}" if card["color"] == "🌈" else ""
        await bot.send_message(target, f"🤖 Бот сбросил {_uno_label(card)}{sfx}")
    except Exception: pass
    await _uno_after(game, chat_id, 0, card)


# --- УНО в ЛС: и .uno, и /uno, и /uno@bot ---
@dp.message(F.text.func(lambda t: _normalize_dot(t) in (".uno", "/uno", "uno")))
async def cmd_uno_dot(message):
    if message.chat.type != "private": return
    await _try_delete_command(message)
    chat_id = message.chat.id
    if chat_id in UNO_GAMES:
        await message.answer("⚠️ Игра уже идёт. /cancel чтобы сбросить."); return
    uid = message.from_user.id
    d = _uno_diff(uid)
    b = InlineKeyboardBuilder()
    for k, label in DIFF_LABELS.items():
        mark = "✅ " if k == d else ""
        b.button(text=f"{mark}{label}", callback_data=f"uno_diff:{k}")
    b.row(InlineKeyboardButton(text="🚀 Начать", callback_data="uno_start_ls"))
    b.adjust(1)
    await message.answer(f"🎴 <b>Уно против бота</b>\n\nСложность: <b>{DIFF_LABELS[d]}</b>",
                          reply_markup=b.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("uno_diff:"))
async def cb_uno_diff(cb):
    d = cb.data.split(":", 1)[1]
    if d not in DIFF_LABELS: await cb.answer("Ошибка", show_alert=True); return
    _uno_set_diff(cb.from_user.id, d)
    b = InlineKeyboardBuilder()
    for k, label in DIFF_LABELS.items():
        mark = "✅ " if k == d else ""
        b.button(text=f"{mark}{label}", callback_data=f"uno_diff:{k}")
    b.row(InlineKeyboardButton(text="🚀 Начать", callback_data="uno_start_ls"))
    b.adjust(1)
    try: await cb.message.edit_text(f"🎴 <b>Уно против бота</b>\n\nСложность: <b>{DIFF_LABELS[d]}</b>",
                                     reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception: pass
    await cb.answer(DIFF_LABELS[d])


@dp.callback_query(F.data == "uno_start_ls")
async def cb_uno_start_ls(cb):
    chat_id = cb.message.chat.id
    if chat_id in UNO_GAMES: await cb.answer("Уже идёт", show_alert=True); return
    uid = cb.from_user.id; name = cb.from_user.full_name or "Ты"; d = _uno_diff(uid)
    game = {"mode": "ls", "players": [{"id": uid, "name": name},
            {"id": 0, "name": "🤖 Бот", "is_bot": True, "difficulty": d}],
            "hands": {}, "deck": [], "top": None, "current_color": None,
            "turn_idx": 0, "direction": 1, "started": True, "finished": False,
            "chat_id": chat_id, "player_uid": uid}
    _uno_start_round(game); UNO_GAMES[chat_id] = game
    try: await cb.message.delete()
    except Exception: pass
    await _uno_send_dm(uid, game); await cb.answer("Погнали!")


def _uno_lobby_text(game):
    players = game["players"]
    lines = ["🎴 <b>Набор в Уно</b>\n"]
    if players: lines.append(f"👥 ({len(players)}): " + ", ".join(html_mod.escape(p["name"]) for p in players))
    if game.get("no_timer"): lines.append("\n⏸ Таймер отключён. Старт: <code>/play</code>")
    else: lines.append(f"\n⏱ Старт через <b>{game.get('time_left', UNO_LOBBY_TIMEOUT)}</b> сек.")
    lines.append("\nМинимум 2 игрока.")
    return "\n".join(lines)


async def _uno_lobby_tick(chat_id):
    while True:
        await asyncio.sleep(UNO_LOBBY_TICK)
        game = UNO_GAMES.get(chat_id)
        if not game or game.get("started"): return
        if game.get("no_timer"): continue
        game["time_left"] = max(0, game.get("time_left", UNO_LOBBY_TIMEOUT) - UNO_LOBBY_TICK)
        b = InlineKeyboardBuilder(); b.button(text="🙋 Присоединиться", callback_data="uno_join")
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                         text=_uno_lobby_text(game), reply_markup=b.as_markup(), parse_mode="HTML")
        except Exception: pass
        if game["time_left"] <= 0:
            try: await bot.send_message(chat_id, "⏱ Время вышло. Жду <code>/play</code>.", parse_mode="HTML")
            except Exception: pass
            return


# --- УНО в группе: /uno ---
@dp.message(F.text.func(lambda t: t and _normalize_dot(t).startswith("/uno")))
async def cmd_uno_group(message):
    if message.chat.type == "private": return
    await _try_delete_command(message)
    chat_id = message.chat.id
    if chat_id in UNO_GAMES and UNO_GAMES[chat_id].get("started"):
        await message.answer("⚠️ Игра уже идёт."); return
    args = (message.text or "").split()
    no_timer = any(a.lower() == "notimer" for a in args[1:])
    u = message.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    UNO_GAMES[chat_id] = {"mode": "group", "players": [{"id": u.id, "name": name}],
                          "bc_id": message.business_connection_id, "no_timer": no_timer,
                          "time_left": UNO_LOBBY_TIMEOUT, "started": False, "deck": [], "hands": {}}
    game = UNO_GAMES[chat_id]
    b = InlineKeyboardBuilder(); b.button(text="🙋 Присоединиться", callback_data="uno_join")
    msg = await message.answer(_uno_lobby_text(game), reply_markup=b.as_markup(), parse_mode="HTML")
    game["lobby_msg_id"] = msg.message_id
    if not no_timer: asyncio.create_task(_uno_lobby_tick(chat_id))


@dp.callback_query(F.data == "uno_join")
async def cb_uno_join(cb):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or game.get("started"): await cb.answer("Игра идёт", show_alert=True); return
    uid = cb.from_user.id
    if any(p["id"] == uid for p in game["players"]): await cb.answer("Ты уже тут", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["players"].append({"id": uid, "name": name})
    b = InlineKeyboardBuilder(); b.button(text="🙋 Присоединиться", callback_data="uno_join")
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                     text=_uno_lobby_text(game), reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception: pass
    await cb.answer("Зашёл!")


@dp.message(F.text == "/play")
async def cmd_play(message):
    await _try_delete_command(message)
    chat_id = message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or game.get("started"):
        await message.answer("Нет активного лобби. Сначала <code>/uno</code>.", parse_mode="HTML"); return
    creator = game["players"][0]["id"] if game.get("players") else None
    if not (is_owner(message.from_user) or message.from_user.id == creator):
        await message.answer("Стартовать может только создатель лобби или владелец."); return
    if len(game["players"]) < 2: await message.answer("⚠️ Нужно ≥2 игрока."); return
    _uno_start_round(game)
    try: await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                      text="🎴 <b>Уно началось!</b>", parse_mode="HTML")
    except Exception: pass
    for p in game["players"]:
        try: await bot.send_message(p["id"], "🎴 Игра началась!")
        except Exception:
            try: await bot.send_message(chat_id, f"⚠️ {html_mod.escape(p['name'])} — напиши боту в ЛС /start.")
            except Exception: pass
    for p in game["players"]: await _uno_send_dm(p["id"], game)
    await _uno_refresh_group(game, chat_id)


@dp.callback_query(F.data.startswith("uno_play:"))
async def cb_uno_play(cb):
    chat_id = cb.message.chat.id
    # ищем игру: сначала как ЛС, потом как группа
    game = UNO_GAMES.get(chat_id)
    if not game:
        # возможно колбэк из ЛС, а игра привязана к ЛС chat_id
        for cid, g in UNO_GAMES.items():
            if g.get("mode") == "ls" and g.get("player_uid") == cb.from_user.id:
                chat_id = cid; game = g; break
    if not game: await cb.answer("Нет игры", show_alert=True); return
    try: idx = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Ошибка", show_alert=True); return
    uid = cb.from_user.id
    if game["players"][game["turn_idx"]]["id"] != uid: await cb.answer("Не твой ход", show_alert=True); return
    hand = game["hands"].get(str(uid), [])
    if idx < 0 or idx >= len(hand): await cb.answer("Нет карты", show_alert=True); return
    card = hand[idx]; top = game["top"]; cur = game["current_color"]
    if not _uno_can(card, top, cur): await cb.answer("Нельзя", show_alert=True); return
    hand.pop(idx); game["top"] = card
    if card["color"] == "🌈":
        b = InlineKeyboardBuilder()
        for c in UNO_COLORS: b.button(text=c, callback_data=f"uno_color:{c}")
        b.adjust(4)
        await cb.message.answer("🎨 Выбери цвет:", reply_markup=b.as_markup())
        game["pending_uid"] = uid; game["pending_card"] = card
        await cb.answer(); return
    game["current_color"] = card["color"]
    await _uno_after(game, chat_id, uid, card); await cb.answer()


@dp.callback_query(F.data.startswith("uno_color:"))
async def cb_uno_color(cb):
    color = cb.data.split(":", 1)[1]; uid = cb.from_user.id
    target = next(((cid, g) for cid, g in UNO_GAMES.items() if g.get("pending_uid") == uid), None)
    if not target: await cb.answer("Неактуально", show_alert=True); return
    chat_id, game = target
    game["current_color"] = color
    card = game.pop("pending_card"); game.pop("pending_uid", None)
    try: await cb.message.edit_text(f"🎨 {color}")
    except Exception: pass
    await _uno_after(game, chat_id, uid, card); await cb.answer(color)


@dp.callback_query(F.data == "uno_dm_draw")
async def cb_uno_dm_draw(cb):
    uid = cb.from_user.id
    target = next(((cid, g) for cid, g in UNO_GAMES.items()
                   if (g["mode"] == "ls" and g.get("player_uid") == uid) or
                      (g["mode"] == "group" and uid in [p["id"] for p in g["players"]])), None)
    if not target: await cb.answer("Нет игры", show_alert=True); return
    chat_id, game = target
    if game["players"][game["turn_idx"]]["id"] != uid: await cb.answer("Не твой ход", show_alert=True); return
    if not game["deck"]: await cb.answer("Колода пуста", show_alert=True); return
    card = game["deck"].pop(); game["hands"][str(uid)].append(card)
    game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
    await cb.answer(f"Взял {_uno_label(card)}")
    target_chat = game["chat_id"] if game["mode"] == "ls" else chat_id
    try: await bot.send_message(target_chat, "🎴 Игрок взял карту.")
    except Exception: pass
    for p in game["players"]:
        if p.get("is_bot"): continue
        await _uno_send_dm(p["id"], game)
    cur = game["players"][game["turn_idx"]]
    if cur.get("is_bot"): await asyncio.sleep(1.5); await _uno_bot(game, chat_id)


@dp.callback_query(F.data == "uno_grp_draw")
async def cb_uno_grp_draw(cb):
    chat_id = cb.message.chat.id; game = UNO_GAMES.get(chat_id)
    if not game: await cb.answer("Нет игры", show_alert=True); return
    uid = cb.from_user.id
    if game["players"][game["turn_idx"]]["id"] != uid: await cb.answer("Не твой ход", show_alert=True); return
    if not game["deck"]: await cb.answer("Колода пуста", show_alert=True); return
    card = game["deck"].pop(); game["hands"][str(uid)].append(card)
    game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
    await cb.answer(f"Взял {_uno_label(card)}")
    try: await bot.send_message(uid, f"🃏 Взял: {_uno_label(card)}")
    except Exception: pass
    await _uno_refresh_group(game, chat_id)


@dp.callback_query(F.data == "uno_shout")
async def cb_uno_shout(cb):
    chat_id = cb.message.chat.id; game = UNO_GAMES.get(chat_id)
    if not game: await cb.answer("Нет игры", show_alert=True); return
    uid = cb.from_user.id
    hand = game["hands"].get(str(uid), [])
    if len(hand) == 1:
        name = next((p["name"] for p in game["players"] if p["id"] == uid), "?")
        try: await bot.send_message(chat_id, f"📣 <b>{html_mod.escape(name)}</b> крикнул УНО!", parse_mode="HTML")
        except Exception: pass
        await cb.answer("📣")
    elif len(hand) > 1: await cb.answer("Уно только с 1 картой!", show_alert=True)
    else: await cb.answer("Ты уже выиграл", show_alert=True)


@dp.callback_query(F.data == "uno_surrender")
async def cb_uno_surrender(cb):
    chat_id = cb.message.chat.id
    if chat_id in UNO_GAMES:
        UNO_GAMES.pop(chat_id, None)
        try: await bot.send_message(chat_id, "🏳️ Отменено.")
        except Exception: pass
    await cb.answer()


@dp.callback_query(F.data == "uno_noop")
async def cb_uno_noop(cb): await cb.answer()


# ============ БИЗНЕС ============
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    try:
        if connection.is_enabled:
            BUSINESS_CONNECTIONS[connection.id] = {"user_id": connection.user.id}
            DATA.setdefault("business_owners", {})[connection.id] = connection.user.id
            if connection.id in DATA.get("dead_bc", []):
                DATA["dead_bc"] = [x for x in DATA["dead_bc"] if x != connection.id]
            save_data(DATA); print(f"Business подключён: {connection.id}")
        else:
            BUSINESS_CONNECTIONS.pop(connection.id, None)
            DATA.get("business_owners", {}).pop(connection.id, None)
            save_data(DATA)
    except Exception as e: print(f"business: {str(e)[:150]}")


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
            hist.append({"from_id": user.id, "text": text[:400], "ts": time.time(), "is_out": is_out})
            if len(hist) > HISTORY_LIMIT: DATA["history"][str(chat_id)] = hist[-HISTORY_LIMIT:]
        if chat_id in MUTED and user.id in MUTED[chat_id]:
            if is_owner(user): return
            try: await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
            except Exception: pass
            try: await bot.send_message(chat_id, "🔇 МОЛЧАТЬ!!!", business_connection_id=bc_id)
            except Exception: pass
    except Exception as e: print(f"bc_main: {str(e)[:150]}")


@dp.business_message(F.voice)
async def bc_voice(message):
    bc = message.business_connection_id
    if not bc or _is_dead_bc(bc): return
    fi = await bot.get_file(message.voice.file_id)
    src, wav = f"downloads/stt_{message.voice.file_id}.ogg", f"downloads/stt_{message.voice.file_id}.wav"
    await bot.download_file(fi.file_path, src)
    try: status = await bot.send_message(message.chat.id, "📝 Слушаю...", business_connection_id=bc)
    except Exception: return
    try:
        loop = asyncio.get_event_loop()
        text, _ = await loop.run_in_executor(None, _stt_pipeline, src, wav)
        preview = (f"📝 Расшифровка:\n\n{text[:3900]}" if text else "🤷 Не разобрал.")
        await bot.edit_message_text(chat_id=message.chat.id, message_id=status.message_id, text=preview, business_connection_id=bc)
    except Exception as e: print(f"bc stt: {str(e)[:150]}")
    finally:
        for p in (src, wav):
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass


# ============ HTTP-СЕРВЕР ДЛЯ HEALTHCHECK RELAXDEV ============
async def _health(request):
    return web.Response(text="ok")


async def _run_http_server():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    app.router.add_get("/healthz", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[HTTP] healthcheck на 0.0.0.0:{port}")


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
        BotCommand(command="whisper", description="Шёпот"),
        BotCommand(command="uno", description="Уно (в ЛС с ботом, в группе с людьми)"),
        BotCommand(command="play", description="Старт Уно в группе"),
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
    # Запускаем HTTP-сервер СРАЗУ, чтобы RelaxDev видел открытый порт
    asyncio.create_task(_run_http_server())
    await asyncio.sleep(0.5)

    DATA = await load_data_from_tg()
    print(f"keys: {len(DATA['keys'])}, users: {len(DATA['users'])}")
    await set_bot_commands()
    await set_bot_menu()
    asyncio.create_task(keep_alive())
    print("Bot started")
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): print("stopped")
