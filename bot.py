import os
import re
import json
import time
import asyncio
import secrets
import random
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
    MenuButtonCommands, InputMediaPhoto,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from PIL import Image
import yt_dlp
from static_ffmpeg import run as static_ffmpeg_run

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("нет BOT_TOKEN")

STORAGE_CHAT_ID = os.environ.get("STORAGE_CHAT_ID")

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
STT_MODEL = None

TTT_GAMES = {}
MUTED = {}
BUSINESS_CONNECTIONS = {}

WHISPER_TTL = 86400  # 24 часа

# --- Тексты кнопок (константы, чтобы не путаться) ---
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


os.makedirs("downloads", exist_ok=True)
os.makedirs("temp_photos", exist_ok=True)

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


def _empty_data():
    return {"keys": {}, "users": {}, "sticker_packs": {}, "whispers": {}, "whisper_counter": 1}


def load_data_from_file():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                d.setdefault("keys", {})
                d.setdefault("users", {})
                d.setdefault("sticker_packs", {})
                d.setdefault("whispers", {})
                d.setdefault("whisper_counter", 1)
                return d
        except Exception as e:
            print(f"load_data_from_file: {e}")
    return _empty_data()


def save_data_to_file(data):
    try:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        print(f"save_data_to_file: {e}")


async def load_data_from_tg():
    if not STORAGE_CHAT_ID:
        print("STORAGE_CHAT_ID не задан, читаю из файла")
        return load_data_from_file()
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
            async with s.get(url, params={"chat_id": STORAGE_CHAT_ID}) as r:
                resp = await r.json()
        if not resp.get("ok"):
            print(f"load_data_from_tg getChat: {resp}")
            return load_data_from_file()

        pinned = resp["result"].get("pinned_message")
        if not pinned or "document" not in pinned:
            print("load_data_from_tg: нет закреплённого файла, читаю из file")
            return load_data_from_file()

        file_id = pinned["document"]["file_id"]
        async with aiohttp.ClientSession() as s:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile"
            async with s.get(url, params={"file_id": file_id}) as r:
                fdata = await r.json()
        if not fdata.get("ok"):
            print(f"load_data_from_tg getFile: {fdata}")
            return load_data_from_file()

        file_path = fdata["result"]["file_path"]
        async with aiohttp.ClientSession() as s:
            url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            async with s.get(url) as r:
                content = await r.read()

        d = json.loads(content.decode("utf-8"))
        d.setdefault("keys", {})
        d.setdefault("users", {})
        d.setdefault("sticker_packs", {})
        d.setdefault("whispers", {})
        d.setdefault("whisper_counter", 1)
        print(f"load_data_from_tg OK: keys={len(d['keys'])}, users={len(d['users'])}, whispers={len(d['whispers'])}")
        save_data_to_file(d)
        return d
    except Exception as e:
        print(f"load_data_from_tg err: {e}")
        return load_data_from_file()


async def save_data_to_tg(data):
    save_data_to_file(data)
    if not STORAGE_CHAT_ID:
        return
    try:
        import aiohttp
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        async with aiohttp.ClientSession() as s:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
            form = aiohttp.FormData()
            form.add_field("chat_id", str(STORAGE_CHAT_ID))
            form.add_field(
                "document", content,
                filename="data.json",
                content_type="application/json",
            )
            form.add_field("disable_notification", "true")
            async with s.post(url, data=form) as r:
                resp = await r.json()
        if not resp.get("ok"):
            print(f"save_data_to_tg send: {resp}")
            return
        msg_id = resp["result"]["message_id"]
        async with aiohttp.ClientSession() as s:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/pinChatMessage"
            async with s.post(url, data={
                "chat_id": STORAGE_CHAT_ID,
                "message_id": msg_id,
                "disable_notification": True,
            }) as r:
                presp = await r.json()
        if not presp.get("ok"):
            print(f"save_data_to_tg pin: {presp}")
    except Exception as e:
        print(f"save_data_to_tg err: {e}")


DATA = _empty_data()


def save_data(data):
    save_data_to_file(data)
    if STORAGE_CHAT_ID:
        try:
            asyncio.create_task(save_data_to_tg(data))
        except RuntimeError:
            pass


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


def cleanup_expired_whispers():
    now = time.time()
    w = DATA.get("whispers", {})
    expired = [wid for wid, wd in w.items() if wd.get("expires", 0) < now]
    for wid in expired:
        del w[wid]
    if expired:
        print(f"whispers cleanup: удалено {len(expired)} просроченных")


PASSTHROUGH_COMMANDS = {
    "/whoami", "/cancel", "/start", "/mykey", "/help", "/code", "/whisper", "/revoke",
}
DOT_COMMANDS = (".ttt", ".mute", ".unmute", ".xo", ".revoke")


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, Message):
            return await handler(event, data)
        user = event.from_user
        if user is None:
            return await handler(event, data)
        text = (getattr(event, "text", None) or "").strip()
        cmd = text.split()[0].lower() if text else ""

        chat_id = event.chat.id if event.chat else None
        if chat_id and chat_id in MUTED and user.id in MUTED[chat_id]:
            if not is_owner(user):
                try:
                    await event.delete()
                except Exception as e:
                    print(f"mute delete err: {e}")
                try:
                    await bot.send_message(chat_id, "🔇 МОЛЧАТЬ!!!")
                except Exception as e:
                    print(f"mute send err: {e}")
                return

        if cmd in PASSTHROUGH_COMMANDS:
            return await handler(event, data)
        if text.startswith(DOT_COMMANDS):
            return await handler(event, data)
        if is_owner(user):
            return await handler(event, data)
        status = user_status(user.id)
        if status == "valid":
            return await handler(event, data)
        if status == "expired":
            DATA["users"].pop(str(user.id), None)
            save_data(DATA)
            try:
                await event.answer("⏰ Срок действия ключа истёк. Введи новый через /code.")
            except Exception as e:
                print(f"expired send err: {e}")
            return

        try:
            await event.answer(
                "🔒 Доступ только по ключу.\n\n"
                "1. Получи ключ у @vimbrix\n"
                "2. Отправь: /code ТВОЙ_КЛЮЧ\n\n"
                "Если уже активировал — /mykey покажет статус."
            )
        except Exception as e:
            print(f"BLOCKED send err: {e}")


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
                await event.answer("😉 Ты уже активировал этот ключ (навсегда).")
            else:
                exp = u.get("expires_at", 0)
                rem = max(0, exp - time.time())
                await event.answer(
                    f"😉 Ключ уже активирован.\n\n"
                    f"⏳ Осталось: *{format_duration(rem)}*\n"
                    f"📅 До: *{format_until(exp)}*",
                    parse_mode="Markdown",
                )
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
        await event.answer(
            "✅ *Ключ активирован!*\n\n"
            "♾ Тип: навсегда"
        )
    else:
        await event.answer(
            f"✅ *Ключ активирован!*\n\n"
            f"⏱ Ключ действует: *{format_duration(duration)}*\n"
            f"📅 Дата истечения: *{format_until(now + duration)}*"
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
if STORAGE_CHAT_ID:
    print(f"Storage chat: {STORAGE_CHAT_ID}")
else:
    print("Storage chat: НЕ ЗАДАН (данные будут теряться)")


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
    if img.mode != "RGB":
        img = img.convert("RGB")

    cols = 3
    rows = total_parts // cols

    w, h = img.size

    new_w = (w // cols) * cols
    new_h = (h // rows) * rows

    if new_w != w or new_h != h:
        img = img.resize((new_w, new_h), Image.LANCZOS)
        w, h = img.size
        print(f"split: -> ровная сетка {cols}x{rows} ({w}x{h})")

    pw = w // cols
    ph = h // rows

    files = []
    for row in range(rows):
        for col in range(cols):
            left = col * pw
            top = row * ph
            right = left + pw
            bottom = top + ph
            crop = img.crop((left, top, right, bottom))
            fn = f"temp_photos/part_{row}_{col}.png"
            crop.save(fn, "PNG", optimize=False, compress_level=1)
            files.append(fn)
    return files


def calculate_auto_parts(image_path):
    img = Image.open(image_path)
    w, h = img.size
    ratio = h / w
    if ratio > 1.5:
        return 9
    elif ratio > 1.0:
        return 6
    else:
        return 3


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


# ============================================================
#                  КЛЮЧИ (переписано с нуля)
# ============================================================

def owner_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(text=BTN_CREATE_KEY),
            KeyboardButton(text=BTN_KEYS_STATUS),
        ]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _generate_key() -> str:
    for _ in range(200):
        k = "".join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_LENGTH))
        if k not in DATA["keys"]:
            return k
    raise RuntimeError("Не удалось сгенерировать уникальный ключ")


def _key_status_text(kd: dict) -> str:
    """Красивое описание статуса одного ключа."""
    if kd.get("used_by"):
        u = DATA["users"].get(str(kd["used_by"]), {})
        uname = u.get("username") or f"id{kd['used_by']}"
        if u.get("permanent"):
            return f"♾ активирован @{uname}"
        exp = u.get("expires_at", 0)
        if exp > time.time():
            rem = max(0, exp - time.time())
            return f"⏳ @{uname} — осталось {format_duration(rem)} (до {format_until(exp)})"
        return f"❌ @{uname} — истёк"
    if kd.get("permanent"):
        return "♾ свободен"
    return f"⏳ {format_duration(kd.get('duration', 0))}, свободен"


def _render_keys_list():
    """Возвращает (text, inline_keyboard или None)."""
    keys = DATA["keys"]
    if not keys:
        return "🗂 Пока ни одного ключа нет.", None

    free_keys = [(k, v) for k, v in keys.items() if not v.get("used_by")]
    used_keys = [(k, v) for k, v in keys.items() if v.get("used_by")]

    lines = [f"🗂 *Ключи* (всего: {len(keys)})\n"]

    if free_keys:
        lines.append(f"*Свободные ({len(free_keys)}):*")
        for k, kd in free_keys:
            lines.append(f"`{k}` — {_key_status_text(kd)}")
        lines.append("")

    if used_keys:
        lines.append(f"*Активированные ({len(used_keys)}):*")
        for k, kd in used_keys:
            lines.append(f"`{k}` — {_key_status_text(kd)}")

    b = InlineKeyboardBuilder()
    for k in keys.keys():
        b.button(text=f"🗑 {k}", callback_data=f"kdel:{k}")
    b.adjust(2)

    return "\n".join(lines), b.as_markup()


async def _send_keys_list(message: Message):
    try:
        text, kb = _render_keys_list()
    except Exception as e:
        import traceback
        traceback.print_exc()
        await message.answer(f"❌ Ошибка формирования списка: {e}")
        return
    try:
        if kb is None:
            await message.answer(text, parse_mode="Markdown")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        print(f"[key] markdown send err: {e}")
        try:
            if kb is None:
                await message.answer(text)
            else:
                await message.answer(text, reply_markup=kb)
        except Exception as e2:
            print(f"[key] plain send err: {e2}")
            await message.answer(f"❌ Не смог отправить список: {e2}")


async def _show_create_key_menu(message: Message):
    b = InlineKeyboardBuilder()
    b.button(text="♾ Навсегда", callback_data="kc:perm")
    b.button(text="⏳ На время", callback_data="kc:temp")
    b.adjust(1)
    await message.answer("❓ Какой ключ делаем?", reply_markup=b.as_markup())


def _revoke_by_user_id(uid):
    uid = str(uid)
    u = DATA["users"].pop(uid, None)
    if not u:
        return None
    key = u.get("key")
    key_deleted = False
    if key and key in DATA["keys"]:
        del DATA["keys"][key]
        key_deleted = True
    return {"uid": uid, "username": u.get("username") or "",
            "key": key, "key_deleted": key_deleted}


def _revoke_by_username(uname):
    uname_l = uname.lower().lstrip("@")
    for uid, udata in list(DATA["users"].items()):
        if (udata.get("username") or "").lower() == uname_l:
            return _revoke_by_user_id(uid)
    return None


def _revoke_by_keycode(code):
    code = code.strip().upper()
    kd = DATA["keys"].get(code)
    if not kd:
        return None
    used_by = kd.get("used_by")
    del DATA["keys"][code]
    user_removed = False
    if used_by and str(used_by) in DATA["users"]:
        del DATA["users"][str(used_by)]
        user_removed = True
    return {"key": code, "used_by": used_by, "user_removed": user_removed}


# --- Reply keyboard handlers (bulletproof lambda filters) ---

@dp.message(lambda m: m.text is not None and BTN_CREATE_KEY in m.text)
async def btn_create_key(message: Message):
    print(f"[key] create menu by {message.from_user.id}")
    if not is_owner(message.from_user):
        return
    await _show_create_key_menu(message)


@dp.message(lambda m: m.text is not None and BTN_KEYS_STATUS in m.text)
async def btn_keys_status(message: Message):
    print(f"[key] status list by {message.from_user.id}")
    if not is_owner(message.from_user):
        return
    await _send_keys_list(message)


# --- Commands ---

@dp.message(F.text == "/keys")
async def cmd_keys(message: Message):
    if not is_owner(message.from_user):
        return
    await _send_keys_list(message)


@dp.message(F.text == "/newkey")
async def cmd_newkey(message: Message):
    if not is_owner(message.from_user):
        return
    await _show_create_key_menu(message)


# --- Inline callbacks ---

@dp.callback_query(F.data == "kc:perm")
async def cb_kc_perm(cb: CallbackQuery):
    if not is_owner(cb.from_user):
        await cb.answer("Только владелец.", show_alert=True)
        return
    key = _generate_key()
    DATA["keys"][key] = {
        "permanent": True,
        "duration": 0,
        "created_at": time.time(),
        "used_by": None,
    }
    save_data(DATA)
    await cb.message.edit_text(
        f"♾ *Перманентный ключ создан:*\n\n`{key}`\n\n"
        f"Отправь его юзеру.\nАктивация: `/code {key}`",
        parse_mode="Markdown",
    )
    await cb.answer("Готово!")


@dp.callback_query(F.data == "kc:temp")
async def cb_kc_temp(cb: CallbackQuery, state: FSMContext):
    if not is_owner(cb.from_user):
        await cb.answer("Только владелец.", show_alert=True)
        return
    await cb.message.edit_text(
        "⏳ На сколько ключ? Формат: `1d 2h 30m`\n"
        "(d — дни, h — часы, m — минуты, s — секунды)\n\n"
        "Пример: `24h` или `7d`",
        parse_mode="Markdown",
    )
    await state.set_state(BotStates.waiting_for_duration)
    await cb.answer()


@dp.message(BotStates.waiting_for_duration)
async def process_duration(message: Message, state: FSMContext):
    try:
        if not is_owner(message.from_user):
            return
        duration = parse_duration(message.text or "")
        if not duration:
            await message.answer(
                "🤔 Не понял. Пример: `1d 2h 30m`",
                parse_mode="Markdown",
            )
            return
        key = _generate_key()
        DATA["keys"][key] = {
            "permanent": False,
            "duration": duration,
            "created_at": time.time(),
            "used_by": None,
        }
        save_data(DATA)
        await message.answer(
            f"⏳ *Временный ключ создан:*\n\n`{key}`\n\n"
            f"⏱ Живёт: {format_duration(duration)}\n"
            f"Активация: `/code {key}`",
            parse_mode="Markdown",
        )
    finally:
        await state.clear()


@dp.callback_query(F.data.startswith("kdel:"))
async def cb_key_delete(cb: CallbackQuery):
    if not is_owner(cb.from_user):
        await cb.answer("Только владелец.", show_alert=True)
        return

    code = cb.data.split(":", 1)[1].strip().upper()
    info = _revoke_by_keycode(code)

    if not info:
        await cb.answer("Ключ уже удалён.", show_alert=True)
    else:
        save_data(DATA)
        msg = f"✅ Ключ {code} удалён."
        if info["used_by"]:
            if info["user_removed"]:
                msg += f"\n🔓 Доступ юзера {info['used_by']} снят."
            else:
                msg += "\n(доступ уже отсутствовал)"
        await cb.answer(msg, show_alert=True)

    try:
        text, kb = _render_keys_list()
        if kb is None:
            await cb.message.edit_text(text, parse_mode="Markdown")
        else:
            await cb.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        print(f"[key] refresh err: {e}")


# --- /revoke command ---

async def _do_revoke(message: Message, arg: str = None):
    if not is_owner(message.from_user):
        await message.answer("Только владелец может отзывать ключи.")
        return

    if message.reply_to_message and message.reply_to_message.from_user:
        tgt = message.reply_to_message.from_user
        if is_owner(tgt):
            await message.answer("Владельца не трогаем.")
            return
        info = _revoke_by_user_id(tgt.id)
        if info:
            save_data(DATA)
            await message.answer(
                f"✅ Доступ @{info['username'] or info['uid']} отозван.\n"
                f"Ключ: {info['key'] or '—'}"
            )
        else:
            await message.answer("🤷 У этого юзера нет активного доступа.")
        return

    if not arg:
        await message.answer(
            "📖 *Как отзывать доступ:*\n\n"
            "• `/revoke KEYCODE` — удалить ключ\n"
            "• `/revoke @username` — отозвать у юзера\n"
            "• `/revoke 123456789` — по user_id\n"
            "• Ответом на сообщение юзера: `/revoke`",
            parse_mode="Markdown",
        )
        return

    arg = arg.strip()

    info = _revoke_by_keycode(arg)
    if info:
        save_data(DATA)
        extra = ""
        if info["used_by"]:
            extra = f"\nДоступ юзера {info['used_by']} снят." if info["user_removed"] else "\n(юзера не было в базе)"
        await message.answer(f"✅ Ключ `{info['key']}` аннулирован.{extra}", parse_mode="Markdown")
        return

    if arg.isdigit():
        info = _revoke_by_user_id(arg)
        if info:
            save_data(DATA)
            await message.answer(
                f"✅ Доступ {info['uid']} (@{info['username'] or '?'}) отозван.\n"
                f"Ключ: {info['key'] or '—'}"
            )
            return

    info = _revoke_by_username(arg.lstrip("@"))
    if info:
        save_data(DATA)
        await message.answer(
            f"✅ Доступ @{info['username']} отозван.\nКлюч: {info['key'] or '—'}"
        )
        return

    await message.answer("🤷 Ни ключ, ни юзер не найдены.")


@dp.message(F.text == "/revoke")
async def cmd_revoke(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    await _do_revoke(message, parts[1] if len(parts) >= 2 else None)


@dp.message(F.text.startswith("/revoke "))
async def cmd_revoke_with_arg(message: Message):
    await _do_revoke(message, message.text.split(maxsplit=1)[1])


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith(".revoke")))
async def dot_revoke(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    await _do_revoke(message, parts[1] if len(parts) >= 2 else None)


# ============================================================
#              STT — АУДИО/ВИДЕО В ТЕКСТ
# ============================================================

def get_stt_model():
    global STT_MODEL
    if STT_MODEL is None:
        print("Загружаю модель распознавания (первый раз долго)...")
        from faster_whisper import WhisperModel
        STT_MODEL = WhisperModel("base", device="cpu", compute_type="int8")
        print("Модель STT загружена")
    return STT_MODEL


def convert_to_wav(src_path: str, wav_path: str):
    cmd = [FFMPEG_EXE_PATH, "-y", "-i", src_path,
           "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:] if r.stderr else "wav convert err")
    return wav_path


def transcribe_wav(wav_path: str):
    model = get_stt_model()
    segments, info = model.transcribe(
        wav_path, language="ru", beam_size=5, vad_filter=True,
        initial_prompt="Голосовое сообщение на русском языке.",
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    return text, info.language


def _stt_pipeline(src_path: str, wav_path: str):
    convert_to_wav(src_path, wav_path)
    return transcribe_wav(wav_path)


async def _handle_stt(message: Message, file_id: str, ext_hint: str = ".ogg"):
    fi = await bot.get_file(file_id)
    src = f"downloads/stt_{file_id}{ext_hint}"
    wav = f"downloads/stt_{file_id}.wav"
    await bot.download_file(fi.file_path, src)

    status = await message.answer("📝 Слушаю... расшифровываю.")
    try:
        loop = asyncio.get_event_loop()
        text, lang = await loop.run_in_executor(None, _stt_pipeline, src, wav)
        if not text:
            await status.edit_text("🤷 Не разобрал ни слова. Может, тихо записано?")
        else:
            preview = text if len(text) <= 3900 else text[:3900] + "..."
            await status.edit_text(f"📝 Расшифровка:\n\n{preview}")
    except Exception as e:
        err = str(e)[:300]
        print(f"stt err: {err}")
        try:
            await status.edit_text(f"😔 Не получилось расшифровать:\n{err}")
        except Exception:
            await message.answer(f"😔 Не получилось расшифровать:\n{err}")
    finally:
        for p in (src, wav):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


# ============================================================
#                   КРЕСТИКИ-НОЛИКИ
# ============================================================

TTT_WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def ttt_board_text(game):
    board = game["board"]
    x_name = game["names"].get("x") or "?"
    o_name = game["names"].get("o") or "ждём игрока..."

    def cell(i):
        v = board[i]
        if v == "X":
            return "❌"
        if v == "O":
            return "⭕"
        return "▫️"

    grid = (
        f"{cell(0)} {cell(1)} {cell(2)}\n"
        f"{cell(3)} {cell(4)} {cell(5)}\n"
        f"{cell(6)} {cell(7)} {cell(8)}"
    )
    if game.get("finished"):
        footer = game.get("footer", "")
    else:
        footer = f"Ход: {'❌' if game['turn'] == 'x' else '⭕'}"
    return (
        f"❌⭕ *Крестики-нолики* ❌⭕\n\n"
        f"❌ {x_name}\n⭕ {o_name}\n\n{grid}\n\n{footer}"
    )


def ttt_keyboard(game):
    board = game["board"]
    b = InlineKeyboardBuilder()
    if not game.get("o_id") and not game.get("finished"):
        b.button(text="🙋 Присоединиться", callback_data="ttt_join")
        return b.as_markup()
    row = []
    for i in range(9):
        if board[i] is None and not game.get("finished"):
            row.append(InlineKeyboardButton(text="⬜", callback_data=f"ttt_move:{i}"))
        else:
            symbol = "❌" if board[i] == "X" else ("⭕" if board[i] == "O" else "⬜")
            row.append(InlineKeyboardButton(text=symbol, callback_data="ttt_noop"))
        if len(row) == 3:
            b.row(*row)
            row = []
    if game.get("finished"):
        b.row(InlineKeyboardButton(text="🔄 Начать заново", callback_data="ttt_reset"))
    else:
        b.row(InlineKeyboardButton(text="🏳️ Сдаться", callback_data="ttt_reset"))
    return b.as_markup()


def ttt_check_winner(board):
    for a, b, c in TTT_WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    if all(cell is not None for cell in board):
        return "draw"
    return None


async def _ttt_refresh(old_message: Message, game: dict, bc_id: str = None):
    chat_id = old_message.chat.id
    text = ttt_board_text(game)
    kb = ttt_keyboard(game)
    if bc_id:
        try:
            await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[old_message.message_id])
        except Exception as e:
            print(f"ttt bc delete err: {e}")
        try:
            await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown",
                                   business_connection_id=bc_id)
        except Exception as e:
            print(f"ttt bc send err: {e}")
    else:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=old_message.message_id,
                                        text=text, reply_markup=kb, parse_mode="Markdown")
        except Exception as e:
            print(f"ttt edit err: {e}")
            try:
                await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")
            except Exception as e2:
                print(f"ttt fallback send err: {e2}")


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith(".ttt")))
async def cmd_ttt(message: Message):
    chat_id = message.chat.id
    if chat_id in TTT_GAMES and not TTT_GAMES[chat_id].get("finished"):
        await message.answer("⚠️ Игра уже идёт. Подожди или сдайся.")
        return
    user = message.from_user
    name = user.full_name or (f"@{user.username}" if user.username else "Игрок")
    TTT_GAMES[chat_id] = {
        "board": [None] * 9, "x_id": user.id, "o_id": None, "turn": "x",
        "names": {"x": name, "o": None}, "finished": False, "footer": "",
    }
    game = TTT_GAMES[chat_id]
    await message.answer(ttt_board_text(game), reply_markup=ttt_keyboard(game), parse_mode="Markdown")


@dp.callback_query(F.data == "ttt_join")
async def cb_ttt_join(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = TTT_GAMES.get(chat_id)
    if not game:
        await cb.answer("Игра не найдена.", show_alert=True)
        return
    if game["finished"] or game.get("o_id"):
        await cb.answer("Место занято.", show_alert=True)
        return
    if cb.from_user.id == game["x_id"]:
        await cb.answer("Ты уже играешь за ❌", show_alert=True)
        return
    user = cb.from_user
    name = user.full_name or (f"@{user.username}" if user.username else "Игрок")
    game["o_id"] = user.id
    game["names"]["o"] = name
    await _ttt_refresh(cb.message, game, game.get("bc_id"))
    await cb.answer("Погнали!")


@dp.callback_query(F.data.startswith("ttt_move:"))
async def cb_ttt_move(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = TTT_GAMES.get(chat_id)
    if not game or game["finished"]:
        await cb.answer("Игра не найдена или закончена.", show_alert=True)
        return
    if not game.get("o_id"):
        await cb.answer("Ещё не все подключились.", show_alert=True)
        return
    turn = game["turn"]
    expected_id = game["x_id"] if turn == "x" else game["o_id"]
    if cb.from_user.id != expected_id:
        await cb.answer("Сейчас не твой ход 🙃", show_alert=True)
        return
    try:
        idx = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Некорректный ход.", show_alert=True)
        return
    if idx < 0 or idx > 8 or game["board"][idx] is not None:
        await cb.answer("Клетка занята.", show_alert=True)
        return
    game["board"][idx] = "X" if turn == "x" else "O"
    winner = ttt_check_winner(game["board"])
    if winner == "X":
        game["finished"] = True
        game["footer"] = f"🏆 *Победа!* ❌ {game['names']['x']}"
    elif winner == "O":
        game["finished"] = True
        game["footer"] = f"🏆 *Победа!* ⭕ {game['names']['o']}"
    elif winner == "draw":
        game["finished"] = True
        game["footer"] = "🤝 *Ничья!*"
    else:
        game["turn"] = "o" if turn == "x" else "x"
    await _ttt_refresh(cb.message, game, game.get("bc_id"))
    await cb.answer()


@dp.callback_query(F.data == "ttt_reset")
async def cb_ttt_reset(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    game = TTT_GAMES.get(chat_id)
    if not game:
        await cb.answer("Игра не найдена.", show_alert=True)
        return
    if cb.from_user.id not in (game["x_id"], game["o_id"]):
        await cb.answer("Только игроки могут сбросить.", show_alert=True)
        return
    game["board"] = [None] * 9
    game["turn"] = "x"
    game["finished"] = False
    game["footer"] = ""
    await _ttt_refresh(cb.message, game, game.get("bc_id"))
    await cb.answer("Поле очищено, ходит ❌")


@dp.callback_query(F.data == "ttt_noop")
async def cb_ttt_noop(cb: CallbackQuery):
    await cb.answer()


# ============================================================
#                          МУТ
# ============================================================

def _target_user_from_message(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    text = message.text or ""
    m = re.search(r"@(\w+)", text)
    if m:
        uname = m.group(1).lower()
        for uid, udata in DATA["users"].items():
            if (udata.get("username") or "").lower() == uname:
                return type("U", (), {"id": int(uid), "username": uname, "full_name": f"@{uname}"})()
    return None


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith(".mute")))
async def cmd_mute(message: Message):
    if not is_owner(message.from_user):
        await message.answer("Только владелец может мутить.")
        return
    target = _target_user_from_message(message)
    if not target:
        await message.answer("Кого мутить? Ответь `.mute` на его сообщение, или укажи @username.")
        return
    if target.id == message.from_user.id:
        await message.answer("Себя мутить? Смысл?")
        return
    if is_owner(target):
        await message.answer("Владельца мутить нельзя.")
        return
    chat_id = message.chat.id
    MUTED.setdefault(chat_id, set()).add(target.id)
    name = target.full_name or (f"@{target.username}" if target.username else str(target.id))
    await message.answer(f"🔇 *МОЛЧАТЬ!!!* {name} в муте.", parse_mode="Markdown")


@dp.message(F.text.func(lambda t: t and t.strip().lower().startswith(".unmute")))
async def cmd_unmute(message: Message):
    if not is_owner(message.from_user):
        await message.answer("Только владелец может снимать мут.")
        return
    target = _target_user_from_message(message)
    if not target:
        await message.answer("Ответь `.unmute` на его сообщение, или укажи @username.")
        return
    chat_id = message.chat.id
    muted_set = MUTED.get(chat_id, set())
    if target.id in muted_set:
        muted_set.discard(target.id)
        name = target.full_name or (f"@{target.username}" if target.username else str(target.id))
        await message.answer(f"Так и быть, говори, {name}.")
    else:
        await message.answer("Он и так не в муте.")


# ============================================================
#              BUSINESS CHAT
# ============================================================

@dp.business_connection()
async def on_business_connection(connection):
    try:
        if connection.is_enabled:
            BUSINESS_CONNECTIONS[connection.id] = connection.user.id
            print(f"Business подключён: {connection.id} -> user {connection.user.id}")
            try:
                await bot.send_message(
                    connection.user.id,
                    "✅ Бот подключён.\n\n"
                    "📖 Команды в бизнес-чате:\n"
                    "• `.ttt` — крестики-нолики\n"
                    "• `.mute` — замутить (ответом)\n"
                    "• `.unmute` — снять мут\n"
                    "• `.revoke` — отозвать доступ",
                )
            except Exception as e:
                print(f"business notify err: {e}")
        else:
            BUSINESS_CONNECTIONS.pop(connection.id, None)
    except Exception as e:
        print(f"business_connection err: {e}")


@dp.business_message(F.text.func(lambda t: t and t.strip().lower().startswith(".ttt")))
async def bc_cmd_ttt(message: Message):
    chat_id = message.chat.id
    bc_id = message.business_connection_id
    if chat_id in TTT_GAMES and not TTT_GAMES[chat_id].get("finished"):
        await bot.send_message(chat_id, "⚠️ Игра уже идёт.", business_connection_id=bc_id)
        return
    user = message.from_user
    name = user.full_name or (f"@{user.username}" if user.username else "Игрок")
    TTT_GAMES[chat_id] = {
        "board": [None] * 9, "x_id": user.id, "o_id": None, "turn": "x",
        "names": {"x": name, "o": None}, "finished": False, "footer": "", "bc_id": bc_id,
    }
    await bot.send_message(chat_id, ttt_board_text(TTT_GAMES[chat_id]),
                           reply_markup=ttt_keyboard(TTT_GAMES[chat_id]),
                           parse_mode="Markdown", business_connection_id=bc_id)


@dp.business_message(F.text.func(lambda t: t and t.strip().lower().startswith(".mute")))
async def bc_cmd_mute(message: Message):
    bc_id = message.business_connection_id
    if not is_owner(message.from_user):
        await bot.send_message(message.chat.id, "Только владелец.", business_connection_id=bc_id)
        return
    target = _target_user_from_message(message)
    if not target:
        await bot.send_message(message.chat.id, "Ответь `.mute` на сообщение жертвы.",
                               business_connection_id=bc_id)
        return
    MUTED.setdefault(message.chat.id, set()).add(target.id)
    name = target.full_name or (f"@{target.username}" if target.username else str(target.id))
    await bot.send_message(message.chat.id, f"🔇 *МОЛЧАТЬ!!!* {name} в муте.",
                           parse_mode="Markdown", business_connection_id=bc_id)


@dp.business_message(F.text.func(lambda t: t and t.strip().lower().startswith(".unmute")))
async def bc_cmd_unmute(message: Message):
    bc_id = message.business_connection_id
    if not is_owner(message.from_user):
        await bot.send_message(message.chat.id, "Только владелец.", business_connection_id=bc_id)
        return
    target = _target_user_from_message(message)
    if not target:
        await bot.send_message(message.chat.id, "Укажи @username.", business_connection_id=bc_id)
        return
    muted_set = MUTED.get(message.chat.id, set())
    if target.id in muted_set:
        muted_set.discard(target.id)
        name = target.full_name or (f"@{target.username}" if target.username else str(target.id))
        await bot.send_message(message.chat.id, f"Так и быть, говори, {name}.",
                               business_connection_id=bc_id)
    else:
        await bot.send_message(message.chat.id, "Он и так не в муте.", business_connection_id=bc_id)


@dp.business_message()
async def bc_mute_enforcer(message: Message):
    chat_id = message.chat.id
    user = message.from_user
    if not user:
        return
    if chat_id in MUTED and user.id in MUTED[chat_id]:
        if is_owner(user):
            return
        bc_id = message.business_connection_id
        try:
            await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
        except Exception as e:
            print(f"bc mute delete err: {e}")
        try:
            await bot.send_message(chat_id, "🔇 МОЛЧАТЬ!!!", business_connection_id=bc_id)
        except Exception as e:
            print(f"bc mute send err: {e}")


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
    if is_owner(message.from_user):
        # Владельцу — без "введи ключ"
        text = (
            "👋 Привет!\n\n"
            "📷 Фото — режу на части 3×N\n"
            "🎬 TikTok — видео или MP3\n"
            "🎵 Аудио — обложка + теги\n"
            "🎤 Голосовое — в текст\n"
            "🎨 Стикерпаки — собираю стикеры\n"
            f"🤫 Шёпот — `@{bot_uname} текст @username`\n\n"
            "👑 Админ-панель включена — кнопки снизу."
        )
        await message.answer(text, reply_markup=owner_reply_kb(), parse_mode=None)
    else:
        text = (
            "👋 Привет!\n\n"
            "📷 Фото — режу на части 3×N\n"
            "🎬 TikTok — видео или MP3\n"
            "🎵 Аудио — обложка + теги\n"
            "🎤 Голосовое — в текст\n"
            "🎨 Стикерпаки — собираю стикеры\n"
            f"🤫 Шёпот — `@{bot_uname} текст @username`\n\n"
            "🔑 Отправь /code ТВОЙ_КЛЮЧ, чтобы начать."
        )
        await message.answer(text, parse_mode=None)


@dp.message(F.text == "/help")
async def cmd_help(message: Message):
    bot_uname = await get_bot_username()
    await message.answer(
        "📖 *Что умею:*\n\n"
        "🎤 *Голосовое / кружок* — расшифрую в текст\n"
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
        await message.answer(
            f"🔑 Твой ключ: `{u.get('key')}`\n♾ Тип: навсегда",
            parse_mode="Markdown",
        )
    else:
        exp = u.get("expires_at", 0)
        rem = max(0, exp - time.time())
        await message.answer(
            f"🔑 Твой ключ: `{u.get('key')}`\n"
            f"⏳ Осталось: *{format_duration(rem)}*\n"
            f"📅 До: *{format_until(exp)}*",
            parse_mode="Markdown",
        )


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


@dp.message(F.voice)
async def process_voice(message: Message, state: FSMContext):
    await state.set_state(None)
    await _handle_stt(message, message.voice.file_id, ".ogg")


@dp.message(F.video_note)
async def process_video_note(message: Message, state: FSMContext):
    await state.set_state(None)
    await _handle_stt(message, message.video_note.file_id, ".mp4")


@dp.message(F.text == "/whisper")
async def cmd_whisper(message: Message):
    bot_uname = await get_bot_username()
    await message.answer(
        "🤫 *Как отправить шёпот:*\n\n"
        f"1. Напиши в любом чате: `@{bot_uname} текст @username`\n"
        f"2. Сверху появится подсказка — выбери её\n"
        f"3. Отправь — прочитает только адресат\n\n"
        f"Шёпот живёт 24 часа и переживает перезапуск бота.",
        parse_mode="Markdown",
    )


# ============================================================
#                  ШЁПОТ
# ============================================================

def _next_whisper_id() -> int:
    c = int(DATA.get("whisper_counter", 1))
    DATA["whisper_counter"] = c + 1
    return c


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
                    f"1. Напиши: `@{bot_uname} текст @username`\n"
                    "2. Выбери подсказку сверху\n"
                    "3. Отправь — прочитает только получатель."
                ),
                parse_mode="Markdown",
            ),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    mentions = [m for m in re.finditer(r"@(\w+)", text) if m.group(1).lower() != bot_uname]

    if not mentions:
        result = InlineQueryResultArticle(
            id="hint_no_target",
            title="🤫 Добавь @username получателя",
            description="Например: привет @vimbrix",
            input_message_content=InputTextMessageContent(
                message_text=f"🤫 *Не хватает получателя.*\n\nНапиши: `@{bot_uname} текст @username`",
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
            input_message_content=InputTextMessageContent(message_text="🤫 Добавь текст перед @username."),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    target_id = None
    for uid, udata in DATA["users"].items():
        if (udata.get("username") or "").lower() == target_username.lower():
            target_id = int(uid)
            break

    wid = _next_whisper_id()
    DATA["whispers"][str(wid)] = {
        "target_id": target_id, "target_name": target_username,
        "text": whisper_text, "from_id": query.from_user.id,
        "from_name": query.from_user.full_name,
        "expires": time.time() + WHISPER_TTL,
    }
    save_data(DATA)
    print(f"whisper #{wid} -> @{target_username}, всего: {len(DATA['whispers'])}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Прочитать содержимое", callback_data=f"whisper:{wid}")],
        [InlineKeyboardButton(text="Как отправлять шепот?", callback_data="whisper_help")],
    ])

    result = InlineQueryResultArticle(
        id=str(wid),
        title=f"🤫 Шёпот для @{target_username}",
        description=whisper_text[:60],
        input_message_content=InputTextMessageContent(
            message_text=f"🔒 Секретное сообщение для @{target_username}\nТолько он может прочитать содержимое",
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
    w = DATA["whispers"].get(str(wid))
    if not w:
        await cb.answer("Этот шёпот уже улетел в никуда.", show_alert=True)
        return
    if w.get("expires", 0) < time.time():
        DATA["whispers"].pop(str(wid), None)
        save_data(DATA)
        await cb.answer("Шёпот устарел и растворился.", show_alert=True)
        return

    is_target = False
    if w.get("target_id") is not None and cb.from_user.id == w["target_id"]:
        is_target = True
    if (cb.from_user.username or "").lower() == (w.get("target_name") or "").lower():
        is_target = True
    is_sender = cb.from_user.id == w.get("from_id")

    if is_sender:
        txt = w["text"]
        if len(txt) <= 180:
            await cb.answer(f"📤 Твой шёпот для @{w['target_name']}:\n\n{txt}", show_alert=True)
        else:
            try:
                await bot.send_message(cb.from_user.id, f"📤 Твой шёпот для @{w['target_name']}:\n\n{txt}")
                await cb.answer("📩 Отправил тебе в личку.", show_alert=True)
            except Exception:
                await cb.answer(f"📤 Твой шёпот для @{w['target_name']}.\n\n{txt[:180]}...", show_alert=True)
    elif is_target:
        txt = w["text"]
        if len(txt) <= 180:
            await cb.answer(f"🤫 {txt}", show_alert=True)
        else:
            try:
                await bot.send_message(cb.from_user.id, f"🤫 Шёпот от {w['from_name']}:\n\n{txt}")
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
#                        СТИКЕРПАКИ
# ============================================================

@dp.message(F.text == "/stickers")
async def cmd_stickers(message: Message, state: FSMContext):
    await state.set_state(None)
    b = InlineKeyboardBuilder()
    b
