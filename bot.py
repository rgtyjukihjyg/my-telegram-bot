import os
import re
import json
import time
import html as html_mod
import asyncio
import secrets
import random
import shutil
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

_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
_session_kwargs = {"timeout": 60}
if _proxy:
    _session_kwargs["proxy"] = _proxy
    print(f"Использую прокси: {_proxy}")

session = AiohttpSession(**_session_kwargs)
bot = Bot(
    token=BOT_TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode="HTML"),
)
dp = Dispatcher()

BOT_USERNAME_CACHE = None
STT_MODEL = None

TTT_GAMES = {}
MUTED = {}
BUSINESS_CONNECTIONS = {}
DEAD_BC_NOTIFIED = set()

WHISPER_TTL = 86400

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
    return {
        "keys": {}, "users": {}, "sticker_packs": {},
        "whispers": {}, "whisper_counter": 1,
        "business_owners": {},
    }


def _validate_data(d):
    if not isinstance(d, dict):
        return _empty_data()
    d.setdefault("keys", {})
    d.setdefault("users", {})
    d.setdefault("sticker_packs", {})
    d.setdefault("whispers", {})
    d.setdefault("whisper_counter", 1)
    d.setdefault("business_owners", {})
    return d


def load_data_from_file():
    for path in (DATA_FILE, DATA_FILE + ".bak"):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                print(f"load_data_from_file: OK из {path} "
                      f"(keys={len(d.get('keys', {}))}, users={len(d.get('users', {}))})")
                return _validate_data(d)
            except Exception as e:
                print(f"load_data_from_file {path}: {e}")
    return _empty_data()


def save_data_to_file(data):
    try:
        if os.path.exists(DATA_FILE):
            try:
                shutil.copy2(DATA_FILE, DATA_FILE + ".bak")
            except Exception as e:
                print(f"backup err: {e}")
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        print(f"save_data_to_file: {e}")


async def _tg_api_get(url, params=None, timeout=30):
    import aiohttp
    last_err = None
    for attempt in range(3):
        try:
            t = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get(url, params=params) as r:
                    return await r.json()
        except Exception as e:
            last_err = e
            print(f"_tg_api_get attempt {attempt+1}: {str(e)[:150]}")
            if attempt < 2:
                await asyncio.sleep(2)
    raise last_err if last_err else RuntimeError("get failed")


async def _tg_download_file(url, timeout=60):
    import aiohttp
    last_err = None
    for attempt in range(3):
        try:
            t = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get(url) as r:
                    return await r.read()
        except Exception as e:
            last_err = e
            print(f"_tg_download_file attempt {attempt+1}: {str(e)[:150]}")
            if attempt < 2:
                await asyncio.sleep(2)
    raise last_err if last_err else RuntimeError("download failed")


async def load_data_from_tg():
    if not STORAGE_CHAT_ID:
        print("STORAGE_CHAT_ID не задан, читаю из файла")
        return load_data_from_file()

    for attempt in range(4):
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
            resp = await _tg_api_get(url, params={"chat_id": STORAGE_CHAT_ID})
            if not resp.get("ok"):
                print(f"load_data_from_tg getChat: {resp}")
                break

            pinned = resp["result"].get("pinned_message")
            if not pinned or "document" not in pinned:
                print("load_data_from_tg: нет закреплённого файла, читаю из file")
                return load_data_from_file()

            file_id = pinned["document"]["file_id"]

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile"
            fdata = await _tg_api_get(url, params={"file_id": file_id})
            if not fdata.get("ok"):
                print(f"load_data_from_tg getFile: {fdata}")
                break

            file_path = fdata["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            content = await _tg_download_file(url)

            d = json.loads(content.decode("utf-8"))
            d = _validate_data(d)
            print(f"load_data_from_tg OK: keys={len(d['keys'])}, "
                  f"users={len(d['users'])}, whispers={len(d['whispers'])}")
            save_data_to_file(d)
            return d

        except Exception as e:
            print(f"load_data_from_tg attempt {attempt+1} err: {str(e)[:150]}")
            if attempt < 3:
                await asyncio.sleep(3 * (attempt + 1))

    print("load_data_from_tg: все попытки провалились, читаю локально")
    return load_data_from_file()


async def save_data_to_tg(data):
    save_data_to_file(data)
    if not STORAGE_CHAT_ID:
        return
    try:
        import aiohttp
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        t = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=t) as s:
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

        async with aiohttp.ClientSession(timeout=t) as s:
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
        print(f"save_data_to_tg err: {str(e)[:200]}")


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


def _is_peer_invalid(err: Exception) -> bool:
    s = str(err)
    return "BUSINESS_PEER_INVALID" in s


def _is_peer_usage_missing(err: Exception) -> bool:
    s = str(err)
    return "BUSINESS_PEER_USAGE_MISSING" in s


async def _check_business_connection(bc_id: str) -> bool:
    """Проверяет, активно ли бизнес-соединение."""
    entry = BUSINESS_CONNECTIONS.get(bc_id)
    if entry and entry.get("is_enabled", True):
        return True
    return False


async def _notify_owner_bc_dead(bc_id: str, chat_id: int = None):
    """Сообщает владельцу (или самому боту-владельцу) что bc_id умер."""
    if bc_id in DEAD_BC_NOTIFIED:
        if chat_id:
            if chat_id in TTT_GAMES and TTT_GAMES[chat_id].get("bc_id") == bc_id:
                TTT_GAMES.pop(chat_id, None)
            MUTED.pop(chat_id, None)
        return

    owner_id = None

    bc_entry = BUSINESS_CONNECTIONS.get(bc_id)
    if bc_entry and isinstance(bc_entry, dict):
        owner_id = bc_entry.get("user_id")

    if not owner_id:
        owner_id = DATA.get("business_owners", {}).get(bc_id)

    if not owner_id:
        owner_id = OWNER_ID
        print(f"[bc] bc_id={bc_id} неизвестен, шлю уведомление OWNER_ID={OWNER_ID}")

    DEAD_BC_NOTIFIED.add(bc_id)

    try:
        await bot.send_message(
            owner_id,
            "⚠️ <b>Бизнес-подключение устарело</b>\n\n"
            f"ID: <code>{html_mod.escape(bc_id[:24])}...</code>\n\n"
            "Telegram сбросил соединение бота с Business-аккаунтом. "
            "Команды в бизнес-чатах (<code>.ttt</code>, <code>.mute</code> и т.п.) "
            "больше не работают в этом чате.\n\n"
            "🔧 <b>Как починить:</b>\n"
            "1. Telegram → <b>Настройки</b>\n"
            "2. <b>Telegram Business</b> → <b>Чат-боты</b>\n"
            "3. Удали этого бота и добавь заново\n\n"
            "После переподключения всё заработает.",
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"[bc] не смог уведомить {owner_id}: {str(e)[:150]}")

    BUSINESS_CONNECTIONS.pop(bc_id, None)
    DATA.get("business_owners", {}).pop(bc_id, None)

    if chat_id:
        if chat_id in TTT_GAMES and TTT_GAMES[chat_id].get("bc_id") == bc_id:
            TTT_GAMES.pop(chat_id, None)
        MUTED.pop(chat_id, None)


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
                    print(f"mute delete err: {str(e)[:150]}")
                try:
                    await bot.send_message(chat_id, "🔇 МОЛЧАТЬ!!!")
                except Exception as e:
                    print(f"mute send err: {str(e)[:150]}")
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
                print(f"expired send err: {str(e)[:150]}")
            return

        try:
            await event.answer(
                "🔒 Доступ только по ключу.\n\n"
                "1. Получи ключ у @vimbrix\n"
                "2. Отправь: /code ТВОЙ_КЛЮЧ\n\n"
                "Если уже активировал — /mykey покажет статус."
            )
        except Exception as e:
            print(f"BLOCKED send err: {str(e)[:150]}")


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
                    f"⏳ Осталось: <b>{format_duration(rem)}</b>\n"
                    f"📅 До: <b>{format_until(exp)}</b>",
                    parse_mode="HTML",
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
        await event.answer("✅ <b>Ключ активирован!</b>\n\n♾ Тип: навсегда", parse_mode="HTML")
    else:
        await event.answer(
            f"✅ <b>Ключ активирован!</b>\n\n"
            f"⏱ Ключ действует: <b>{format_duration(duration)}</b>\n"
            f"📅 Дата истечения: <b>{format_until(now + duration)}</b>",
            parse_mode="HTML",
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
#                  КЛЮЧИ
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
    if kd.get("used_by"):
        u = DATA["users"].get(str(kd["used_by"]), {})
        uname = u.get("username") or f"id{kd['used_by']}"
        uname_safe = html_mod.escape(uname)
        if u.get("permanent"):
            return f"♾ активирован @{uname_safe}"
        exp = u.get("expires_at", 0)
        if exp > time.time():
            rem = max(0, exp - time.time())
            return f"⏳ @{uname_safe} — осталось {format_duration(rem)} (до {format_until(exp)})"
        return f"❌ @{uname_safe} — истёк"
    if kd.get("permanent"):
        return "♾ свободен"
    return f"⏳ {format_duration(kd.get('duration', 0))}, свободен"


def _render_keys_list():
    keys = DATA["keys"]
    if not keys:
        return "🗂 Пока ни одного ключа нет.", None

    free_keys = [(k, v) for k, v in keys.items() if not v.get("used_by")]
    used_keys = [(k, v) for k, v in keys.items() if v.get("used_by")]

    lines = [f"🗂 <b>Ключи</b> (всего: {len(keys)})\n"]

    if free_keys:
        lines.append(f"<b>Свободные ({len(free_keys)}):</b>")
        for k, kd in free_keys:
            lines.append(f"<code>{html_mod.escape(k)}</code> — {_key_status_text(kd)}")
        lines.append("")

    if used_keys:
        lines.append(f"<b>Активированные ({len(used_keys)}):</b>")
        for k, kd in used_keys:
            lines.append(f"<code>{html_mod.escape(k)}</code> — {_key_status_text(kd)}")

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
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        print(f"[key] html send err: {str(e)[:150]}")
        try:
            plain = re.sub(r"<[^>]+>", "", text)
            if kb is None:
                await message.answer(plain)
            else:
                await message.answer(plain, reply_markup=kb)
        except Exception as e2:
            print(f"[key] plain send err: {str(e2)[:150]}")
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
        f"♾ <b>Перманентный ключ создан:</b>\n\n"
        f"<code>{html_mod.escape(key)}</code>\n\n"
        f"Отправь его юзеру.\nАктивация: <code>/code {html_mod.escape(key)}</code>",
        parse_mode="HTML",
    )
    await cb.answer("Готово!")


@dp.callback_query(F.data == "kc:temp")
async def cb_kc_temp(cb: CallbackQuery, state: FSMContext):
    if not is_owner(cb.from_user):
        await cb.answer("Только владелец.", show_alert=True)
        return
    await cb.message.edit_text(
        "⏳ На сколько ключ? Формат: <code>1d 2h 30m</code>\n"
        "(d — дни, h — часы, m — минуты, s — секунды)\n\n"
        "Пример: <code>24h</code> или <code>7d</code>",
        parse_mode="HTML",
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
                "🤔 Не понял. Пример: <code>1d 2h 30m</code>",
                parse_mode="HTML",
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
            f"⏳ <b>Временный ключ создан:</b>\n\n"
            f"<code>{html_mod.escape(key)}</code>\n\n"
            f"⏱ Живёт: {format_duration(duration)}\n"
            f"Активация: <code>/code {html_mod.escape(key)}</code>",
            parse_mode="HTML",
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
            await cb.message.edit_text(text, parse_mode="HTML")
        else:
            await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        print(f"[key] refresh err: {str(e)[:150]}")


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
                f"✅ Доступ @{html_mod.escape(info['username'] or info['uid'])} отозван.\n"
                f"Ключ: {info['key'] or '—'}",
                parse_mode="HTML",
            )
        else:
            await message.answer("🤷 У этого юзера нет активного доступа.")
        return

    if not arg:
        await message.answer(
            "📖 <b>Как отзывать доступ:</b>\n\n"
            "• <code>/revoke KEYCODE</code> — удалить ключ\n"
            "• <code>/revoke @username</code> — отозвать у юзера\n"
            "• <code>/revoke 123456789</code> — по user_id\n"
            "• Ответом на сообщение юзера: <code>/revoke</code>",
            parse_mode="HTML",
        )
        return

    arg = arg.strip()

    info = _revoke_by_keycode(arg)
    if info:
        save_data(DATA)
        extra = ""
        if info["used_by"]:
            extra = f"\nДоступ юзера {info['used_by']} снят." if info["user_removed"] else "\n(юзера не было в базе)"
        await message.answer(
            f"✅ Ключ <code>{html_mod.escape(info['key'])}</code> аннулирован.{extra}",
            parse_mode="HTML",
        )
        return

    if arg.isdigit():
        info = _revoke_by_user_id(arg)
        if info:
            save_data(DATA)
            await message.answer(
                f"✅ Доступ {info['uid']} (@{html_mod.escape(info['username'] or '?')}) отозван.\n"
                f"Ключ: {info['key'] or '—'}",
                parse_mode="HTML",
            )
            return

    info = _revoke_by_username(arg.lstrip("@"))
    if info:
        save_data(DATA)
        await message.answer(
            f"✅ Доступ @{html_mod.escape(info['username'])} отозван.\nКлюч: {info['key'] or '—'}",
            parse_mode="HTML",
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
        f"❌⭕ <b>Крестики-нолики</b> ❌⭕\n\n"
        f"❌ {html_mod.escape(x_name)}\n⭕ {html_mod.escape(o_name)}\n\n"
        f"{grid}\n\n{footer}"
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
        if not await _check_business_connection(bc_id):
            await _notify_owner_bc_dead(bc_id, chat_id)
            return
        try:
            await bot.delete_business_messages(
                business_connection_id=bc_id,
                message_ids=[old_message.message_id],
            )
        except Exception as e:
            err = str(e)
            print(f"ttt bc delete err: {err[:200]}")
            if _is_peer_invalid(e):
                await _notify_owner_bc_dead(bc_id, chat_id)
                return
        try:
            await bot.send_message(
                chat_id, text,
                reply_markup=kb,
                parse_mode="HTML",
                business_connection_id=bc_id,
            )
        except Exception as e:
            err = str(e)
            print(f"ttt bc send err: {err[:200]}")
            if _is_peer_invalid(e):
                await _notify_owner_bc_dead(bc_id, chat_id)
    else:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_message.message_id,
                text=text,
                reply_markup=kb,
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"ttt edit err: {str(e)[:150]}")
            try:
                await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
            except Exception as e2:
                print(f"ttt fallback send err: {str(e2)[:150]}")


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
    await message.answer(ttt_board_text(game), reply_markup=ttt_keyboard(game), parse_mode="HTML")


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
        game["footer"] = f"🏆 <b>Победа!</b> ❌ {html_mod.escape(game['names']['x'])}"
    elif winner == "O":
        game["finished"] = True
        game["footer"] = f"🏆 <b>Победа!</b> ⭕ {html_mod.escape(game['names']['o'])}"
    elif winner == "draw":
        game["finished"] = True
        game["footer"] = "🤝 <b>Ничья!</b>"
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
    await message.answer(f"🔇 <b>МОЛЧАТЬ!!!</b> {html_mod.escape(name)} в муте.", parse_mode="HTML")


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
        await message.answer(f"Так и быть, говори, {html_mod.escape(name)}.")
    else:
        await message.answer("Он и так не в муте.")


# ============================================================
#              BUSINESS CHAT
# ============================================================

@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    try:
        if connection.is_enabled:
            BUSINESS_CONNECTIONS[connection.id] = {
                "user_id": connection.user.id,
                "is_enabled": True,
                "can_reply": getattr(connection, "can_reply", True),
            }
            DATA.setdefault("business_owners", {})[connection.id] = connection.user.id
            save_data(DATA)
            print(f"Business подключён: {connection.id} -> user {connection.user.id}")
            try:
                await bot.send_message(
                    connection.user.id,
                    "✅ Бот подключён.\n\n"
                    "📖 Команды в бизнес-чате:\n"
                    "• <code>.ttt</code> — крестики-нолики\n"
                    "• <code>.mute</code> — замутить (ответом)\n"
                    "• <code>.unmute</code> — снять мут\n"
                    "• <code>.revoke</code> — отозвать доступ",
                    parse_mode="HTML",
                )
            except Exception as e:
                print(f"business notify err: {str(e)[:200]}")
        else:
            BUSINESS_CONNECTIONS.pop(connection.id, None)
            DATA.get("business_owners", {}).pop(connection.id, None)
            save_data(DATA)
            for cid, game in list(TTT_GAMES.items()):
                if game.get("bc_id") == connection.id:
                    TTT_GAMES.pop(cid, None)
            print(f"Business отключён: {connection.id}")
    except Exception as e:
        print(f"business_connection err: {str(e)[:200]}")


@dp.business_message(F.text.func(lambda t: t and t.strip().lower().startswith(".ttt")))
async def bc_cmd_ttt(message: Message):
    try:
        chat_id = message.chat.id
        bc_id = message.business_connection_id
        if not await _check_business_connection(bc_id):
            await _notify_owner_bc_dead(bc_id, chat_id)
            return
        if chat_id in TTT_GAMES and not TTT_GAMES[chat_id].get("finished"):
            try:
                await bot.send_message(chat_id, "⚠️ Игра уже идёт.", business_connection_id=bc_id)
            except Exception as e:
                if _is_peer_invalid(e):
                    await _notify_owner_bc_dead(bc_id, chat_id)
                    return
            return
        user = message.from_user
        name = user.full_name or (f"@{user.username}" if user.username else "Игрок")
        TTT_GAMES[chat_id] = {
            "board": [None] * 9, "x_id": user.id, "o_id": None, "turn": "x",
            "names": {"x": name, "o": None}, "finished": False, "footer": "", "bc_id": bc_id,
        }
        await bot.send_message(
            chat_id, ttt_board_text(TTT_GAMES[chat_id]),
            reply_markup=ttt_keyboard(TTT_GAMES[chat_id]),
            parse_mode="HTML", business_connection_id=bc_id,
        )
    except Exception as e:
        err = str(e)
        print(f"bc_cmd_ttt err: {err[:200]}")
        if _is_peer_invalid(e):
            await _notify_owner_bc_dead(message.business_connection_id, message.chat.id)


@dp.business_message(F.text.func(lambda t: t and t.strip().lower().startswith(".mute")))
async def bc_cmd_mute(message: Message):
    try:
        bc_id = message.business_connection_id
        if not await _check_business_connection(bc_id):
            await _notify_owner_bc_dead(bc_id, message.chat.id)
            return
        if not is_owner(message.from_user):
            try:
                await bot.send_message(message.chat.id, "Только владелец.", business_connection_id=bc_id)
            except Exception as e:
                if _is_peer_invalid(e):
                    await _notify_owner_bc_dead(bc_id, message.chat.id)
            return
        target = _target_user_from_message(message)
        if not target:
            try:
                await bot.send_message(message.chat.id, "Ответь `.mute` на сообщение жертвы.",
                                       business_connection_id=bc_id)
            except Exception:
                pass
            return
        MUTED.setdefault(message.chat.id, set()).add(target.id)
        name = target.full_name or (f"@{target.username}" if target.username else str(target.id))
        try:
            await bot.send_message(message.chat.id, f"🔇 <b>МОЛЧАТЬ!!!</b> {html_mod.escape(name)} в муте.",
                                   parse_mode="HTML", business_connection_id=bc_id)
        except Exception as e:
            err = str(e)
            print(f"bc mute reply err: {err[:200]}")
            if _is_peer_invalid(e):
                await _notify_owner_bc_dead(bc_id, message.chat.id)
    except Exception as e:
        print(f"bc_cmd_mute err: {str(e)[:200]}")


@dp.business_message(F.text.func(lambda t: t and t.strip().lower().startswith(".unmute")))
async def bc_cmd_unmute(message: Message):
    try:
        bc_id = message.business_connection_id
        if not await _check_business_connection(bc_id):
            await _notify_owner_bc_dead(bc_id, message.chat.id)
            return
        if not is_owner(message.from_user):
            try:
                await bot.send_message(message.chat.id, "Только владелец.", business_connection_id=bc_id)
            except Exception:
                pass
            return
        target = _target_user_from_message(message)
        if not target:
            try:
                await bot.send_message(message.chat.id, "Укажи @username.", business_connection_id=bc_id)
            except Exception:
                pass
            return
        muted_set = MUTED.get(message.chat.id, set())
        if target.id in muted_set:
            muted_set.discard(target.id)
            name = target.full_name or (f"@{target.username}" if target.username else str(target.id))
            try:
                await bot.send_message(message.chat.id, f"Так и быть, говори, {html_mod.escape(name)}.",
                                       business_connection_id=bc_id)
            except Exception as e:
                if _is_peer_invalid(e):
                    await _notify_owner_bc_dead(bc_id, message.chat.id)
        else:
            try:
                await bot.send_message(message.chat.id, "Он и так не в муте.", business_connection_id=bc_id)
            except Exception as e:
                if _is_peer_invalid(e):
                    await _notify_owner_bc_dead(bc_id, message.chat.id)
    except Exception as e:
        print(f"bc_cmd_unmute err: {str(e)[:200]}")


@dp.business_message()
async def bc_mute_enforcer(message: Message):
    try:
        chat_id = message.chat.id
        user = message.from_user
        if not user:
            return
        if chat_id in MUTED and user.id in MUTED[chat_id]:
            if is_owner(user):
                return
            bc_id = message.business_connection_id
            if not bc_id:
                return
            if not await _check_business_connection(bc_id):
                await _notify_owner_bc_dead(bc_id, chat_id)
                return
            try:
                await bot.delete_business_messages(
                    business_connection_id=bc_id,
                    message_ids=[message.message_id],
                )
            except Exception as e:
                err = str(e)
                print(f"bc mute delete err: {err[:200]}")
                if _is_peer_invalid(e):
                    await _notify_owner_bc_dead(bc_id, chat_id)
                return
            try:
                await bot.send_message(
                    chat_id, "🔇 МОЛЧАТЬ!!!",
                    business_connection_id=bc_id,
                )
            except Exception as e:
                err = str(e)
                print(f"bc mute send err: {err[:200]}")
                if _is_peer_invalid(e):
                    await _notify_owner_bc_dead(bc_id, chat_id)
    except Exception as e:
        print(f"bc_mute_enforcer err: {str(e)[:200]}")


# ============================================================
#                         HANDLERS
# ============================================================

@dp.message(F.text == "/whoami")
async def cmd_whoami(message: Message):
    u = message.from_user
    await message.answer(
        f"ID: {u.id}\nUsername: @{html_mod.escape(u.username or 'нет')}\n"
        f"Имя: {html_mod.escape(u.full_name)}\n\n"
        f"Ожидаемый владелец: @{OWNER_USERNAME}\nOWNER_ID: {OWNER_ID or 'не задан'}\n\n"
        f"Ты владелец? {'ДА' if is_owner(u) else 'НЕТ'}",
        parse_mode="HTML",
    )


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    bot_uname = await get_bot_username()
    if is_owner(message.from_user):
        text = (
            "👋 Привет!\n\n"
            "📷 Фото — режу на части 3×N\n"
            "🎬 TikTok — видео или MP3\n"
            "🎵 Аудио — обложка + теги\n"
            "🎤 Голосовое — в текст\n"
            "🎨 Стикерпаки — собираю стикеры\n"
            f"🤫 Шёпот — <code>@{bot_uname} текст @username</code>\n\n"
            "👑 Админ-панель включена — кнопки снизу."
        )
        await message.answer(text, reply_markup=owner_reply_kb(), parse_mode="HTML")
    else:
        text = (
            "👋 Привет!\n\n"
            "📷 Фото — режу на части 3×N\n"
            "🎬 TikTok — видео или MP3\n"
            "🎵 Аудио — обложка + теги\n"
            "🎤 Голосовое — в текст\n"
            "🎨 Стикерпаки — собираю стикеры\n"
            f"🤫 Шёпот — <code>@{bot_uname} текст @username</code>\n\n"
            "🔑 Отправь /code ТВОЙ_КЛЮЧ, чтобы начать."
        )
        await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "/help")
async def cmd_help(message: Message):
    bot_uname = await get_bot_username()
    await message.answer(
        "📖 <b>Что умею:</b>\n\n"
        "🎤 <b>Голосовое / кружок</b> — расшифрую в текст\n"
        "🎨 <code>/stickers</code> — стикерпаки\n"
        "🤫 <code>/whisper</code> — как отправить шёпот\n"
        "🔑 <code>/mykey</code> — статус ключа\n\n"
        "📷 <b>Фото</b> — режу на 3×N\n"
        "🎬 <b>TikTok-ссылка</b> — видео или MP3\n"
        "🎵 <b>Аудиофайл</b> — обложка и теги\n\n"
        f"🤫 <b>Шёпот в чате:</b> <code>@{bot_uname} текст @username</code>",
        parse_mode="HTML",
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
            f"🔑 Твой ключ: <code>{html_mod.escape(u.get('key'))}</code>\n"
            f"♾ Тип: навсегда",
            parse_mode="HTML",
        )
    else:
        exp = u.get("expires_at", 0)
        rem = max(0, exp - time.time())
        await message.answer(
            f"🔑 Твой ключ: <code>{html_mod.escape(u.get('key'))}</code>\n"
            f"⏳ Осталось: <b>{format_duration(rem)}</b>\n"
            f"📅 До: <b>{format_until(exp)}</b>",
            parse_mode="HTML",
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
        "🤫 <b>Как отправить шёпот:</b>\n\n"
        f"1. Напиши в любом чате: <code>@{bot_uname} текст @username</code>\n"
        f"2. Сверху появится подсказка — выбери её\n"
        f"3. Отправь — прочитает только адресат\n\n"
        f"Шёпот живёт 24 часа и переживает перезапуск бота.",
        parse_mode="HTML",
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
                    "🤫 <b>Как отправить шёпот:</b>\n\n"
                    f"1. Напиши: <code>@{bot_uname} текст @username</code>\n"
                    "2. Выбери подсказку сверху\n"
                    "3. Отправь — прочитает только получатель."
                ),
                parse_mode="HTML",
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
                message_text=f"🤫 <b>Не хватает получателя.</b>\n\nНапиши: <code>@{bot_uname} текст @username</code>",
                parse_mode="HTML",
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
    b.button(text="➕ Создать новый", callback_data="st_create")
    b.button(text="📎 Добавить в существующий", callback_data="st_add")
    b.button(text="📂 Мои паки", callback_data="st_list")
    b.adjust(1)
    await message.answer(
        "🎨 <b>Стикерпаки</b>\n\n"
        "Создай свой пак и добавляй туда стикеры из фото.\n"
        "В имени пака будет метка бота.",
        reply_markup=b.as_markup(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "st_create")
async def cb_st_create(cb: CallbackQuery, state: FSMContext):
    bot_uname = await get_bot_username()
    await cb.message.edit_text(
        "📝 Придумай короткое имя для пака.\n"
        "Только латиница, цифры, <code>_</code>. Начинается с буквы.\n"
        f"В Telegram пак будет: <code>имя_by_{bot_uname}</code>.\n\n"
        "Пример: <code>mycats</code>",
        parse_mode="HTML",
    )
    await state.set_state(BotStates.waiting_sticker_name)
    await cb.answer()


@dp.message(BotStates.waiting_sticker_name)
async def process_sticker_name(message: Message, state: FSMContext):
    short = (message.text or "").strip().lower()
    if not validate_pack_shortname(short):
        await message.answer(
            "⚠️ Не подходит. Только латиница, цифры, <code>_</code>. Начинается с буквы.",
            parse_mode="HTML",
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
        f"Пример: <code>Мои котики</code>",
        parse_mode="HTML",
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
            f"📛 {html_mod.escape(title)}\n"
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
        lines.append(f"• {html_mod.escape(p['title'])}\n  https://t.me/addstickers/{p['name']}")
    await cb.message.edit_text("\n".join(lines), parse_mode="HTML")
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
        f"📎 Пак: {html_mod.escape(pack['title'])}\n\n"
        f"Пришли картинку для стикера.",
        parse_mode="HTML",
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
        await status.edit_text(f"✅ Стикер добавлен в {html_mod.escape(pack_name)}", parse_mode="HTML")
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
#              АУДИО: ОБЛОЖКА + ТЕГИ
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


# ============================================================
#         ОБРАБОТКА ФОТО (нарезка)
# ============================================================

@dp.message(F.document & F.document.mime_type.startswith("image/"))
async def process_photo_document(message: Message, state: FSMContext):
    d = message.document
    fi = await bot.get_file(d.file_id)
    ext = os.path.splitext(d.file_name or "img.png")[1] or ".png"
    lp = f"temp_photos/{d.file_id}{ext}"
    await bot.download_file(fi.file_path, lp)
    await state.update_data(photo_path=lp)
    await state.set_state(BotStates.waiting_for_parts)
    b = InlineKeyboardBuilder()
    b.button(text="🎲 На твоё усмотрение", callback_data="auto_split")
    b.button(text="📎 Отправить оригинал", callback_data="send_original")
    await message.answer(
        "✂️ На сколько кусочков резать?\n"
        "Число должно делиться на 3 (например, 6, 9, 12).\n"
        "Или жми кнопку — сам прикину.\n\n"
        "✅ Файл принят в оригинальном качестве.",
        reply_markup=b.as_markup(),
    )


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
    b.button(text="📎 Отправить оригинал", callback_data="send_original")
    await message.answer(
        "⚠️ Фото сжато Telegram — мелкие детали (сердечки, тонкие линии) могут быть потеряны.\n"
        "Чтобы сохранить качество, пришли картинку <b>как файл</b> (📎 → Файл).\n\n"
        "✂️ На сколько кусочков резать? (кратно 3)\n"
        "Или жми кнопку.",
        reply_markup=b.as_markup(),
        parse_mode="HTML",
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


@dp.callback_query(F.data == "send_original", BotStates.waiting_for_parts)
async def send_original(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    pp = d.get("photo_path")
    if not pp or not os.path.exists(pp):
        await cb.answer("Файл потерялся", show_alert=True)
        await state.clear()
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer_document(
        FSInputFile(pp, filename=os.path.basename(pp)),
        caption="📎 Оригинал без изменений."
    )
    await state.clear()
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

        def sort_key(path):
            m = re.search(r'part_(\d+)_(\d+)', path)
            if m:
                return (int(m.group(1)), int(m.group(2)))
            return (999, 999)

        parts = sorted(parts, key=sort_key)
        total = len(parts)
        print(f"split: {total} частей")

        for i, pf in enumerate(parts):
            if not os.path.exists(pf):
                continue
            idx = i + 1
            await msg_obj.answer_photo(
                photo=FSInputFile(pf, filename=f"{idx:02d}.png"),
                caption=f"{idx}/{total}",
            )
            await asyncio.sleep(0.4)

        for pf in parts:
            if os.path.exists(pf):
                try:
                    os.remove(pf)
                except Exception:
                    pass

        await st.delete()
        await msg_obj.answer(
            f"✅ Готово! {total} кусочков.\n"
            f"Порядок: слева-направо, сверху-вниз (01 → {total:02d})."
        )
    except Exception as e:
        print(f"split err: {e}")
        await msg_obj.answer(f"❌ Ошибка: {e}")
    finally:
        if os.path.exists(pp):
            os.remove(pp)
        await state.clear()


# ============================================================
#         СКАЧИВАНИЕ ССЫЛОК
# ============================================================

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


# ============================================================
#                      ЗАПУСК
# ============================================================

async def keep_alive():
    while True:
        try:
            await asyncio.sleep(240)
            me = await bot.get_me()
            print(f"keep-alive: @{me.username}")
        except Exception as e:
            print(f"keep-alive: {str(e)[:200]}")


async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="code", description="Активировать ключ"),
        BotCommand(command="mykey", description="Мой ключ"),
        BotCommand(command="stickers", description="Стикерпаки"),
        BotCommand(command="whisper", description="Как отправить шёпот"),
        BotCommand(command="cancel", description="Отменить действие"),
    ]
    try:
        await bot.set_my_commands(commands)
        print("Команды установлены")
    except Exception as e:
        print(f"set_my_commands err: {str(e)[:150]}")


async def set_bot_menu():
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        print("Меню-кнопка установлена")
    except Exception as e:
        print(f"set_chat_menu_button err: {str(e)[:150]}")


async def preload_stt():
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, get_stt_model)
    except Exception as e:
        print(f"STT preload failed: {e}")


dp.message.middleware(AccessMiddleware())


async def main():
    global DATA
    DATA = await load_data_from_tg()
    cleanup_expired_whispers()
    print(f"keys: {len(DATA['keys'])}, users: {len(DATA['users'])}, whispers: {len(DATA['whispers'])}")
    await set_bot_commands()
    await set_bot_menu()
    asyncio.create_task(preload_stt())
    print("Bot started")
    asyncio.create_task(keep_alive())
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("stopped")
