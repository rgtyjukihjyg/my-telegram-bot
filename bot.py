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


def _mark_bc_dead(bc_id):
    if bc_id not in DATA["dead_bc"]:
        DATA["dead_bc"].append(bc_id)
        if len(DATA["dead_bc"]) > 100: DATA["dead_bc"] = DATA["dead_bc"][-100:]


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


# ============ РУЛЕТКА ============
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


# ============ ГОЛОСОВЫЕ ============
@dp.message(F.voice)
async def process_voice(message, state):
    await state.set_state(None); await _handle_stt(message, message.voice.file_id, ".ogg")


@dp.message(F.video_note)
async def process_video_note(message, state):
    await state.set_state(None); await _handle_stt(message, message.video_note.file_id, ".mp4")


# ============ SWITCH / TTS ============
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


# ============ МУТ ============
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


# ============ ОБЩИЕ ============
HELP_TEXT = ("📖 <b>Что умею:</b>\n\n"
    "🎲 <code>.ruletka</code> или <code>.rl</code> — рулетка\n"
    "❌⭕ <code>.ttt</code> — крестики-нолики\n"
    "🎴 <code>.uno</code> в ЛС / <code>/uno</code> в группе\n"
    "🔁 <code>.switch</code> — раскладка (ЛС, реплай)\n"
    "🔊 <code>.tts текст</code> — озвучка (ЛС)\n"
    "🎤 голосовое — расшифровка\n"
    "📷 фото — на куски (ЛС)\n"
    "🎬 ссылка — видео/MP3 (ЛС)\n"
    "🎨 <code>/stickers</code> — стикерпаки (ЛС)\n"
    "🔑 <code>/mykey</code> — статус ключа\n")


@dp.message(F.text.func(lambda t: _normalize_dot(t) in ("/help", ".help")))
async def cmd_help(message): await message.answer(HELP_TEXT, parse_mode="HTML")


@dp.message(F.text == "/start")
async def cmd_start(message):
    bot_uname = await get_bot_username()
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
    bot_uname = await get_bot_username()
    await message.answer(f"🤫 <code>@{bot_uname} текст @username</code>", parse_mode="HTML")
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


def _mark_bc_dead(bc_id):
    if bc_id not in DATA["dead_bc"]:
        DATA["dead_bc"].append(bc_id)
        if len(DATA["dead_bc"]) > 100: DATA["dead_bc"] = DATA["dead_bc"][-100:]


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


# ============ РУЛЕТКА ============
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


# ============ ГОЛОСОВЫЕ ============
@dp.message(F.voice)
async def process_voice(message, state):
    await state.set_state(None); await _handle_stt(message, message.voice.file_id, ".ogg")


@dp.message(F.video_note)
async def process_video_note(message, state):
    await state.set_state(None); await _handle_stt(message, message.video_note.file_id, ".mp4")


# ============ SWITCH / TTS ============
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


# ============ МУТ ============
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


# ============ ОБЩИЕ ============
HELP_TEXT = ("📖 <b>Что умею:</b>\n\n"
    "🎲 <code>.ruletka</code> или <code>.rl</code> — рулетка\n"
    "❌⭕ <code>.ttt</code> — крестики-нолики\n"
    "🎴 <code>.uno</code> в ЛС / <code>/uno</code> в группе\n"
    "🔁 <code>.switch</code> — раскладка (ЛС, реплай)\n"
    "🔊 <code>.tts текст</code> — озвучка (ЛС)\n"
    "🎤 голосовое — расшифровка\n"
    "📷 фото — на куски (ЛС)\n"
    "🎬 ссылка — видео/MP3 (ЛС)\n"
    "🎨 <code>/stickers</code> — стикерпаки (ЛС)\n"
    "🔑 <code>/mykey</code> — статус ключа\n")


@dp.message(F.text.func(lambda t: _normalize_dot(t) in ("/help", ".help")))
async def cmd_help(message): await message.answer(HELP_TEXT, parse_mode="HTML")


@dp.message(F.text == "/start")
async def cmd_start(message):
    bot_uname = await get_bot_username()
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
    bot_uname = await get_bot_username()
    await message.answer(f"🤫 <code>@{bot_uname} текст @username</code>", parse_mode="HTML")
