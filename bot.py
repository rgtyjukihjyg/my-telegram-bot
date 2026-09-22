import os
import re
import json
import time
import asyncio
import secrets
import random
import itertools
import urllib.request
import urllib.parse
import subprocess
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import (
    Message, FSInputFile, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, InputSticker, BotCommand,
    MenuButtonCommands,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from PIL import Image
import yt_dlp
from static_ffmpeg import run as static_ffmpeg_run
import edge_tts

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("нет BOT_TOKEN")

OWNER_ID = 7752398574
OWNER_USERNAME = (os.environ.get("OWNER_USERNAME") or "vimbrix").lower().lstrip("@")

_env_id = os.environ.get("OWNER_ID")
if _env_id and _env_id.strip().isdigit():
    OWNER_ID = int(_env_id)

DATA_FILE = "data.json"
KEY_LENGTH = 12
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()

BOT_USERNAME_CACHE = None


class BotStates(StatesGroup):
    waiting_for_parts = State()
    waiting_for_media_type = State()
    waiting_for_cover = State()
    waiting_for_meta = State()
    waiting_for_duration = State()
    waiting_tts_text = State()
    waiting_sticker_name = State()
    waiting_sticker_title = State()
    waiting_sticker_first = State()
    waiting_sticker_add_photo = State()


os.makedirs("downloads", exist_ok=True)
os.makedirs("temp_photos", exist_ok=True)

WHISPERS = {}
_whisper_counter = itertools.count(1)

FUNNY_REPLIES = [
    "Ержан, фу, нельзя, место!",
    "Дорогая, не лезь, оно тебя сожрёт",
    "Тебя в детстве не учили не нажимать куда попало?",
    "Это не твоё. Отойди.",
    "Руки убрал!",
    "Не для тебя писали.",
    "Читать чужие шёпоты — плохая примета.",
    "Здесь пусто. Серьёзно. Уходи.",
    "Кыш.",
    "А тебе кто разрешил?",
    "Тут был шёпот. Был. Ключевое слово — был.",
    "Не суй нос в чужой шёпот.",
    "Любопытной Варваре на базаре нос оторвали.",
    "Иди своей дорогой, путник.",
]


TTS_VOICES = {
    "ru_m1": ("ru-RU-DmitryNeural", "🇷🇺 Дмитрий (муж)"),
    "ru_f1": ("ru-RU-SvetlanaNeural", "🇷🇺 Светлана (жен)"),
    "en_m1": ("en-US-GuyNeural", "🇺🇸 Guy (male)"),
    "en_f1": ("en-US-AriaNeural", "🇺🇸 Aria (female)"),
}


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                d.setdefault("keys", {})
                d.setdefault("users", {})
                d.setdefault("sticker_packs", {})
                return d
        except Exception as e:
            print(f"load_data: {e}")
    return {"keys": {}, "users": {}, "sticker_packs": {}}


def save_data(data):
    try:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        print(f"save_data: {e}")


DATA = load_data()


def is_owner(user) -> bool:
    if not user:
        return False
    if OWNER_ID is not None and user.id == OWNER_ID:
        return True
    return (user.username or "").lower() == OWNER_USERNAME


async def get_bot_username() -> str:
    global BOT_USERNAME_CACHE
    if BOT_USERNAME_CACHE is None:
        me = await bot.get_me()
        BOT_USERNAME_CACHE = me.username
    return BOT_USERNAME_CACHE


def generate_key():
    for _ in range(100):
        k = "".join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_LENGTH))
        if k not in DATA["keys"]:
            return k
    raise RuntimeError("key gen error")


def parse_duration(s):
    s = (s or "").lower().strip()
    total = 0
    matched = False
    for m in re.finditer(r"(\d+)\s*([dhms])", s):
        n = int(m.group(1))
        u = m.group(2)
        if u == "d":
            total += n * 86400
        elif u == "h":
            total += n * 3600
        elif u == "m":
            total += n * 60
        elif u == "s":
            total += n
        matched = True
    return total if matched and total > 0 else None


def format_duration(seconds):
    seconds = int(seconds)
    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, seconds = divmod(seconds, 60)
    parts = []
    if d:
        parts.append(f"{d} дн.")
    if h:
        parts.append(f"{h} ч.")
    if m:
        parts.append(f"{m} мин.")
    if not parts:
        parts.append(f"{seconds} сек.")
    return " ".join(parts)


def format_until(ts):
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


def user_status(user_id):
    u = DATA["users"].get(str(user_id))
    if not u:
        return "none"
    if u.get("permanent"):
        return "valid"
    if u.get("expires_at", 0) > time.time():
        return "valid"
    return "expired"


PASSTHROUGH_COMMANDS = {
    "/whoami", "/cancel", "/start", "/mykey", "/help", "/code",
    "/tts", "/stickers", "/whisper",
}


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, Message):
            return await handler(event, data)
        user = event.from_user
        if user is None:
            return await handler(event, data)
        text = (getattr(event, "text", None) or "").strip()
        cmd = text.split()[0].lower() if text else ""
        if cmd in PASSTHROUGH_COMMANDS:
            return await handler(event, data)
        if is_owner(user):
            return await handler(event, data)
        status = user_status(user.id)
        if status == "valid":
            return await handler(event, data)
        if status == "expired":
            DATA["users"].pop(str(user.id), None)
            save_data(DATA)
            await event.answer("⏰ Срок действия ключа истёк. Введи новый через /code.")
            return
        await event.answer(
            "🔒 Доступ только по ключу.\n"
            "Отправь: /code ТВОЙ_КЛЮЧ"
        )


async def try_activate_key(event, user, key):
    key_data = DATA["keys"].get(key)
    user_id = str(user.id)
    if not key_data:
        await event.answer("❌ Такого ключа нет. Проверь раскладку и регистр.")
        return
    used_by = key_data.get("used_by")
    if used_by is not None:
        if str(used_by) == user_id:
            u = DATA["users"].get(user_id, {})
            if u.get("permanent"):
                await event.answer("😉 Ты уже активировал этот ключ.")
            else:
                await event.answer(f"😉 Уже активирован. До {format_until(u.get('expires_at', 0))}")
        else:
            await event.answer("⚠️ Этот ключ уже занят другим аккаунтом.")
        return
    now = time.time()
    permanent = bool(key_data.get("permanent"))
    duration = int(key_data.get("duration", 0))
    key_data["used_by"] = user.id
    key_data["activated_at"] = now
    entry = {
        "username": user.username or "",
        "key": key,
        "permanent": permanent,
        "activated_at": now,
    }
    if not permanent:
        entry["expires_at"] = now + duration
    DATA["users"][user_id] = entry
    save_data(DATA)
    if permanent:
        await event.answer("✅ Готово! Ключ активирован навсегда.")
    else:
        await event.answer(
            f"✅ Ключ активирован на {format_duration(duration)}\n"
            f"📅 Действует до {format_until(now + duration)}"
        )


def _cleanup_folder(folder):
    if os.path.exists(folder):
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except Exception as e:
                print(f"{p}: {e}")


_cleanup_folder("downloads")
_cleanup_folder("temp_photos")
print("Временные папки очищены")

FFMPEG_EXE_PATH, FFPROBE_EXE_PATH = static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
FFMPEG_DIR = os.path.dirname(FFMPEG_EXE_PATH)
print(f"ffmpeg: {FFMPEG_EXE_PATH}")
print(f"Owner: @{OWNER_USERNAME}")


EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
    "\U0001F900-\U0001F9FF\U00002700-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U0001FA00-\U0001FAFF]+",
    flags=re.UNICODE,
)
DASH_RE = re.compile(r"\s*[-\u2013\u2014\u2212\u2015]\s*")


def clean_meta(s, max_len=64, strip_author=""):
    if not s:
        return ""
    s = str(s).strip()
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"#\S+", " ", s)
    s = re.sub(r"@\S+", " ", s)
    s = re.sub(r"\btiktok\b", " ", s, flags=re.IGNORECASE)
    s = EMOJI_RE.sub(" ", s)
    s = " ".join(s.split())
    if strip_author:
        a = strip_author.strip().lower()
        for _ in range(3):
            if s.lower().startswith(a):
                s = s[len(a):].lstrip(" -|.,:;")
            else:
                break
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
                if not c:
                    break
                f.write(c)
    return out_path


def tiktok_via_api(url, mode):
    api_url = f"https://tikwm.com/api/?url={urllib.parse.quote(url)}&hd=1"
    req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", errors="ignore"))
    if data.get("code") != 0:
        raise RuntimeError(f"TikTok API: {data.get('msg', 'unknown')}")
    d = data["data"]
    vid_id = d.get("id") or "tiktok"
    title = clean_meta(d.get("title") or "tiktok")
    author = (d.get("author") or {}).get("unique_id") or ""
    duration = d.get("duration")
    if author:
        title = clean_meta(d.get("title") or "tiktok", strip_author=author)
    if mode == "audio":
        media_url = d.get("music")
        if not media_url:
            raise RuntimeError("нет ссылки на аудио")
        tmp = f"downloads/{vid_id}_raw"
        _download_direct(media_url, tmp)
        out = f"downloads/{vid_id}.mp3"
        r = subprocess.run(
            [FFMPEG_EXE_PATH, "-y", "-i", tmp, "-vn",
             "-c:a", "libmp3lame", "-b:a", "192k", out],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
        )
        try:
            os.remove(tmp)
        except Exception:
            pass
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg: {r.stderr[-300:] if r.stderr else 'err'}")
        return out, title, duration, None, None
    media_url = d.get("hdplay") or d.get("play")
    if not media_url:
        raise RuntimeError("нет ссылки на видео")
    out = f"downloads/{vid_id}.mp4"
    _download_direct(media_url, out)
    return out, title, duration, None, None


def other_site_download(url, mode):
    opts = {
        "outtmpl": "downloads/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "ffmpeg_location": FFMPEG_DIR,
    }
    if mode == "audio":
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        opts.update({
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
        })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        fn = ydl.prepare_filename(info)
    base, _ = os.path.splitext(fn)
    if mode == "audio":
        cands = [base + ".mp3", fn + ".mp3"]
    else:
        cands = [fn, base + ".mp4", base + ".mkv", base + ".webm"]
    final = next((p for p in cands if os.path.exists(p)), None)
    if not final:
        raise FileNotFoundError(cands)
    title = clean_meta(info.get("title") or "media")
    performer = clean_meta(
        info.get("artist") or info.get("uploader") or info.get("channel") or "",
        max_len=64,
    ) or "Unknown"
    return final, title, info.get("duration"), performer, info.get("thumbnail")


def split_image(image_path, total_parts):
    img = Image.open(image_path)
    w, h = img.size
    cols = 3
    rows = total_parts // cols
    pw = w // cols
    ph = h // rows
    files = []
    for row in range(rows):
        for col in range(cols):
            left = col * pw
            top = row * ph
            right = (col + 1) * pw if col < cols - 1 else w
            bottom = (row + 1) * ph if row < rows - 1 else h
            crop = img.crop((left, top, right, bottom))
            fn = f"temp_photos/part_{row}_{col}.jpg"
            crop.save(fn, "JPEG", quality=95)
            files.append(fn)
    return files


def calculate_auto_parts(image_path):
    img = Image.open(image_path)
    w, h = img.size
    rows = max(1, round(h / (w / 3)))
    return rows * 3


def apply_tags(audio_path, cover_path, title, performer):
    out = os.path.splitext(audio_path)[0] + "_tagged.mp3"
    cmd = [FFMPEG_EXE_PATH, "-y", "-i", audio_path]
    if cover_path and os.path.exists(cover_path):
        cmd += ["-i", cover_path, "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)"]
    else:
        cmd += ["-map", "0:a"]
    cmd += ["-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3",
            "-metadata", f"title={title}", "-metadata", f"artist={performer}", out]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-500:] if r.stderr else "ffmpeg err")
    return out


def get_duration(path):
    try:
        cmd = [FFPROBE_EXE_PATH, "-v", "error", "-show_entries",
               "format=duration", "-of",
               "default=noprint_wrappers=1:nokey=1", path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return None


def owner_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔑 Создать ключ"),
             KeyboardButton(text="📋 Статусы ключей")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


async def run_tts(text: str, voice: str, out_path: str):
    communicate = edge_tts.Communicate(text, voice, rate="+0%", volume="+0%")
    await communicate.save(out_path)
    return out_path


def tts_voice_kb():
    b = InlineKeyboardBuilder()
    for key, (_, label) in TTS_VOICES.items():
        b.button(text=label, callback_data=f"tts_voice:{key}")
    b.adjust(2)
    return b.as_markup()


def convert_to_ogg(mp3_path: str, ogg_path: str):
    cmd = [FFMPEG_EXE_PATH, "-y", "-i", mp3_path,
           "-c:a", "libopus", "-b:a", "64k", ogg_path]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:] if r.stderr else "ogg convert err")
    return ogg_path


def photo_to_webp_sticker(src_path: str, dst_path: str):
    img = Image.open(src_path).convert("RGBA")
    w, h = img.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    canvas = canvas.resize((512, 512), Image.LANCZOS)
    canvas.save(dst_path, "WEBP", quality=95, method=6)
    return dst_path


def validate_pack_shortname(name: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,50}$", name))


# ============================================================
#                         HANDLERS
# ============================================================

@dp.message(F.text == "/whoami")
async def cmd_whoami(message: Message):
    u = message.from_user
    await message.answer(
        f"ID: {u.id}\nUsername: @{u.username or 'нет'}\nИмя: {u.full_name}\n\n"
        f"Ожидаемый владелец: @{OWNER_USERNAME}\nOWNER_ID: {OWNER_ID or 'не задан'}\n\n"
        f"Ты владелец? {'ДА' if is_owner(u) else 'НЕТ'}"
    )


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    bot_uname = await get_bot_username()
    text = (
        "👋 Привет! Что я умею:\n\n"
        "📷 Фото — режу на части 3×N\n"
        "🎬 TikTok — качаю видео или вытаскиваю звук\n"
        "🎵 Аудио — ставлю обложку, название, исполнителя\n"
        "🎙 Озвучка — текст превращаю в голос\n"
        "🎨 Стикерпаки — собираю твои стикеры\n"
        f"🤫 Шёпот — в чате напиши `@{bot_uname} текст @username`\n\n"
        "📖 Команды:\n"
        "/help — помощь\n"
        "/tts — озвучка текста\n"
        "/stickers — стикерпаки\n"
        "/whisper — как отправить шёпот\n"
        "/mykey — мой ключ\n"
        "/code — активировать ключ\n"
        "/cancel — отменить действие\n\n"
        "🔑 Отправь /code ТВОЙ_КЛЮЧ, чтобы начать."
    )
    if is_owner(message.from_user):
        await message.answer(
            text + "\n\n👑 Админ-панель включена — кнопки снизу.",
            reply_markup=owner_reply_kb(),
            parse_mode=None,
        )
    else:
        await message.answer(text, parse_mode=None)


@dp.message(F.text == "/help")
async def cmd_help(message: Message):
    bot_uname = await get_bot_username()
    await message.answer(
        "📖 *Что умею:*\n\n"
        "🎙 `/tts` — озвучить текст\n"
        "🎨 `/stickers` — стикерпаки\n"
        "🤫 `/whisper` — как отправить шёпот\n"
        "🔑 `/mykey` — статус ключа\n\n"
        "📷 *Фото* — режу на 3×N\n"
        "🎬 *TikTok-ссылка* — видео или MP3\n"
        "🎵 *Аудиофайл* — обложка и теги\n\n"
        f"🤫 *Шёпот в чате:* `@{bot_uname} текст @username`",
        parse_mode="Markdown",
    )


@dp.message(F.text.startswith("/code"))
async def cmd_code(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Формат: /code ТВОЙ_КЛЮЧ")
        return
    key = parts[1].strip().strip("`").strip().upper()
    if len(key) != KEY_LENGTH or not key.isalnum():
        await message.answer("❌ Ключ выглядит странно. Проверь, что скопировал полностью.")
        return
    await try_activate_key(message, message.from_user, key)


@dp.message(F.text == "/mykey")
async def cmd_mykey(message: Message):
    user_id = str(message.from_user.id)
    u = DATA["users"].get(user_id)
    if not u:
        await message.answer("🤷 У тебя пока нет активного ключа.")
        return
    if u.get("permanent"):
        await message.answer(f"🔑 Твой ключ: {u.get('key')}\n♾ Тип: навсегда")
    else:
        exp = u.get("expires_at", 0)
        rem = max(0, exp - time.time())
        await message.answer(
            f"🔑 Твой ключ: {u.get('key')}\n"
            f"📅 До: {format_until(exp)}\n"
            f"⏳ Осталось: {format_duration(rem)}"
        )


@dp.message(F.text == "/newkey")
async def cmd_newkey(message: Message):
    if not is_owner(message.from_user):
        return
    b = InlineKeyboardBuilder()
    b.button(text="♾ Навсегда", callback_data="newkey_perm")
    b.button(text="⏳ На время", callback_data="newkey_temp")
    await message.answer("❓ Какой ключ делаем?", reply_markup=b.as_markup())


@dp.message(F.text == "/keys")
async def cmd_keys(message: Message):
    if not is_owner(message.from_user):
        return
    await _send_keys_list(message)


@dp.message(F.text == "🔑 Создать ключ")
async def btn_newkey(message: Message):
    if not is_owner(message.from_user):
        return
    b = InlineKeyboardBuilder()
    b.button(text="♾ Навсегда", callback_data="newkey_perm")
    b.button(text="⏳ На время", callback_data="newkey_temp")
    await message.answer("❓ Какой ключ делаем?", reply_markup=b.as_markup())


@dp.message(F.text == "📋 Статусы ключей")
async def btn_keys(message: Message):
    if not is_owner(message.from_user):
        return
    await _send_keys_list(message)


async def _send_keys_list(message: Message):
    keys = DATA["keys"]
    if not keys:
        await message.answer("🗂 Пока ни одного ключа нет.")
        return
    lines = ["🗂 Все ключи:\n"]
    for k, kd in keys.items():
        if kd.get("used_by"):
            u = DATA["users"].get(str(kd["used_by"]), {})
            uname = u.get("username") or "?"
            if u.get("permanent"):
                status = "♾ активен"
            elif u.get("expires_at", 0) > time.time():
                status = f"⏳ до {format_until(u.get('expires_at', 0))}"
            else:
                status = "❌ истёк"
            lines.append(f"{k} → @{uname} ({status})")
        else:
            if kd.get("permanent"):
                lines.append(f"{k} — ♾ свободен")
            else:
                lines.append(f"{k} — ⏳ {format_duration(kd.get('duration', 0))}, свободен")
    await message.answer("\n".join(lines))


@dp.callback_query(F.data == "newkey_perm")
async def cb_newkey_perm(cb: CallbackQuery):
    if not is_owner(cb.from_user):
        await cb.answer("🙅 Не твоя кнопка", show_alert=True)
        return
    key = generate_key()
    DATA["keys"][key] = {
        "permanent": True,
        "duration": 0,
        "created_at": time.time(),
        "used_by": None,
    }
    save_data(DATA)
    await cb.message.edit_text(f"♾ Перманентный ключ:\n{key}\n\n📤 Отправь его юзеру.")


@dp.callback_query(F.data == "newkey_temp")
async def cb_newkey_temp(cb: CallbackQuery, state: FSMContext):
    if not is_owner(cb.from_user):
        await cb.answer("🙅 Не твоя кнопка", show_alert=True)
        return
    await cb.message.edit_text(
        "⏳ Введи длительность: 1d 2h 30m\n"
        "(d — дни, h — часы, m — минуты, s — секунды)"
    )
    await state.set_state(BotStates.waiting_for_duration)


@dp.message(BotStates.waiting_for_duration)
async def process_duration(message: Message, state: FSMContext):
    if not is_owner(message.from_user):
        await state.clear()
        return
    duration = parse_duration(message.text or "")
    if not duration:
        await message.answer("🤔 Не понял. Попробуй так: 1d 2h 30m")
        return
    key = generate_key()
    DATA["keys"][key] = {
        "permanent": False,
        "duration": duration,
        "created_at": time.time(),
        "used_by": None,
    }
    save_data(DATA)
    await message.answer(
        f"⏳ Временный ключ:\n{key}\n⏱ Живёт {format_duration(duration)}"
    )
    await state.clear()


@dp.message(F.text == "/cancel")
async def cmd_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    for k in ("audio_path", "cover_path", "photo_path"):
        p = data.get(k)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    await state.clear()
    await message.answer("👌 Отменил.")


# ============================================================
#                         TTS
# ============================================================

@dp.message(F.text == "/tts")
async def cmd_tts(message: Message, state: FSMContext):
    await state.set_state(None)
    await message.answer("🎙 Выбери голос:", reply_markup=tts_voice_kb())


@dp.callback_query(F.data.startswith("tts_voice:"))
async def cb_tts_voice(cb: CallbackQuery, state: FSMContext):
    voice_key = cb.data.split(":", 1)[1]
    if voice_key not in TTS_VOICES:
        await cb.answer("Такого голоса нет.", show_alert=True)
        return
    await state.update_data(tts_voice=voice_key)
    await state.set_state(BotStates.waiting_tts_text)
    await cb.message.edit_text(
        f"🎙 Голос: {TTS_VOICES[voice_key][1]}\n\n"
        f"Пришли текст — озвучу. До 1000 символов."
    )
    await cb.answer()


@dp.message(BotStates.waiting_tts_text)
async def process_tts_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Пустой текст.")
        return
    if len(text) > 1000:
        await message.answer(f"⚠️ Слишком много: {len(text)} символов. Максимум 1000.")
        return
    data = await state.get_data()
    voice_key = data.get("tts_voice", "ru_f1")
    voice_id, voice_label = TTS_VOICES[voice_key]

    status = await message.answer("🎙 Генерирую...")
    mp3_path = f"downloads/tts_{message.from_user.id}_{int(time.time())}.mp3"
    ogg_path = f"downloads/tts_{message.from_user.id}_{int(time.time())}.ogg"
    try:
        await run_tts(text, voice_id, mp3_path)
        if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) < 100:
            raise RuntimeError("пустой файл от edge-tts")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, convert_to_ogg, mp3_path, ogg_path)
        dur = await loop.run_in_executor(None, get_duration, ogg_path)

        kw = {
            "voice": FSInputFile(ogg_path, filename="voice.ogg"),
            "caption": f"🎙 Голос: {voice_label}",
        }
        if dur:
            kw["duration"] = int(dur)
        await message.answer_voice(**kw)
        await status.delete()
    except Exception as e:
        err = str(e)[:300]
        try:
            await status.edit_text(f"😔 Не получилось озвучить:\n{err}")
        except Exception:
            await message.answer(f"😔 Не получилось озвучить:\n{err}")
    finally:
        for p in (mp3_path, ogg_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        await state.clear()


# ============================================================
#                    ШЁПОТ (INLINE MODE)
# ============================================================

@dp.message(F.text == "/whisper")
async def cmd_whisper(message: Message):
    bot_uname = await get_bot_username()
    await message.answer(
        "🤫 *Как отправить шёпот:*\n\n"
        f"1. Напиши в любом чате: `@{bot_uname} текст @username`\n"
        f"2. Сверху появится подсказка — выбери её\n"
        f"3. Отправь — прочитает только адресат\n\n"
        f"Например: `@{bot_uname} привет, ты классный @vimbrix`",
        parse_mode="Markdown",
    )


@dp.inline_query()
async def inline_whisper(query: InlineQuery):
    text = (query.query or "").strip()
    bot_uname = (await get_bot_username()).lower()

    if not text:
        result = InlineQueryResultArticle(
            id="hint_empty",
            title="🤫 Напиши: текст @username",
            description="Например: привет @vimbrix",
            input_message_content=InputTextMessageContent(
                message_text=(
                    "🤫 *Как отправить шёпот:*\n\n"
                    f"1. Напиши в поле ввода: `@{bot_uname} текст @username`\n"
                    "2. Выбери подсказку сверху\n"
                    "3. Отправь — прочитает только получатель."
                ),
                parse_mode="Markdown",
            ),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    mentions = [
        m for m in re.finditer(r"@(\w+)", text)
        if m.group(1).lower() != bot_uname
    ]

    if not mentions:
        result = InlineQueryResultArticle(
            id="hint_no_target",
            title="🤫 Добавь @username получателя",
            description="Например: привет @vimbrix",
            input_message_content=InputTextMessageContent(
                message_text=(
                    "🤫 *Не хватает получателя.*\n\n"
                    f"Напиши: `@{bot_uname} текст @username`"
                ),
                parse_mode="Markdown",
            ),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    target_mention = mentions[-1]
    target_username = target_mention.group(1)
    whisper_text = text[:target_mention.start()].strip()

    if not whisper_text:
        result = InlineQueryResultArticle(
            id="hint_no_text",
            title="🤫 А что шептать-то?",
            description="Напиши текст перед @username",
            input_message_content=InputTextMessageContent(
                message_text="🤫 Добавь текст перед @username."
            ),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    target_id = None
    for uid, udata in DATA["users"].items():
        if (udata.get("username") or "").lower() == target_username.lower():
            target_id = int(uid)
            break

    wid = next(_whisper_counter)
    WHISPERS[wid] = {
        "target_id": target_id,
        "target_name": target_username,
        "text": whisper_text,
        "from_id": query.from_user.id,
        "from_name": query.from_user.full_name,
        "expires": time.time() + 86400,
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Прочитать содержимое", callback_data=f"whisper:{wid}")],
        [InlineKeyboardButton(text="Как отправлять шепот?", callback_data="whisper_help")],
    ])

    result = InlineQueryResultArticle(
        id=str(wid),
        title=f"🤫 Шёпот для @{target_username}",
        description=whisper_text[:60],
        input_message_content=InputTextMessageContent(
            message_text=(
                f"🔒 Секретное сообщение для @{target_username}\n"
                f"Только он может прочитать содержимое"
            ),
        ),
        reply_markup=kb,
    )
    await query.answer([result], cache_time=0, is_personal=True)


@dp.callback_query(F.data.startswith("whisper:"))
async def cb_whisper(cb: CallbackQuery):
    try:
        wid = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Шёпот сломался.", show_alert=True)
        return

    w = WHISPERS.get(wid)
    if not w:
        await cb.answer("Этот шёпот уже улетел в никуда.", show_alert=True)
        return
    if w["expires"] < time.time():
        WHISPERS.pop(wid, None)
        await cb.answer("Шёпот устарел и растворился.", show_alert=True)
        return

    is_target = False
    if w["target_id"] is not None and cb.from_user.id == w["target_id"]:
        is_target = True
    if (cb.from_user.username or "").lower() == w["target_name"].lower():
        is_target = True

    is_sender = cb.from_user.id == w["from_id"]

    if is_sender:
        txt = w["text"]
        if len(txt) <= 180:
            await cb.answer(
                f"📤 Это твой шёпот для @{w['target_name']}.\n\nТекст: {txt}",
                show_alert=True,
            )
        else:
            try:
                await bot.send_message(
                    cb.from_user.id,
                    f"📤 Твой шёпот для @{w['target_name']}:\n\n{txt}",
                )
                await cb.answer("📩 Отправил тебе в личку.", show_alert=True)
            except Exception:
                await cb.answer(
                    f"📤 Твой шёпот для @{w['target_name']}.\n\nТекст: {txt[:180]}...",
                    show_alert=True,
                )
    elif is_target:
        txt = w["text"]
        if len(txt) <= 180:
            await cb.answer(f"🤫 {txt}", show_alert=True)
        else:
            try:
                await bot.send_message(
                    cb.from_user.id,
                    f"🤫 Шёпот от {w['from_name']}:\n\n{txt}",
                )
                await cb.answer("📩 Шёпот ушёл тебе в личку.", show_alert=True)
            except Exception:
                await cb.answer(f"🤫 {txt[:180]}...", show_alert=True)
    else:
        await cb.answer(random.choice(FUNNY_REPLIES), show_alert=True)


@dp.callback_query(F.data == "whisper_help")
async def cb_whisper_help(cb: CallbackQuery):
    bot_uname = await get_bot_username()
    await cb.answer(
        "🤫 Чтобы отправить шёпот:\n\n"
        f"1. В любом чате напиши:\n@{bot_uname} текст @username\n\n"
        "2. Выбери подсказку сверху\n"
        "3. Отправь — прочитает только адресат",
        show_alert=True,
    )


# ============================================================
#                       СТИКЕРПАКИ
# ============================================================

@dp.message(F.text == "/stickers")
async def cmd_stickers(message: Message, state: FSMContext):
    await state.set_state(None)
    b = InlineKeyboardBuilder()
    b.button(text="➕ Создать новый", callback_data="st_create")
    b.button(text="📎 Добавить в существующий", callback_data="st_add")
    b.button(text="📂 Мои паки", callback_data="st_list")
    b.adjust(1)
    await message.answer(
        "🎨 *Стикерпаки*\n\n"
        "Создай свой пак и добавляй туда стикеры из фото.\n"
        "В имени пака будет метка бота.",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data == "st_create")
async def cb_st_create(cb: CallbackQuery, state: FSMContext):
    bot_uname = await get_bot_username()
    await cb.message.edit_text(
        "📝 Придумай короткое имя для пака.\n"
        "Только латиница, цифры, `_`. Начинается с буквы.\n"
        f"В Telegram пак будет: `имя_by_{bot_uname}`.\n\n"
        "Пример: `mycats`"
    )
    await state.set_state(BotStates.waiting_sticker_name)
    await cb.answer()


@dp.message(BotStates.waiting_sticker_name)
async def process_sticker_name(message: Message, state: FSMContext):
    short = (message.text or "").strip().lower()
    if not validate_pack_shortname(short):
        await message.answer(
            "⚠️ Не подходит. Только латиница, цифры, `_`. Начинается с буквы."
        )
        return
    bot_uname = await get_bot_username()
    full_name = f"{short}_by_{bot_uname}"
    if len(full_name) > 64:
        await message.answer("⚠️ Слишком длинное. Сократи.")
        return
    await state.update_data(sticker_short=short, sticker_full=full_name)
    await state.set_state(BotStates.waiting_sticker_title)
    await message.answer(
        f"📛 Теперь название пака (видно в списке).\n"
        f"Пример: `Мои котики`"
    )


@dp.message(BotStates.waiting_sticker_title)
async def process_sticker_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title or len(title) > 64:
        await message.answer("⚠️ Название 1-64 символа. Попробуй ещё.")
        return
    await state.update_data(sticker_title=title)
    await state.set_state(BotStates.waiting_sticker_first)
    await message.answer(
        "🖼 Теперь пришли первую картинку для стикера.\n"
        "Сделаю из неё стикер 512×512."
    )


@dp.message(BotStates.waiting_sticker_first, F.photo)
async def process_sticker_first(message: Message, state: FSMContext):
    data = await state.get_data()
    full_name = data.get("sticker_full")
    title = data.get("sticker_title")
    if not full_name or not title:
        await message.answer("⚠️ Что-то потерялось. Начни заново: /stickers")
        await state.clear()
        return

    p = message.photo[-1]
    fi = await bot.get_file(p.file_id)
    src = f"temp_photos/{p.file_id}.jpg"
    dst = f"temp_photos/sticker_{p.file_id}.webp"
    await bot.download_file(fi.file_path, src)

    status = await message.answer("🎨 Готовлю стикер...")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, photo_to_webp_sticker, src, dst)
        if not os.path.exists(dst):
            raise RuntimeError("не удалось создать webp")
        with open(dst, "rb") as f:
            sticker_bytes = f.read()
        print(f"sticker size: {len(sticker_bytes)} bytes")
        if len(sticker_bytes) > 512 * 1024:
            raise RuntimeError("стикер больше 512 КБ")

        await bot.create_new_sticker_set(
            user_id=message.from_user.id,
            name=full_name,
            title=title,
            stickers=[InputSticker(
                sticker=BufferedInputFile(sticker_bytes, filename="sticker.webp"),
                format="static",
                emoji_list=["😀"],
            )],
        )

        user_id = str(message.from_user.id)
        packs = DATA["sticker_packs"].setdefault(user_id, [])
        packs.append({
            "name": full_name,
            "title": title,
            "created_at": time.time(),
        })
        save_data(DATA)

        await status.edit_text(
            f"✅ Пак создан!\n\n"
            f"📛 {title}\n"
            f"🔗 https://t.me/addstickers/{full_name}\n\n"
            f"Теперь можешь добавлять туда стикеры: /stickers → Добавить."
        )
    except Exception as e:
        err = str(e)[:400]
        print(f"sticker err: {err}")
        try:
            await status.edit_text(f"❌ Ошибка:\n{err}")
        except Exception:
            await message.answer(f"❌ Ошибка:\n{err}")
    finally:
        for f in (src, dst):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        await state.clear()


@dp.message(BotStates.waiting_sticker_first)
async def wrong_sticker_first(message: Message):
    await message.answer("🖼 Нужна именно картинка.")


@dp.callback_query(F.data == "st_list")
async def cb_st_list(cb: CallbackQuery):
    user_id = str(cb.from_user.id)
    packs = DATA["sticker_packs"].get(user_id, [])
    if not packs:
        await cb.message.edit_text("📂 У тебя пока нет паков. Создай первый через /stickers")
        await cb.answer()
        return
    lines = ["📂 Твои стикерпаки:\n"]
    for p in packs:
        lines.append(f"• {p['title']}\n  https://t.me/addstickers/{p['name']}")
    await cb.message.edit_text("\n".join(lines))
    await cb.answer()


@dp.callback_query(F.data == "st_add")
async def cb_st_add(cb: CallbackQuery, state: FSMContext):
    user_id = str(cb.from_user.id)
    packs = DATA["sticker_packs"].get(user_id, [])
    if not packs:
        await cb.message.edit_text("📂 У тебя ещё нет паков. Создай сначала: /stickers")
        await cb.answer()
        return
    b = InlineKeyboardBuilder()
    for i, p in enumerate(packs):
        b.button(text=p["title"], callback_data=f"st_pick:{i}")
    b.adjust(1)
    await cb.message.edit_text("📎 В какой пак добавить?", reply_markup=b.as_markup())
    await cb.answer()


@dp.callback_query(F.data.startswith("st_pick:"))
async def cb_st_pick(cb: CallbackQuery, state: FSMContext):
    try:
        idx = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Ошибка", show_alert=True)
        return
    user_id = str(cb.from_user.id)
    packs = DATA["sticker_packs"].get(user_id, [])
    if idx < 0 or idx >= len(packs):
        await cb.answer("Пак не найден", show_alert=True)
        return
    pack = packs[idx]
    await state.update_data(add_pack=pack["name"])
    await state.set_state(BotStates.waiting_sticker_add_photo)
    await cb.message.edit_text(
        f"📎 Пак: {pack['title']}\n\n"
        f"Пришли картинку для стикера."
    )
    await cb.answer()


@dp.message(BotStates.waiting_sticker_add_photo, F.photo)
async def process_sticker_add(message: Message, state: FSMContext):
    data = await state.get_data()
    pack_name = data.get("add_pack")
    if not pack_name:
        await message.answer("⚠️ Что-то потерялось. Заново: /stickers")
        await state.clear()
        return

    p = message.photo[-1]
    fi = await bot.get_file(p.file_id)
    src = f"temp_photos/{p.file_id}.jpg"
    dst = f"temp_photos/sticker_{p.file_id}.webp"
    await bot.download_file(fi.file_path, src)

    status = await message.answer("🎨 Готовлю стикер...")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, photo_to_webp_sticker, src, dst)
        with open(dst, "rb") as f:
            sticker_bytes = f.read()
        if len(sticker_bytes) > 512 * 1024:
            raise RuntimeError("стикер больше 512 КБ")

        await bot.add_sticker_to_set(
            user_id=message.from_user.id,
            name=pack_name,
            sticker=InputSticker(
                sticker=BufferedInputFile(sticker_bytes, filename="sticker.webp"),
                format="static",
                emoji_list=["😀"],
            ),
        )
        await status.edit_text(f"✅ Стикер добавлен в {pack_name}")
    except Exception as e:
        err = str(e)[:300]
        try:
            await status.edit_text(f"❌ Ошибка:\n{err}")
        except Exception:
            await message.answer(f"❌ Ошибка:\n{err}")
    finally:
        for f in (src, dst):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        await state.clear()


@dp.message(BotStates.waiting_sticker_add_photo)
async def wrong_sticker_add(message: Message):
    await message.answer("🖼 Нужна именно картинка.")


# ============================================================
#                    РАБОТА С МЕДИА
# ============================================================

@dp.message(F.audio)
async def process_audio_for_tag(message: Message, state: FSMContext):
    a = message.audio
    old = await state.get_data()
    for k in ("audio_path", "cover_path"):
        p = old.get(k)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    fi = await bot.get_file(a.file_id)
    ext = os.path.splitext(a.file_name or "audio.mp3")[1] or ".mp3"
    lp = f"downloads/tag_{a.file_id}{ext}"
    await bot.download_file(fi.file_path, lp)
    await state.update_data(audio_path=lp)
    await state.set_state(BotStates.waiting_for_cover)
    await message.answer("🎨 Кидай обложку — картинку для этого трека.")


@dp.message(BotStates.waiting_for_cover, F.photo)
async def process_cover(message: Message, state: FSMContext):
    p = message.photo[-1]
    fi = await bot.get_file(p.file_id)
    cp = f"downloads/cover_{p.file_id}.jpg"
    await bot.download_file(fi.file_path, cp)
    await state.update_data(cover_path=cp)
    await state.set_state(BotStates.waiting_for_meta)
    await message.answer(
        "✍️ Теперь название и исполнитель через палочку:\n"
        "Название | Исполнитель"
    )


@dp.message(BotStates.waiting_for_cover)
async def wrong_cover(message: Message):
    await message.answer("📷 Нужна именно картинка.")


@dp.message(BotStates.waiting_for_meta)
async def process_meta(message: Message, state: FSMContext):
    t = (message.text or "").strip()
    if "|" not in t:
        await message.answer("⚠️ Формат такой: Название | Исполнитель")
        return
    title, performer = [x.strip() for x in t.split("|", 1)]
    if not title:
        await message.answer("⚠️ Название-то пустое.")
        return
    if not performer:
        performer = "Unknown"
    data = await state.get_data()
    ap = data.get("audio_path")
    cp = data.get("cover_path")
    if not ap or not os.path.exists(ap):
        await message.answer("🤷 Аудио пропало. Давай заново.")
        await state.clear()
        return
    status = await message.answer("🎧 Ставлю метки...")
    out = None
    try:
        loop = asyncio.get_event_loop()
        out = await loop.run_in_executor(None, apply_tags, ap, cp, title, performer)
        dur = await loop.run_in_executor(None, get_duration, out)
        kw = {
            "audio": FSInputFile(out, filename=f"{title}.mp3"),
            "title": title,
            "performer": performer,
            "caption": "✅ Готово! Забирай.",
        }
        if dur:
            kw["duration"] = int(dur)
        if cp and os.path.exists(cp):
            kw["thumbnail"] = FSInputFile(cp)
        await message.answer_audio(**kw)
        await status.delete()
    except Exception as e:
        err = str(e)[:300]
        try:
            await status.edit_text(f"❌ Ошибка: {err}")
        except Exception:
            await message.answer(f"❌ Ошибка: {err}")
    finally:
        for p in (ap, cp, out):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        await state.clear()


@dp.message(F.voice | F.video_note)
async def reject_voice(message: Message):
    await message.answer("🙈 Голосовые и кружки пока не расшифровываю.")


@dp.message(F.photo)
async def process_photo(message: Message, state: FSMContext):
    p = message.photo[-1]
    fi = await bot.get_file(p.file_id)
    lp = f"temp_photos/{p.file_id}.jpg"
    await bot.download_file(fi.file_path, lp)
    await state.update_data(photo_path=lp)
    await state.set_state(BotStates.waiting_for_parts)
    b = InlineKeyboardBuilder()
    b.button(text="🎲 На твоё усмотрение", callback_data="auto_split")
    await message.answer(
        "✂️ На сколько кусочков резать?\n"
        "Число должно делиться на 3 (например, 6, 9, 12).\n"
        "Или жми кнопку — сам прикину.",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data == "auto_split", BotStates.waiting_for_parts)
async def auto_split(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    pp = d.get("photo_path")
    if not pp or not os.path.exists(pp):
        await cb.answer("Файл потерялся", show_alert=True)
        await state.clear()
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    loop = asyncio.get_event_loop()
    n = await loop.run_in_executor(None, calculate_auto_parts, pp)
    await cb.message.answer(f"🎲 Прикинул: {n} кусочков.")
    await execute_splitting(cb.message, state, pp, n)
    await cb.answer()


@dp.message(BotStates.waiting_for_parts)
async def process_parts(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("❗ Нужно число цифрами.")
        return
    n = int(message.text)
    if n < 3 or n % 3 != 0:
        await message.answer("⚠️ Число должно делиться на 3. Попробуй ещё раз.")
        return
    d = await state.get_data()
    await execute_splitting(message, state, d.get("photo_path"), n)


async def execute_splitting(msg_obj, state, pp, n):
    st = await msg_obj.answer("🔪 Режу...")
    try:
        loop = asyncio.get_event_loop()
        parts = await loop.run_in_executor(None, split_image, pp, n)
        for pf in parts:
            if os.path.exists(pf):
                await msg_obj.answer_photo(FSInputFile(pf))
                os.remove(pf)
        await st.delete()
        await msg_obj.answer("✅ Готово! Держи свои кусочки.")
    except Exception as e:
        await msg_obj.answer(f"❌ Что-то пошло не так: {e}")
    finally:
        if os.path.exists(pp):
            os.remove(pp)
        await state.clear()


@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def ask_type(message: Message, state: FSMContext):
    url = message.text.strip()
    if "youtube.com" in url or "youtu.be" in url:
        await message.answer(
            "⚠️ С YouTube не могу качать — сервер блокируется.\n"
            "Попробуй TikTok или другую ссылку."
        )
        return
    await state.update_data(download_url=url)
    await state.set_state(BotStates.waiting_for_media_type)
    b = InlineKeyboardBuilder()
    b.button(text="🎵 Только звук (MP3)", callback_data="get_audio")
    b.button(text="🎬 Видео (MP4)", callback_data="get_video")
    await message.answer("❓ Что вытащить из ссылки?", reply_markup=b.as_markup())


@dp.callback_query(F.data.in_({"get_audio", "get_video"}), BotStates.waiting_for_media_type)
async def process_download(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    url = d.get("download_url")
    mode = cb.data
    await cb.message.edit_reply_markup(reply_markup=None)
    status = await cb.message.answer("⏳ Секунду, качаю...")
    await cb.answer()

    loop = asyncio.get_event_loop()
    is_tiktok = "tiktok.com" in url or "vm.tiktok.com" in url or "vt.tiktok.com" in url

    fn = None
    tp = None
    try:
        if is_tiktok:
            try:
                await status.edit_text("📥 Тяну с TikTok...")
            except Exception:
                pass
            fn, title, dur, _, _ = await loop.run_in_executor(None, tiktok_via_api, url, mode)
        else:
            try:
                await status.edit_text("📥 Пробую скачать...")
            except Exception:
                pass
            fn, title, dur, performer, thumb_url = await loop.run_in_executor(
                None, other_site_download, url, mode
            )
            if thumb_url and mode == "get_audio":
                try:
                    tp = f"downloads/thumb_{abs(hash(url)) % 10**8}.jpg"
                    req = urllib.request.Request(
                        thumb_url, headers={"User-Agent": "Mozilla/5.0"}
                    )
                    with urllib.request.urlopen(req, timeout=15) as r:
                        data = r.read()
                    raw = tp + ".raw"
                    with open(raw, "wb") as f:
                        f.write(data)
                    img = Image.open(raw).convert("RGB")
                    img.save(tp, "JPEG", quality=90)
                    os.remove(raw)
                except Exception as te:
                    print(f"thumb: {te}")
                    tp = None

        if not fn or not os.path.exists(fn):
            raise FileNotFoundError("файл не скачался")

        if mode == "get_audio":
            kw = {
                "audio": FSInputFile(fn, filename=f"{title}.mp3"),
                "title": title,
                "caption": "🎵 Готово, забирай звук.",
            }
            if dur:
                kw["duration"] = int(dur)
            if tp and os.path.exists(tp):
                kw["thumbnail"] = FSInputFile(tp)
            await cb.message.answer_audio(**kw)
        else:
            await cb.message.answer_video(
                video=FSInputFile(fn, filename=f"{title}.mp4"),
                caption="🎬 Готово, приятного просмотра.",
                duration=int(dur) if dur else None,
            )
        await status.delete()

    except Exception as e:
        print(f"err: {e}")
        err = str(e)[:300]
        try:
            await status.edit_text(f"😔 Не получилось скачать.\n{err}")
        except Exception:
            await cb.message.answer(f"😔 Не получилось скачать.\n{err}")
    finally:
        for p in (fn, tp):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        await state.clear()


async def keep_alive():
    while True:
        try:
            await asyncio.sleep(240)
            me = await bot.get_me()
            print(f"keep-alive: @{me.username}")
        except Exception as e:
            print(f"keep-alive: {e}")


async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="code", description="Активировать ключ"),
        BotCommand(command="mykey", description="Мой ключ"),
        BotCommand(command="tts", description="Озвучить текст"),
        BotCommand(command="stickers", description="Стикерпаки"),
        BotCommand(command="whisper", description="Как отправить шёпот"),
        BotCommand(command="cancel", description="Отменить действие"),
    ]
    try:
        await bot.set_my_commands(commands)
        print("Команды установлены")
    except Exception as e:
        print(f"set_my_commands err: {e}")


async def set_bot_menu():
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        print("Меню-кнопка установлена")
    except Exception as e:
        print(f"set_chat_menu_button err: {e}")


async def test_tts():
    try:
        test_path = "downloads/test_tts.mp3"
        await run_tts("тест", "ru-RU-DmitryNeural", test_path)
        size = os.path.getsize(test_path) if os.path.exists(test_path) else 0
        print(f"TTS test: {size} bytes")
        if os.path.exists(test_path):
            os.remove(test_path)
    except Exception as e:
        print(f"TTS test failed: {e}")


dp.message.middleware(AccessMiddleware())


async def main():
    print(f"keys: {len(DATA['keys'])}, users: {len(DATA['users'])}")
    await set_bot_commands()
    await set_bot_menu()
    await test_tts()
    print("Bot started")
    asyncio.create_task(keep_alive())
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("stopped")
