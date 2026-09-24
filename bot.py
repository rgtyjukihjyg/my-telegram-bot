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


# ================== КОНФИГ ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("нет BOT_TOKEN")
STORAGE_CHAT_ID = os.environ.get("STORAGE_CHAT_ID")
OWNER_ID = 7752398574
OWNER_USERNAME = (os.environ.get("OWNER_USERNAME") or "vimbrix").lower().lstrip("@")
_eid = os.environ.get("OWNER_ID")
if _eid and _eid.strip().isdigit():
    OWNER_ID = int(_eid)
DATA_FILE = "data.json"
KEY_LENGTH = 12
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
STT_SIZE = os.environ.get("STT_SIZE", "small")
PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
WHISPER_TTL = 86400
HISTORY_LIMIT = 60
UNO_LOBBY_TIMEOUT = 60
UNO_LOBBY_TICK = 10
MAFIA_LOBBY_TIMEOUT = 60
BTN_CREATE_KEY = "🔑 Создать ключ"
BTN_KEYS_STATUS = "📋 Статусы ключей"

_skw = {"timeout": 120}
if PROXY:
    _skw["proxy"] = PROXY
session = AiohttpSession(**_skw)
bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

BOT_USERNAME_CACHE = None
STT_MODEL = None
TTT_GAMES = {}
UNO_GAMES = {}
MAFIA_GAMES = {}
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
    "Это не твоё. Отойди.", "Руки убрал!", "Кыш.", "А тебе кто разрешил?"]
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
SPACE_RE = re.compile(r"\s+")


def _cmd(t, *names):
    if not t: return False
    s = SPACE_RE.sub("", t.strip().lower()).rstrip(".!?,;:")
    return s in names


UNO_VARIANTS = (".uno", "/uno", "uno", ".уно", "/уно", "уно", ".гтщ", "/гтщ", "гтщ")
RL_VARIANTS = (".rl", ".рулетка", "рулетка", ".ruletka", "/ruletka", "ruletka", ".кд", "кд")
TTT_VARIANTS = (".ttt", "/ttt", ".еее", "/еее", ".ттт", "ттт")
MUTE_VARIANTS = (".mute", "/mute", ".ьгте", ".ьутв", ".муте", "муте")
UNMUTE_VARIANTS = (".unmute", "/unmute", ".гтьутв", ".гьутв")
SWITCH_VARIANTS = (".switch", "/switch", ".ыцшыеср", ".сщьше")
TTS_VARIANTS = (".tts", "/tts", ".еес", ".ееы")
HELP_VARIANTS = (".help", "/help", ".рудз", ".рфз")


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
            print(f"load OK: keys={len(d['keys'])}, users={len(d['users'])}", flush=True)
            save_data_to_file(d)
            return d
        except Exception as e:
            print(f"load tg {attempt+1}: {str(e)[:120]}", flush=True)
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
    except Exception as e: print(f"save tg: {str(e)[:150]}", flush=True)


DATA = _empty_data()


async def save_data_sync(data=None):
    d = data if data is not None else DATA
    save_data_to_file(d)
    if STORAGE_CHAT_ID:
        try: await save_data_to_tg(d)
        except Exception as e: print(f"save_sync: {str(e)[:150]}", flush=True)


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
        bc = bc_id or getattr(message, "business_connection_id", None)
        if bc:
            await bot.delete_business_messages(business_connection_id=bc,
                                                message_ids=[message.message_id])
        else:
            await message.delete()
    except Exception:
        pass


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
print(f"ffmpeg: {FFMPEG_EXE_PATH}", flush=True)
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
        print(f"STT ({STT_SIZE})...", flush=True)
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


async def _handle_stt(message: Message, file_id, ext_hint=".ogg"):
    fi = await bot.get_file(file_id)
    src, wav = f"downloads/stt_{file_id}{ext_hint}", f"downloads/stt_{file_id}.wav"
    await bot.download_file(fi.file_path, src)
    bc = getattr(message, "business_connection_id", None)
    if bc:
        status = await bot.send_message(message.chat.id, "📝 Слушаю...", business_connection_id=bc)
    else:
        status = await message.answer("📝 Слушаю...")
    try:
        loop = asyncio.get_event_loop()
        text, _ = await loop.run_in_executor(None, _stt_pipeline, src, wav)
        preview = (text[:3900] if len(text) <= 3900 else text[:3900] + "...") or "🤷 Не разобрал."
        new_text = f"📝 Расшифровка:\n\n{preview}"
        if bc:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=status.message_id,
                                         text=new_text, business_connection_id=bc)
        else:
            await status.edit_text(new_text)
    except Exception as e:
        try:
            if bc:
                await bot.edit_message_text(chat_id=message.chat.id, message_id=status.message_id,
                                             text=f"😔 {str(e)[:250]}", business_connection_id=bc)
            else:
                await status.edit_text(f"😔 {str(e)[:250]}")
        except Exception:
            pass
    finally:
        for p in (src, wav):
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass


def _tts_generate(text, out):
    gTTS(text=text, lang="ru").save(out); return out


# ================== MONKEY-PATCH: business-ответы ==================
_orig_answer = Message.answer
_orig_answer_voice = Message.answer_voice
_orig_reply = Message.reply


async def _patched_answer(self, *args, **kwargs):
    bc = getattr(self, "business_connection_id", None)
    if bc and "business_connection_id" not in kwargs:
        text = args[0] if args else kwargs.get("text", "")
        new_kwargs = {k: v for k, v in kwargs.items() if k != "text"}
        new_kwargs["business_connection_id"] = bc
        try:
            return await self.bot.send_message(self.chat.id, text, **new_kwargs)
        except Exception as e:
            print(f"biz answer fail: {str(e)[:150]}", flush=True)
    return await _orig_answer(self, *args, **kwargs)


async def _patched_answer_voice(self, *args, **kwargs):
    bc = getattr(self, "business_connection_id", None)
    if bc and "business_connection_id" not in kwargs:
        voice = args[0] if args else kwargs.get("voice")
        new_kwargs = {k: v for k, v in kwargs.items() if k != "voice"}
        new_kwargs["business_connection_id"] = bc
        try:
            return await self.bot.send_voice(self.chat.id, voice, **new_kwargs)
        except Exception as e:
            print(f"biz answer_voice fail: {str(e)[:150]}", flush=True)
    return await _orig_answer_voice(self, *args, **kwargs)


async def _patched_reply(self, *args, **kwargs):
    bc = getattr(self, "business_connection_id", None)
    if bc:
        kwargs.setdefault("reply_to_message_id", self.message_id)
        return await _patched_answer(self, *args, **kwargs)
    return await _orig_reply(self, *args, **kwargs)


Message.answer = _patched_answer
Message.answer_voice = _patched_answer_voice
Message.reply = _patched_reply


# ================== MIDDLEWARE ==================
ALWAYS_FREE = {"/start", "/help", "/code", "/mykey", "/whoami", "/whisper", "/cancel",
               "/uno", "/play", "/mafia", "/newkey", "/stickers"}


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, Message):
            return await handler(event, data)
        user = event.from_user
        if user is None:
            return await handler(event, data)
        chat_id = event.chat.id if event.chat else None
        bc = getattr(event, "business_connection_id", None)
        text = (getattr(event, "text", None) or "").strip()
        if text:
            print(f"[MIDDLE] uid={user.id} type={event.chat.type} biz={bool(bc)} text={text[:50]!r}",
                  flush=True)
        if chat_id and chat_id in MUTED and user.id in MUTED[chat_id] and not is_owner(user):
            try:
                if bc:
                    await bot.delete_business_messages(business_connection_id=bc,
                                                        message_ids=[event.message_id])
                else:
                    await event.delete()
            except Exception: pass
            try:
                if bc:
                    await bot.send_message(chat_id, "🔇 МОЛЧАТЬ!!!", business_connection_id=bc)
                else:
                    await bot.send_message(chat_id, "🔇 МОЛЧАТЬ!!!")
            except Exception: pass
            return
        if not text:
            return await handler(event, data)
        s = SPACE_RE.sub("", text.strip().lower()).rstrip(".!?,;:")
        if s.startswith("."):
            return await handler(event, data)
        cmd = text.split()[0].lower() if text else ""
        if cmd in ALWAYS_FREE:
            return await handler(event, data)
        if is_owner(user):
            return await handler(event, data)
        status = user_status(user.id)
        if status == "valid":
            return await handler(event, data)
        if status == "expired":
            DATA["users"].pop(str(user.id), None); save_data(DATA)
            try: await event.answer("⏰ Ключ истёк. /code")
            except Exception: pass
            return
        try: await event.answer("🔒 Доступ только по ключу.\n\n/code ТВОЙ_КЛЮЧ")
        except Exception: pass


class BusinessHistoryMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, Message):
            return await handler(event, data)
        bc = getattr(event, "business_connection_id", None)
        if not bc or _is_dead_bc(bc):
            return await handler(event, data)
        user = event.from_user
        if not user:
            return await handler(event, data)
        try:
            chat_id = event.chat.id
            DATA.setdefault("business_chats", {})[str(chat_id)] = bc
            owner_id = BUSINESS_CONNECTIONS.get(bc, {}).get("user_id") or \
                       DATA.get("business_owners", {}).get(bc)
            is_out = (owner_id is not None and user.id == owner_id)
            text = event.text or event.caption or ""
            if text.startswith(".") or text.startswith("/"):
                text = ""
            if text:
                hist = DATA.setdefault("history", {}).setdefault(str(chat_id), [])
                hist.append({"from_id": user.id, "text": text[:400],
                             "ts": time.time(), "is_out": is_out})
                if len(hist) > HISTORY_LIMIT:
                    DATA["history"][str(chat_id)] = hist[-HISTORY_LIMIT:]
        except Exception as e:
            print(f"biz hist: {str(e)[:150]}", flush=True)
        return await handler(event, data)


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
    DATA["users"][uid] = entry
    await save_data_sync(DATA)
    print(f"[key] сохранил {key} -> uid={uid}", flush=True)
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


# ================== БАЗОВЫЕ КОМАНДЫ ==================
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    kb = owner_reply_kb() if is_owner(message.from_user) else None
    await message.answer(
        "👋 Привет!\n\n"
        "🔑 <code>/code КЛЮЧ</code> — активировать доступ\n"
        "📖 /help — список команд\n"
        "ℹ️ /mykey — остаток ключа\n"
        "❌ /cancel — отмена\n\n"
        "🎮 .ttt · .uno · .rl · /mafia · .mute · .tts · .switch",
        reply_markup=kb,
    )


@dp.message(F.text.func(lambda t: _cmd(t, *HELP_VARIANTS)))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Команды</b>\n\n"
        "🎮 <code>.ttt</code> — крестики-нолики\n"
        "🎴 <code>.uno</code> — Уно против бота (ЛС)\n"
        "🎴 <code>/uno</code> — Уно в группе\n"
        "🎴 <code>/uno notimer</code> — лобби без таймера\n"
        "🔫 <code>/mafia</code> — Мафия (группа)\n"
        "🎲 <code>.rl</code> — рулетка\n"
        "🔇 <code>.mute</code> (реплаем) — мут (владелец)\n"
        "🔊 <code>.tts текст</code> — озвучка\n"
        "🔁 <code>.switch</code> (реплаем) — сменить раскладку\n"
        "🎤 голосовое — расшифровка в текст\n"
        "🔑 <code>/code КЛЮЧ</code> — активировать ключ\n"
        "ℹ️ /mykey · /whoami · /cancel"
    )


@dp.message(F.text.startswith("/code"))
async def cmd_code(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ <code>/code ТВОЙ_КЛЮЧ</code>", parse_mode="HTML")
        return
    await try_activate_key(message, message.from_user, parts[1].strip().upper())


@dp.message(F.text == "/mykey")
async def cmd_mykey(message: Message):
    u = DATA["users"].get(str(message.from_user.id))
    if not u:
        await message.answer("🔒 Нет ключа. /code КЛЮЧ"); return
    if u.get("permanent"):
        await message.answer("♾ Ключ навсегда"); return
    rem = max(0, u.get("expires_at", 0) - time.time())
    await message.answer(f"⏳ Осталось: {format_duration(rem)}")


@dp.message(F.text == "/whoami")
async def cmd_whoami(message: Message):
    u = message.from_user
    st = user_status(u.id)
    await message.answer(
        f"🆔 <code>{u.id}</code>\n"
        f"👤 @{u.username or '—'}\n"
        f"🔑 Статус: <b>{st}</b>\n"
        f"👑 Владелец: {'да' if is_owner(u) else 'нет'}"
    )


@dp.message(F.text == "/cancel")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Отменено.")


@dp.message(lambda m: m.text is not None and BTN_CREATE_KEY in m.text)
async def btn_create_key(message: Message):
    if not is_owner(message.from_user): return
    await _show_create_key_menu(message)


@dp.message(lambda m: m.text is not None and BTN_KEYS_STATUS in m.text)
async def btn_keys_status(message: Message):
    if not is_owner(message.from_user): return
    await _send_keys_list(message)


@dp.message(F.text == "/newkey")
async def cmd_newkey(message: Message):
    if not is_owner(message.from_user): return
    await _show_create_key_menu(message)


@dp.callback_query(F.data == "kc:perm")
async def cb_kc_perm(cb: CallbackQuery):
    if not is_owner(cb.from_user): await cb.answer("Не твоя", show_alert=True); return
    key = _generate_key()
    DATA["keys"][key] = {"permanent": True, "duration": 0, "created_at": time.time(), "used_by": None}
    await save_data_sync(DATA)
    await cb.message.edit_text(f"♾ <b>Ключ:</b> <code>{key}</code>", parse_mode="HTML")
    await cb.answer("Готово")


@dp.callback_query(F.data == "kc:temp")
async def cb_kc_temp(cb: CallbackQuery, state: FSMContext):
    if not is_owner(cb.from_user): await cb.answer("Не твоя", show_alert=True); return
    await cb.message.edit_text("⏳ Формат: <code>1d 2h 30m</code>", parse_mode="HTML")
    await state.set_state(BotStates.waiting_for_duration); await cb.answer()


@dp.message(BotStates.waiting_for_duration)
async def process_duration(message: Message, state: FSMContext):
    try:
        if not is_owner(message.from_user): return
        dur = parse_duration(message.text or "")
        if not dur:
            await message.answer("🤔 Пример: <code>1d 2h 30m</code>", parse_mode="HTML"); return
        key = _generate_key()
        DATA["keys"][key] = {"permanent": False, "duration": dur,
                             "created_at": time.time(), "used_by": None}
        await save_data_sync(DATA)
        await message.answer(f"⏳ <b>Ключ:</b> <code>{key}</code>\n⏱ {format_duration(dur)}",
                             parse_mode="HTML")
    finally:
        await state.clear()


@dp.callback_query(F.data.startswith("kdel:"))
async def cb_key_delete(cb: CallbackQuery):
    if not is_owner(cb.from_user): await cb.answer("Не твоя", show_alert=True); return
    code = cb.data.split(":", 1)[1].strip().upper()
    _revoke_by_keycode(code); await save_data_sync(DATA)
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
        if info: await save_data_sync(DATA); await message.answer("✅ Отозван.")
        return
    if not arg: return
    arg = arg.strip()
    info = _revoke_by_keycode(arg)
    if info: await save_data_sync(DATA); await message.answer("✅ Ключ удалён."); return
    if arg.isdigit():
        info = _revoke_by_user_id(arg)
        if info: await save_data_sync(DATA); await message.answer("✅ Отозван."); return


# ================== РУЛЕТКА ==================
@dp.message(F.text.func(lambda t: _cmd(t, *RL_VARIANTS)))
async def cmd_ruletka(message: Message):
    print(f"[rl] HIT text={message.text!r} biz={bool(message.business_connection_id)}", flush=True)
    await _try_delete_command(message)
    win = random.random() < 5/6
    await message.answer(random.choice(RULETKA_WIN if win else RULETKA_LOSE))


# ================== ГОЛОСОВЫЕ ==================
@dp.message(F.voice)
async def process_voice(message: Message, state: FSMContext):
    await state.set_state(None)
    await _handle_stt(message, message.voice.file_id, ".ogg")


@dp.message(F.video_note)
async def process_video_note(message: Message, state: FSMContext):
    await state.set_state(None)
    await _handle_stt(message, message.video_note.file_id, ".mp4")


# ================== SWITCH / TTS ==================
@dp.message(F.text.func(lambda t: _cmd(t, *SWITCH_VARIANTS)))
async def cmd_switch(message: Message):
    if message.chat.type in ("group", "supergroup", "channel"): return
    await _try_delete_command(message)
    if not message.reply_to_message:
        await message.answer("⚠️ Ответь на сообщение.", parse_mode="HTML"); return
    src = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not src.strip():
        await message.answer("⚠️ Нет текста."); return
    conv, dr = _convert_layout(src)
    if not dr or conv == src:
        await message.answer("🤷 Нечего менять."); return
    await message.answer(f"🔁 <b>Исправлено:</b>\n\n{html_mod.escape(conv)}",
                          parse_mode="HTML",
                          reply_to_message_id=message.reply_to_message.message_id)


@dp.message(F.text.func(lambda t: _cmd(t, *TTS_VARIANTS)))
async def cmd_tts(message: Message):
    if message.chat.type in ("group", "supergroup", "channel"): return
    await _try_delete_command(message)
    if not HAS_GTTS:
        await message.answer("❌ gTTS не установлен."); return
    text = ""
    if message.reply_to_message:
        text = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
    else:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1: text = parts[1].strip()
    if not text:
        await message.answer("⚠️ <code>.tts текст</code>", parse_mode="HTML"); return
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


# ================== МУТ ==================
def _target_from_msg(message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    return None


@dp.message(F.text.func(lambda t: _cmd(t, *UNMUTE_VARIANTS)))
async def cmd_unmute(message: Message):
    await _try_delete_command(message)
    if not is_owner(message.from_user): return
    target = _target_from_msg(message)
    if not target: return
    ms = MUTED.get(message.chat.id, set())
    if target.id in ms:
        ms.discard(target.id)
        await message.answer("Так и быть, говори.")


@dp.message(F.text.func(lambda t: _cmd(t, *MUTE_VARIANTS)))
async def cmd_mute(message: Message):
    print(f"[mute] HIT text={message.text!r} owner={is_owner(message.from_user)}", flush=True)
    await _try_delete_command(message)
    if not is_owner(message.from_user): return
    target = _target_from_msg(message)
    if not target:
        await message.answer("Реплаем <code>.mute</code>", parse_mode="HTML"); return
    if target.id == message.from_user.id or is_owner(target): return
    MUTED.setdefault(message.chat.id, set()).add(target.id)
    name = target.full_name or str(target.id)
    await message.answer(f"🔇 <b>МОЛЧАТЬ!!!</b> {html_mod.escape(name)}", parse_mode="HTML")


# ================== ШЁПОТ ==================
def _next_whisper_id():
    c = int(DATA.get("whisper_counter", 1)); DATA["whisper_counter"] = c + 1; return c


@dp.inline_query()
async def inline_whisper(query: InlineQuery):
    text = (query.query or "").strip()
    bot_uname = (await get_bot_username()).lower()
    if not text:
        result = InlineQueryResultArticle(id="h1", title="🤫 Напиши: текст @username",
            description="Например: привет @vimbrix",
            input_message_content=InputTextMessageContent(
                message_text=f"🤫 <code>@{bot_uname} текст @username</code>", parse_mode="HTML"))
        await query.answer([result], cache_time=0, is_personal=True); return
    mentions = [m for m in re.finditer(r"@(\w+)", text) if m.group(1).lower() != bot_uname]
    if not mentions:
        result = InlineQueryResultArticle(id="h2", title="🤫 Добавь @username",
            description="Например: привет @vimbrix",
            input_message_content=InputTextMessageContent(
                message_text=f"🤫 <code>@{bot_uname} текст @username</code>", parse_mode="HTML"))
        await query.answer([result], cache_time=0, is_personal=True); return
    tm = mentions[-1]; tuser = tm.group(1); wtext = text[:tm.start()].strip()
    if not wtext:
        result = InlineQueryResultArticle(id="h3", title="🤫 Что шептать?",
            description="Напиши текст",
            input_message_content=InputTextMessageContent(message_text="🤫 Добавь текст."))
        await query.answer([result], cache_time=0, is_personal=True); return
    tid = None
    for uid, ud in DATA["users"].items():
        if (ud.get("username") or "").lower() == tuser.lower(): tid = int(uid); break
    wid = _next_whisper_id()
    DATA["whispers"][str(wid)] = {"target_id": tid, "target_name": tuser, "text": wtext,
        "from_id": query.from_user.id, "from_name": query.from_user.full_name,
        "expires": time.time() + WHISPER_TTL}
    save_data(DATA)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Прочитать", callback_data=f"whisper:{wid}")]])
    result = InlineQueryResultArticle(id=str(wid), title=f"🤫 Шёпот для @{tuser}",
        description=wtext[:60],
        input_message_content=InputTextMessageContent(message_text=f"🔒 Секретное для @{tuser}"),
        reply_markup=kb)
    await query.answer([result], cache_time=0, is_personal=True)


@dp.callback_query(F.data.startswith("whisper:"))
async def cb_whisper(cb: CallbackQuery):
    try: wid = int(cb.data.split(":", 1)[1])
    except Exception: await cb.answer("Сломался", show_alert=True); return
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
    else:
        await cb.answer(random.choice(FUNNY_REPLIES), show_alert=True)


# ================== КРЕСТИКИ-НОЛИКИ ==================
TTT_WIN_LINES = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]


def _ttt_text(game):
    board = game["board"]
    def cell(i):
        v = board[i]
        return "❌" if v == "X" else ("⭕" if v == "O" else "▫️")
    grid = f"{cell(0)} {cell(1)} {cell(2)}\n{cell(3)} {cell(4)} {cell(5)}\n{cell(6)} {cell(7)} {cell(8)}"
    footer = game.get("footer", "") if game.get("finished") else \
             f"Ход: {'❌' if game['turn']=='x' else '⭕'}"
    x_name = game.get("names", {}).get("x") or "?"
    o_name = game.get("names", {}).get("o") or "ждём..."
    return (f"❌⭕ <b>Крестики-нолики</b>\n\n"
            f"❌ {html_mod.escape(x_name)}\n"
            f"⭕ {html_mod.escape(o_name)}\n\n{grid}\n\n{footer}")


def _ttt_kb(game):
    board = game["board"]; b = InlineKeyboardBuilder()
    if not game.get("o_id") and not game.get("finished"):
        b.button(text="🙋 Присоединиться", callback_data="ttt_join"); return b.as_markup()
    row = []
    for i in range(9):
        if board[i] is None and not game.get("finished"):
            row.append(InlineKeyboardButton(text="⬜", callback_data=f"ttt_move:{i}"))
        else:
            row.append(InlineKeyboardButton(
                text="❌" if board[i]=="X" else ("⭕" if board[i]=="O" else "⬜"),
                callback_data="ttt_noop"))
        if len(row) == 3: b.row(*row); row = []
    b.row(InlineKeyboardButton(text="🏳️ Сдаться", callback_data="ttt_reset"))
    return b.as_markup()


@dp.message(F.text.func(lambda t: _cmd(t, *TTT_VARIANTS)))
async def cmd_ttt(message: Message, state: FSMContext):
    print(f"[ttt] HIT text={message.text!r} chat={message.chat.id} biz={bool(message.business_connection_id)}",
          flush=True)
    await state.clear()
    await _try_delete_command(message)
    chat_id = message.chat.id
    if chat_id in TTT_GAMES and not TTT_GAMES[chat_id].get("finished"):
        await message.answer("⚠️ Игра уже идёт."); return
    u = message.from_user
    name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    TTT_GAMES[chat_id] = {"board": [None]*9, "x_id": u.id, "o_id": None, "turn": "x",
                          "names": {"x": name, "o": None}, "finished": False, "footer": "",
                          "bc_id": message.business_connection_id}
    await message.answer(_ttt_text(TTT_GAMES[chat_id]),
                         reply_markup=_ttt_kb(TTT_GAMES[chat_id]), parse_mode="HTML")


@dp.callback_query(F.data == "ttt_join")
async def cb_ttt_join(cb: CallbackQuery):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game: await cb.answer("Не найдена", show_alert=True); return
    if game["finished"] or game.get("o_id"): await cb.answer("Занято", show_alert=True); return
    if cb.from_user.id == game["x_id"]: await cb.answer("Ты уже ❌", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["o_id"] = u.id; game["names"]["o"] = name
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=cb.message.message_id,
                                     text=_ttt_text(game), reply_markup=_ttt_kb(game),
                                     parse_mode="HTML")
    except Exception: pass
    await cb.answer("Погнали!")


@dp.callback_query(F.data.startswith("ttt_move:"))
async def cb_ttt_move(cb: CallbackQuery):
    chat_id = cb.message.chat.id; game = TTT_GAMES.get(chat_id)
    if not game or game["finished"]: await cb.answer("Недоступно", show_alert=True); return
    if not game.get("o_id"): await cb.answer("Ждём игрока", show_alert=True); return
    turn = game["turn"]; expected = game["x_id"] if turn == "x" else game["o_id"]
    if cb.from_user.id != expected: await cb.answer("Не твой ход", show_alert=True); return
    try: idx = int(cb.data.split(":", 1)[1])
    except Exception: await cb.answer("Ошибка", show_alert=True); return
    if idx < 0 or idx > 8 or game["board"][idx] is not None:
        await cb.answer("Занято", show_alert=True); return
    game["board"][idx] = "X" if turn == "x" else "O"
    w = None
    for a, b2, c in TTT_WIN_LINES:
        if game["board"][a] and game["board"][a] == game["board"][b2] == game["board"][c]:
            w = game["board"][a]; break
    if not w and all(c is not None for c in game["board"]): w = "draw"
    if w == "X":
        game["finished"] = True
        game["footer"] = f"🏆 Победа! ❌ {html_mod.escape(game['names']['x'] or '?')}"
    elif w == "O":
        game["finished"] = True
        game["footer"] = f"🏆 Победа! ⭕ {html_mod.escape(game['names']['o'] or '?')}"
    elif w == "draw":
        game["finished"] = True; game["footer"] = "🤝 Ничья!"
    else:
        game["turn"] = "o" if turn == "x" else "x"
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=cb.message.message_id,
                                     text=_ttt_text(game), reply_markup=_ttt_kb(game),
                                     parse_mode="HTML")
    except Exception: pass
    await cb.answer()


@dp.callback_query(F.data == "ttt_reset")
async def cb_ttt_reset(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    if chat_id in TTT_GAMES: TTT_GAMES.pop(chat_id, None)
    try: await cb.message.delete()
    except Exception: pass
    await cb.answer("Отменено")


@dp.callback_query(F.data == "ttt_noop")
async def cb_ttt_noop(cb: CallbackQuery): await cb.answer()


# ================== УНО ==================
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
    lines = ["🎴 <b>Уно</b>\n", f"🎯 Верхняя: {_uno_label(top)}",
             f"🎨 Цвет: {cur} {COLOR_NAMES.get(cur,'')}\n", "👥 Игроки:"]
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
    counts = []
    for p in game["players"]:
        cnt = len(game["hands"].get(str(p["id"]), []))
        mark = "👈 " if p["id"] == game["players"][game["turn_idx"]]["id"] else ""
        bot_tag = " 🤖" if p.get("is_bot") else ""
        counts.append(f"{mark}{html_mod.escape(p['name'])}{bot_tag}: <b>{cnt}</b>")
    counts_line = " | ".join(counts)
    txt = (f"🎯 Верхняя: {_uno_label(top)}\n"
           f"🎨 Цвет: {cur} {COLOR_NAMES.get(cur,'')}\n\n"
           f"👥 Счёт: {counts_line}\n\n")
    if is_turn: txt += "✅ <b>Твой ход!</b>\n\n"
    else: txt += f"⏳ Ждём: <b>{html_mod.escape(game['players'][game['turn_idx']]['name'])}</b>\n\n"
    txt += f"🃏 <b>Твоя рука ({len(hand)}):</b>\n"
    return txt


def _uno_dm_kb(game, uid):
    hand = game["hands"].get(str(uid), [])
    top = game["top"]; cur = game["current_color"]
    is_turn = game["players"][game["turn_idx"]]["id"] == uid
    b = InlineKeyboardBuilder(); row = []
    for i, c in enumerate(hand):
        ok = _uno_can(c, top, cur) and is_turn
        row.append(InlineKeyboardButton(text=_uno_label(c),
                                         callback_data=f"uno_play:{i}" if ok else "uno_noop"))
        if len(row) == 4: b.row(*row); row = []
    if row: b.row(*row)
    if is_turn: b.row(InlineKeyboardButton(text="🃏 Взять карту", callback_data="uno_dm_draw"))
    return b.as_markup()


async def _uno_send_dm(uid, game):
    try:
        await bot.send_message(uid, _uno_dm_text(game, uid),
                               reply_markup=_uno_dm_kb(game, uid), parse_mode="HTML")
    except Exception as e:
        print(f"[uno dm {uid}] {str(e)[:120]}", flush=True)


async def _uno_refresh_group(game, chat_id):
    text = _uno_group_text(game); kb = _uno_group_kb()
    msg_id = game.get("group_msg_id")
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                     reply_markup=kb, parse_mode="HTML")
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
        try: await bot.send_message(target, f"🏆 <b>{html_mod.escape(winner)} выиграл!</b>",
                                     parse_mode="HTML")
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
        try:
            await bot.send_message(target,
                f"🤖 <b>Бот взял карту.</b>\nТеперь у бота <b>{len(hand)}</b> карт.",
                parse_mode="HTML")
        except Exception: pass
        for p in game["players"]:
            if p.get("is_bot"): continue
            await _uno_send_dm(p["id"], game)
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
            s = (100 if c["type"]=="wild4" else 80 if c["type"]=="draw2" else
                 60 if c["type"]=="skip" else 50 if c["type"]=="reverse" else
                 40 if c["type"]=="wild" else 10 + cc.get(c["color"], 0))
            score.append((s, i, c))
        score.sort(reverse=True); pick = (score[0][1], score[0][2])
    idx, card = pick
    hand.pop(idx); game["top"] = card
    if card["color"] == "🌈":
        cc = {}
        for c in hand:
            if c["color"] != "🌈": cc[c["color"]] = cc.get(c["color"], 0) + 1
        game["current_color"] = max(cc, key=cc.get) if (diff == "hard" and cc) else random.choice(UNO_COLORS)
    else:
        game["current_color"] = card["color"]
    try:
        sfx = f" → цвет <b>{game['current_color']}</b>" if card["color"] == "🌈" else ""
        left = len(hand)
        uno_warn = " 📣 <b>УНО!</b>" if left == 1 else ""
        await bot.send_message(target,
            f"🤖 <b>Бот сбросил {_uno_label(card)}</b>{sfx}\nУ бота осталось <b>{left}</b> карт.{uno_warn}",
            parse_mode="HTML")
    except Exception: pass
    await _uno_after(game, chat_id, 0, card)


@dp.message(F.text.func(lambda t: _cmd(t, *UNO_VARIANTS)))
async def cmd_uno_dot(message: Message, state: FSMContext):
    print(f"[uno] HIT text={message.text!r} type={message.chat.type} biz={bool(message.business_connection_id)}",
          flush=True)
    await state.clear()
    if message.chat.type != "private":
        return
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
async def cb_uno_diff(cb: CallbackQuery):
    d = cb.data.split(":", 1)[1]
    if d not in DIFF_LABELS: await cb.answer("Ошибка", show_alert=True); return
    _uno_set_diff(cb.from_user.id, d)
    b = InlineKeyboardBuilder()
    for k, label in DIFF_LABELS.items():
        mark = "✅ " if k == d else ""
        b.button(text=f"{mark}{label}", callback_data=f"uno_diff:{k}")
    b.row(InlineKeyboardButton(text="🚀 Начать", callback_data="uno_start_ls"))
    b.adjust(1)
    try:
        await cb.message.edit_text(f"🎴 <b>Уно против бота</b>\n\nСложность: <b>{DIFF_LABELS[d]}</b>",
                                    reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception: pass
    await cb.answer(DIFF_LABELS[d])


@dp.callback_query(F.data == "uno_start_ls")
async def cb_uno_start_ls(cb: CallbackQuery):
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
    if players:
        lines.append(f"👥 ({len(players)}): " + ", ".join(html_mod.escape(p["name"]) for p in players))
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
                                         text=_uno_lobby_text(game),
                                         reply_markup=b.as_markup(), parse_mode="HTML")
        except Exception: pass
        if game["time_left"] <= 0:
            try: await bot.send_message(chat_id, "⏱ Время вышло. Жду <code>/play</code>.",
                                         parse_mode="HTML")
            except Exception: pass
            return


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith(("/uno", "/мафия", "/mafia"))))
async def cmd_uno_mafia_group(message: Message, state: FSMContext):
    if message.chat.type == "private": return
    txt = (message.text or "").strip().lower()
    if txt.startswith("/uno"):
        await state.clear()
        await _try_delete_command(message)
        chat_id = message.chat.id
        if chat_id in UNO_GAMES and UNO_GAMES[chat_id].get("started"):
            await message.answer("⚠️ Игра уже идёт."); return
        args = (message.text or "").split()
        no_timer = any(a.lower() == "notimer" for a in args[1:])
        u = message.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
        UNO_GAMES[chat_id] = {"mode": "group", "players": [{"id": u.id, "name": name}],
                              "bc_id": message.business_connection_id, "no_timer": no_timer,
                              "time_left": UNO_LOBBY_TIMEOUT, "started": False,
                              "deck": [], "hands": {}}
        game = UNO_GAMES[chat_id]
        b = InlineKeyboardBuilder(); b.button(text="🙋 Присоединиться", callback_data="uno_join")
        msg = await message.answer(_uno_lobby_text(game), reply_markup=b.as_markup(), parse_mode="HTML")
        game["lobby_msg_id"] = msg.message_id
        if not no_timer: asyncio.create_task(_uno_lobby_tick(chat_id))


@dp.callback_query(F.data == "uno_join")
async def cb_uno_join(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or game.get("started"): await cb.answer("Игра идёт", show_alert=True); return
    uid = cb.from_user.id
    if any(p["id"] == uid for p in game["players"]):
        await cb.answer("Ты уже тут", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["players"].append({"id": uid, "name": name})
    b = InlineKeyboardBuilder(); b.button(text="🙋 Присоединиться", callback_data="uno_join")
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                     text=_uno_lobby_text(game), reply_markup=b.as_markup(),
                                     parse_mode="HTML")
    except Exception: pass
    await cb.answer("Зашёл!")


@dp.message(F.text == "/play")
async def cmd_play(message: Message, state: FSMContext):
    await state.clear()
    await _try_delete_command(message)
    chat_id = message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game or game.get("started"):
        await message.answer("Нет активного лобби. Сначала <code>/uno</code>.", parse_mode="HTML"); return
    creator = game["players"][0]["id"] if game.get("players") else None
    if not (is_owner(message.from_user) or message.from_user.id == creator):
        await message.answer("Стартовать может только создатель лобби или владелец."); return
    if len(game["players"]) < 2:
        await message.answer("⚠️ Нужно ≥2 игрока."); return
    _uno_start_round(game)
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
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
async def cb_uno_play(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = UNO_GAMES.get(chat_id)
    if not game:
        for cid, g in UNO_GAMES.items():
            if g.get("mode") == "ls" and g.get("player_uid") == cb.from_user.id:
                chat_id = cid; game = g; break
    if not game: await cb.answer("Нет игры", show_alert=True); return
    try: idx = int(cb.data.split(":", 1)[1])
    except Exception: await cb.answer("Ошибка", show_alert=True); return
    uid = cb.from_user.id
    if game["players"][game["turn_idx"]]["id"] != uid:
        await cb.answer("Не твой ход", show_alert=True); return
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
async def cb_uno_color(cb: CallbackQuery):
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
async def cb_uno_dm_draw(cb: CallbackQuery):
    uid = cb.from_user.id
    target = next(((cid, g) for cid, g in UNO_GAMES.items()
                   if (g["mode"] == "ls" and g.get("player_uid") == uid) or
                      (g["mode"] == "group" and uid in [p["id"] for p in g["players"]])), None)
    if not target: await cb.answer("Нет игры", show_alert=True); return
    chat_id, game = target
    if game["players"][game["turn_idx"]]["id"] != uid:
        await cb.answer("Не твой ход", show_alert=True); return
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
    if cur.get("is_bot"):
        await asyncio.sleep(1.5); await _uno_bot(game, chat_id)


@dp.callback_query(F.data == "uno_grp_draw")
async def cb_uno_grp_draw(cb: CallbackQuery):
    chat_id = cb.message.chat.id; game = UNO_GAMES.get(chat_id)
    if not game: await cb.answer("Нет игры", show_alert=True); return
    uid = cb.from_user.id
    if game["players"][game["turn_idx"]]["id"] != uid:
        await cb.answer("Не твой ход", show_alert=True); return
    if not game["deck"]: await cb.answer("Колода пуста", show_alert=True); return
    card = game["deck"].pop(); game["hands"][str(uid)].append(card)
    game["turn_idx"] = (game["turn_idx"] + game["direction"]) % len(game["players"])
    await cb.answer(f"Взял {_uno_label(card)}")
    try: await bot.send_message(uid, f"🃏 Взял: {_uno_label(card)}")
    except Exception: pass
    await _uno_refresh_group(game, chat_id)


@dp.callback_query(F.data == "uno_shout")
async def cb_uno_shout(cb: CallbackQuery):
    chat_id = cb.message.chat.id; game = UNO_GAMES.get(chat_id)
    if not game: await cb.answer("Нет игры", show_alert=True); return
    uid = cb.from_user.id
    hand = game["hands"].get(str(uid), [])
    if len(hand) == 1:
        name = next((p["name"] for p in game["players"] if p["id"] == uid), "?")
        try: await bot.send_message(chat_id, f"📣 <b>{html_mod.escape(name)}</b> крикнул УНО!",
                                     parse_mode="HTML")
        except Exception: pass
        await cb.answer("📣")
    elif len(hand) > 1: await cb.answer("Уно только с 1 картой!", show_alert=True)
    else: await cb.answer("Ты уже выиграл", show_alert=True)


@dp.callback_query(F.data == "uno_surrender")
async def cb_uno_surrender(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    if chat_id in UNO_GAMES:
        UNO_GAMES.pop(chat_id, None)
        try: await bot.send_message(chat_id, "🏳️ Отменено.")
        except Exception: pass
    await cb.answer()


@dp.callback_query(F.data == "uno_noop")
async def cb_uno_noop(cb: CallbackQuery): await cb.answer()


# ================== МАФИЯ ==================
ROLES = {"mafia": "🔫 Мафия", "doctor": "💊 Доктор", "detective": "🔍 Комиссар", "civilian": "👤 Мирный"}
MAFIA_MIN = 4


def _mafia_assign(players):
    n = len(players)
    mafia_count = max(1, n // 4)
    roles = ["mafia"] * mafia_count + ["doctor", "detective"] + ["civilian"] * (n - mafia_count - 2)
    random.shuffle(roles)
    for p, r in zip(players, roles): p["role"] = r


def _mafia_lobby_text(game):
    players = game["players"]
    lines = ["🔫 <b>Набор в Мафию</b>\n"]
    if players:
        lines.append(f"👥 ({len(players)}): " + ", ".join(html_mod.escape(p["name"]) for p in players))
    lines.append(f"\n⚠️ Нужно минимум {MAFIA_MIN} (сейчас {len(players)})")
    if game.get("no_timer"): lines.append("\n⏸ Таймер отключён. Старт: <code>/play</code>")
    else: lines.append(f"\n⏱ Старт через <b>{game.get('time_left', MAFIA_LOBBY_TIMEOUT)}</b> сек.")
    return "\n".join(lines)


async def _mafia_lobby_tick(chat_id):
    while True:
        await asyncio.sleep(UNO_LOBBY_TICK)
        game = MAFIA_GAMES.get(chat_id)
        if not game or game.get("state") != "lobby": return
        if game.get("no_timer"): continue
        game["time_left"] = max(0, game.get("time_left", MAFIA_LOBBY_TIMEOUT) - UNO_LOBBY_TICK)
        b = InlineKeyboardBuilder(); b.button(text="🙋 Присоединиться", callback_data="mafia_join")
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                         text=_mafia_lobby_text(game),
                                         reply_markup=b.as_markup(), parse_mode="HTML")
        except Exception: pass
        if game["time_left"] <= 0:
            try: await bot.send_message(chat_id, "⏱ Время вышло. Жду <code>/play</code>.",
                                         parse_mode="HTML")
            except Exception: pass
            return


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith(("/mafia", "/мафия"))))
async def cmd_mafia(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type == "private":
        await message.answer("🔫 Мафия только в группах."); return
    await _try_delete_command(message)
    chat_id = message.chat.id
    if chat_id in MAFIA_GAMES and MAFIA_GAMES[chat_id].get("state") not in ("lobby", "finished"):
        await message.answer("⚠️ Игра уже идёт."); return
    args = (message.text or "").split()
    no_timer = any(a.lower() == "notimer" for a in args[1:])
    u = message.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    MAFIA_GAMES[chat_id] = {"players": [{"id": u.id, "name": name, "alive": True, "role": None}],
                            "bc_id": message.business_connection_id, "no_timer": no_timer,
                            "time_left": MAFIA_LOBBY_TIMEOUT, "state": "lobby", "day": 0}
    game = MAFIA_GAMES[chat_id]
    b = InlineKeyboardBuilder(); b.button(text="🙋 Присоединиться", callback_data="mafia_join")
    msg = await message.answer(_mafia_lobby_text(game), reply_markup=b.as_markup(), parse_mode="HTML")
    game["lobby_msg_id"] = msg.message_id
    if not no_timer: asyncio.create_task(_mafia_lobby_tick(chat_id))


@dp.callback_query(F.data == "mafia_join")
async def cb_mafia_join(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") != "lobby":
        await cb.answer("Лобби закрыто", show_alert=True); return
    uid = cb.from_user.id
    if any(p["id"] == uid for p in game["players"]):
        await cb.answer("Ты уже тут", show_alert=True); return
    if len(game["players"]) >= 10: await cb.answer("Максимум 10", show_alert=True); return
    u = cb.from_user; name = u.full_name or (f"@{u.username}" if u.username else "Игрок")
    game["players"].append({"id": uid, "name": name, "alive": True, "role": None})
    b = InlineKeyboardBuilder(); b.button(text="🙋 Присоединиться", callback_data="mafia_join")
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                     text=_mafia_lobby_text(game), reply_markup=b.as_markup(),
                                     parse_mode="HTML")
    except Exception: pass
    await cb.answer("Зашёл!")


async def _mafia_start(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game: return
    game["state"] = "starting"; game["day"] = 1
    _mafia_assign(game["players"])
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=game["lobby_msg_id"],
                                     text="🔫 <b>Мафия началась!</b>", parse_mode="HTML")
    except Exception: pass
    for p in game["players"]:
        try:
            await bot.send_message(p["id"], f"🔫 Твоя роль: <b>{ROLES[p['role']]}</b>\n\nИгра началась!",
                                    parse_mode="HTML")
        except Exception:
            try: await bot.send_message(chat_id, f"⚠️ {html_mod.escape(p['name'])} — напиши боту /start в ЛС.")
            except Exception: pass
    await asyncio.sleep(2)
    await _mafia_night(chat_id)


async def _mafia_night(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") in ("finished", "cancelled"): return
    game["state"] = "night"
    game["night_actions"] = {"mafia": None, "doctor": None, "detective": None}
    alive = [p for p in game["players"] if p["alive"]]
    txt = f"🌙 <b>НОЧЬ {game['day']}</b>\n\nЖивые:\n" + "\n".join(f"• {html_mod.escape(p['name'])}" for p in alive)
    txt += "\n\nВсе спят. Мафия, доктор и комиссар — проверьте ЛС."
    try: await bot.send_message(chat_id, txt, parse_mode="HTML")
    except Exception: pass
    for p in alive:
        try:
            if p["role"] == "mafia":
                others = [x for x in alive if x["role"] != "mafia"]
                b = InlineKeyboardBuilder()
                for x in others: b.button(text=html_mod.escape(x["name"]),
                                            callback_data=f"mafia_kill:{x['id']}")
                b.adjust(2)
                await bot.send_message(p["id"], "🔫 <b>Ты мафия.</b> Кого убить?",
                                        reply_markup=b.as_markup(), parse_mode="HTML")
            elif p["role"] == "doctor":
                b = InlineKeyboardBuilder()
                for x in alive: b.button(text=html_mod.escape(x["name"]),
                                           callback_data=f"mafia_save:{x['id']}")
                b.adjust(2)
                await bot.send_message(p["id"], "💊 <b>Ты доктор.</b> Кого спасти?",
                                        reply_markup=b.as_markup(), parse_mode="HTML")
            elif p["role"] == "detective":
                others = [x for x in alive if x["id"] != p["id"]]
                b = InlineKeyboardBuilder()
                for x in others: b.button(text=html_mod.escape(x["name"]),
                                            callback_data=f"mafia_check:{x['id']}")
                b.adjust(2)
                await bot.send_message(p["id"], "🔍 <b>Ты комиссар.</b> Кого проверить?",
                                        reply_markup=b.as_markup(), parse_mode="HTML")
        except Exception: pass
    asyncio.create_task(_mafia_night_timeout(chat_id))


async def _mafia_night_timeout(chat_id):
    await asyncio.sleep(60)
    await _mafia_end_night(chat_id)


@dp.callback_query(F.data.startswith("mafia_kill:"))
async def cb_mafia_kill(cb: CallbackQuery):
    uid = cb.from_user.id
    try: target = int(cb.data.split(":", 1)[1])
    except Exception: await cb.answer("Ошибка", show_alert=True); return
    for cid, g in MAFIA_GAMES.items():
        if g.get("state") != "night": continue
        for p in g["players"]:
            if p["id"] == uid and p["role"] == "mafia":
                g["night_actions"]["mafia"] = target
                await cb.answer("Выбрано"); return
    await cb.answer("Не твоя кнопка", show_alert=True)


@dp.callback_query(F.data.startswith("mafia_save:"))
async def cb_mafia_save(cb: CallbackQuery):
    uid = cb.from_user.id
    try: target = int(cb.data.split(":", 1)[1])
    except Exception: await cb.answer("Ошибка", show_alert=True); return
    for cid, g in MAFIA_GAMES.items():
        if g.get("state") != "night": continue
        for p in g["players"]:
            if p["id"] == uid and p["role"] == "doctor":
                g["night_actions"]["doctor"] = target
                await cb.answer("Спасаешь"); return
    await cb.answer("Не твоя кнопка", show_alert=True)


@dp.callback_query(F.data.startswith("mafia_check:"))
async def cb_mafia_check(cb: CallbackQuery):
    uid = cb.from_user.id
    try: target = int(cb.data.split(":", 1)[1])
    except Exception: await cb.answer("Ошибка", show_alert=True); return
    for cid, g in MAFIA_GAMES.items():
        if g.get("state") != "night": continue
        for p in g["players"]:
            if p["id"] == uid and p["role"] == "detective":
                t = next((x for x in g["players"] if x["id"] == target), None)
                if t:
                    is_m = t["role"] == "mafia"
                    await cb.answer(f"{'🔫 МАФИЯ!' if is_m else '👤 Мирный'}", show_alert=True)
                return
    await cb.answer("Не твоя кнопка", show_alert=True)


def _mafia_check_end(game):
    alive = [p for p in game["players"] if p["alive"]]
    mafia = [p for p in alive if p["role"] == "mafia"]
    civs = [p for p in alive if p["role"] != "mafia"]
    if not mafia: return True, "👤 Победа мирных! Мафия мертва."
    if len(mafia) >= len(civs): return True, "🔫 Победа мафии!"
    return False, ""


async def _mafia_end_night(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") != "night": return
    acts = game["night_actions"]
    victim_id = acts.get("mafia"); saved_id = acts.get("doctor")
    killed_name = None
    if victim_id and victim_id != saved_id:
        victim = next((p for p in game["players"] if p["id"] == victim_id), None)
        if victim and victim["alive"]:
            victim["alive"] = False; killed_name = victim["name"]
    if killed_name:
        text = f"☀️ <b>ДЕНЬ {game['day']}</b>\n\n💀 Погиб <b>{html_mod.escape(killed_name)}</b>."
    else:
        text = f"☀️ <b>ДЕНЬ {game['day']}</b>\n\n🌅 Все выжили."
    end, msg = _mafia_check_end(game)
    if end:
        game["state"] = "finished"
        text += f"\n\n🏁 {msg}"
        try: await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception: pass
        MAFIA_GAMES.pop(chat_id, None); return
    try: await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception: pass
    await asyncio.sleep(2)
    await _mafia_vote(chat_id)


async def _mafia_vote(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") == "finished": return
    game["state"] = "vote"; game["votes"] = {}
    alive = [p for p in game["players"] if p["alive"]]
    b = InlineKeyboardBuilder()
    for p in alive: b.button(text=html_mod.escape(p["name"]), callback_data=f"mafia_vote:{p['id']}")
    b.adjust(2)
    txt = f"🗳 <b>Голосование дня {game['day']}</b>\n\nКого посадить? У вас 60 сек."
    try: await bot.send_message(chat_id, txt, reply_markup=b.as_markup(), parse_mode="HTML")
    except Exception: pass
    asyncio.create_task(_mafia_vote_timeout(chat_id))


async def _mafia_vote_timeout(chat_id):
    await asyncio.sleep(60)
    await _mafia_end_vote(chat_id)


@dp.callback_query(F.data.startswith("mafia_vote:"))
async def cb_mafia_vote(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") != "vote":
        await cb.answer("Не голосование", show_alert=True); return
    uid = cb.from_user.id
    if not any(p["id"] == uid and p["alive"] for p in game["players"]):
        await cb.answer("Ты не игрок", show_alert=True); return
    try: target = int(cb.data.split(":", 1)[1])
    except Exception: await cb.answer("Ошибка", show_alert=True); return
    game["votes"][uid] = target
    await cb.answer("Голос принят")


async def _mafia_end_vote(chat_id):
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("state") != "vote": return
    votes = game.get("votes", {})
    if not votes:
        text = "🗳 Никто не голосовал."
    else:
        counter = {}
        for t in votes.values(): counter[t] = counter.get(t, 0) + 1
        max_v = max(counter.values())
        top = [k for k, v in counter.items() if v == max_v]
        if len(top) > 1: text = "🗳 Ничья. Никто не посажен."
        else:
            voted_id = top[0]
            vp = next((p for p in game["players"] if p["id"] == voted_id), None)
            if vp:
                vp["alive"] = False
                text = f"⚖️ <b>{html_mod.escape(vp['name'])}</b> посажен.\nОн был: {ROLES[vp['role']]}"
            else: text = "🗳 Ошибка."
    end, msg = _mafia_check_end(game)
    if end:
        text += f"\n\n🏁 {msg}"
        try: await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception: pass
        MAFIA_GAMES.pop(chat_id, None); return
    try: await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception: pass
    await asyncio.sleep(2)
    game["day"] += 1
    await _mafia_night(chat_id)


# ================== БИЗНЕС ==================
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    try:
        if connection.is_enabled:
            BUSINESS_CONNECTIONS[connection.id] = {"user_id": connection.user.id}
            DATA.setdefault("business_owners", {})[connection.id] = connection.user.id
            if connection.id in DATA.get("dead_bc", []):
                DATA["dead_bc"] = [x for x in DATA["dead_bc"] if x != connection.id]
            save_data(DATA); print(f"Business подключён: {connection.id}", flush=True)
        else:
            BUSINESS_CONNECTIONS.pop(connection.id, None)
            DATA.get("business_owners", {}).pop(connection.id, None)
            save_data(DATA)
    except Exception as e:
        print(f"business: {str(e)[:150]}", flush=True)


# ================== HTTP / ЗАПУСК ==================
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
    print(f"[HTTP] healthcheck на 0.0.0.0:{port}", flush=True)


async def keep_alive():
    while True:
        try:
            await asyncio.sleep(240)
            me = await bot.get_me()
            print(f"keep-alive: @{me.username}", flush=True)
        except Exception as e:
            print(f"keep-alive: {str(e)[:120]}", flush=True)


async def set_bot_commands():
    cmds = [
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="code", description="Активировать ключ"),
        BotCommand(command="mykey", description="Мой ключ"),
        BotCommand(command="whoami", description="Кто я"),
        BotCommand(command="uno", description="Уно"),
        BotCommand(command="mafia", description="Мафия"),
        BotCommand(command="play", description="Старт"),
        BotCommand(command="cancel", description="Отмена"),
    ]
    try:
        await bot.set_my_commands(cmds)
    except Exception as e:
        print(f"cmds: {e}", flush=True)


async def set_bot_menu():
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        print(f"menu: {e}", flush=True)


# ================== MIDDLEWARE И ЗЕРКАЛИРОВАНИЕ ==================
dp.message.outer_middleware(AccessMiddleware())
dp.business_message.outer_middleware(BusinessHistoryMiddleware())
dp.business_message.outer_middleware(AccessMiddleware())

# КРИТИЧНО: в бизнес-чате апдейты приходят как business_message, а не message.
# Переиспользуем те же HandlerObject — просто копируем список.
dp.business_message.handlers.extend(dp.message.handlers)
print(f"[mirror] скопировано хендлеров: {len(dp.message.handlers)}", flush=True)


async def main():
    global DATA
    asyncio.create_task(_run_http_server())
    await asyncio.sleep(0.5)
    DATA = await load_data_from_tg()
    print(f"keys: {len(DATA['keys'])}, users: {len(DATA['users'])}", flush=True)
    await set_bot_commands()
    await set_bot_menu()
    asyncio.create_task(keep_alive())
    print("Bot started", flush=True)
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("stopped", flush=True)
