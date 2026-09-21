import os
import re
import json
import asyncio
import urllib.request
import urllib.parse
import subprocess
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from PIL import Image
import yt_dlp
from static_ffmpeg import run as static_ffmpeg_run

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ Не задана переменная окружения BOT_TOKEN")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()

class BotStates(StatesGroup):
    waiting_for_parts = State()
    waiting_for_media_type = State()
    waiting_for_cover = State()
    waiting_for_meta = State()

os.makedirs("downloads", exist_ok=True)
os.makedirs("temp_photos", exist_ok=True)

# --- Чистка папок при старте ---
def _cleanup_folder(folder):
    if os.path.exists(folder):
        for f in os.listdir(folder):
            path = os.path.join(folder, f)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception as e:
                print(f"⚠ Не удалось удалить {path}: {e}")

_cleanup_folder("downloads")
_cleanup_folder("temp_photos")
print("🧹 Временные папки очищены")

# --- FFMPEG ---
print("🔧 Проверяю ffmpeg / ffprobe...")
FFMPEG_EXE_PATH, FFPROBE_EXE_PATH = static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
FFMPEG_DIR = os.path.dirname(FFMPEG_EXE_PATH)
print(f"✅ ffmpeg:  {FFMPEG_EXE_PATH}")
print(f"✅ ffprobe: {FFPROBE_EXE_PATH}")


# ============================================================
#         УТИЛИТЫ ЧИСТКИ МЕТАДАННЫХ
# ============================================================
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F000-\U0001F2FF"
    "\U0001F900-\U0001F9FF"
    "\U00002700-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U0001FA00-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)
DASH_RE = re.compile(r"\s*[-–—−―]\s*")


def clean_meta(s, max_len=64, strip_author: str = "") -> str:
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
        author_clean = strip_author.strip().lower()
        for _ in range(3):
            low = s.lower()
            if low.startswith(author_clean):
                s = s[len(author_clean):].lstrip(" -–—−|.,:;·•»«\"'")
            else:
                break

    parts = DASH_RE.split(s, maxsplit=1)
    if len(parts) == 2 and len(parts[0].split()) >= 1 and len(parts[1].split()) >= 1:
        s = parts[0] if len(parts[0]) >= len(parts[1]) else parts[1]

    s = s.strip(" -–—−|.,:;·•»«\"'")
    s = " ".join(s.split())
    return s[:max_len].strip()


# ============================================================
#       YOUTUBE через Piped API (обход блокировки IP)
# ============================================================
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://api.piped.projectsegfau.lt",
    "https://pipedapi-libre.kavin.rocks",
    "https://pipedapi.reallyaweso.me",
]

def youtube_via_piped(url, mode):
    """Скачивает YouTube через Piped API. Возвращает (path, title, duration, uploader)."""
    video_id = None
    if "youtu.be/" in url:
        video_id = url.split("youtu.be/")[1].split("?")[0].split("&")[0]
    elif "youtube.com/watch" in url:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        video_id = qs.get("v", [None])[0]
    elif "youtube.com/shorts/" in url:
        video_id = url.split("youtube.com/shorts/")[1].split("?")[0].split("&")[0]

    if not video_id:
        raise RuntimeError("Не удалось определить ID видео из ссылки")

    last_error = None
    data = None

    for instance in PIPED_INSTANCES:
        try:
            api_url = f"{instance}/streams/{video_id}"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", errors="ignore"))
            if data and data.get("title"):
                print(f"✅ Piped: использован инстанс {instance}")
                break
        except Exception as e:
            last_error = e
            print(f"⚠ Piped инстанс {instance} не ответил: {e}")
            continue

    if not data or not data.get("title"):
        raise RuntimeError(f"Все Piped инстансы недоступны. Последняя ошибка: {last_error}")

    title = clean_meta(data.get("title") or "youtube")
    duration = data.get("duration")
    uploader = data.get("uploader") or ""

    best = None
    if mode == "audio":
        streams = data.get("audioStreams", [])
        if not streams:
            raise RuntimeError("Piped: нет аудио-потоков")
        best = max(streams, key=lambda s: s.get("bitrate", 0) or 0)
        media_url = best.get("url")
        ext = ".m4a"
    else:
        streams = data.get("videoStreams", [])
        if not streams:
            raise RuntimeError("Piped: нет видео-потоков")
        with_audio = [s for s in streams if s.get("videoOnly") is False]
        if with_audio:
            best = max(with_audio, key=lambda s: s.get("height", 0) or 0)
        else:
            best = max(streams, key=lambda s: s.get("height", 0) or 0)
        media_url = best.get("url")
        ext = ".mp4"

    if not media_url:
        raise RuntimeError("Piped: не удалось получить прямую ссылку")

    out_path = f"downloads/piped_{video_id}{ext}"

    req = urllib.request.Request(media_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        with open(out_path, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)

    # Если видео получилось без звука (videoOnly) — склеиваем с аудио
    if mode == "video" and best.get("videoOnly"):
        try:
            audio_streams = data.get("audioStreams", [])
            if audio_streams:
                best_audio = max(audio_streams, key=lambda s: s.get("bitrate", 0) or 0)
                audio_url = best_audio.get("url")
                if audio_url:
                    audio_tmp = f"downloads/piped_{video_id}_audio.m4a"
                    req_a = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req_a, timeout=120) as ra:
                        with open(audio_tmp, "wb") as fa:
                            while True:
                                chunk = ra.read(65536)
                                if not chunk:
                                    break
                                fa.write(chunk)
                    merged = f"downloads/piped_{video_id}_merged.mp4"
                    cmd = [
                        FFMPEG_EXE_PATH, "-y",
                        "-i", out_path,
                        "-i", audio_tmp,
                        "-c:v", "copy", "-c:a", "aac",
                        "-shortest", merged,
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=120)
                    os.remove(out_path)
                    os.remove(audio_tmp)
                    if os.path.exists(merged):
                        os.rename(merged, out_path)
        except Exception as e:
            print(f"⚠ Не удалось склеить видео+аудио: {e}")

    # Если аудио — конвертируем в mp3
    if mode == "audio":
        mp3_path = f"downloads/piped_{video_id}.mp3"
        cmd = [
            FFMPEG_EXE_PATH, "-y", "-i", out_path,
            "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
            mp3_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        try:
            os.remove(out_path)
        except Exception:
            pass
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg: {r.stderr[-300:] if r.stderr else 'unknown'}")
        out_path = mp3_path

    return out_path, title, duration, uploader


# ============================================================
#       TIKTOK через публичный API tikwm.com
# ============================================================
def _download_file(url, out_path, timeout=120):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        with open(out_path, 'wb') as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
    return out_path


def tiktok_via_api(url, mode):
    api_url = f"https://tikwm.com/api/?url={urllib.parse.quote(url)}&hd=1"
    req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode('utf-8', errors='ignore'))

    if data.get('code') != 0:
        raise RuntimeError(f"TikTok API: {data.get('msg', 'unknown error')}")

    d = data['data']
    vid_id = d.get('id') or 'tiktok'
    title = clean_meta(d.get('title') or 'tiktok')
    author = (d.get('author') or {}).get('unique_id') or ''
    duration = d.get('duration')

    if author:
        title = clean_meta(d.get('title') or 'tiktok', strip_author=author)

    if mode == 'audio':
        media_url = d.get('music')
        if not media_url:
            raise RuntimeError("TikTok API: не вернул ссылку на аудио")
        tmp_path = f"downloads/{vid_id}_audio_raw"
        _download_file(media_url, tmp_path)
        out_path = f"downloads/{vid_id}.mp3"
        cmd = [
            FFMPEG_EXE_PATH, "-y", "-i", tmp_path,
            "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg: {r.stderr[-300:] if r.stderr else 'unknown'}")
        return out_path, title, duration, None
    else:
        media_url = d.get('hdplay') or d.get('play')
        if not media_url:
            raise RuntimeError("TikTok API: не вернул ссылку на видео")
        out_path = f"downloads/{vid_id}.mp4"
        _download_file(media_url, out_path)
        return out_path, title, duration, None


# ============================================================
#                     НАРЕЗКА ФОТО
# ============================================================
def split_image(image_path, total_parts):
    img = Image.open(image_path)
    width, height = img.size
    cols = 3
    rows = total_parts // cols
    part_width = width // cols
    part_height = height // rows
    saved_files = []

    for row in range(rows):
        for col in range(cols):
            left = col * part_width
            top = row * part_height
            right = (col + 1) * part_width if col < cols - 1 else width
            bottom = (row + 1) * part_height if row < rows - 1 else height
            cropped = img.crop((left, top, right, bottom))
            filename = f"temp_photos/part_{row}_{col}.jpg"
            cropped.save(filename, "JPEG", quality=95)
            saved_files.append(filename)
    return saved_files


def calculate_auto_parts(image_path):
    img = Image.open(image_path)
    width, height = img.size
    target_part_width = width / 3
    rows = max(1, round(height / target_part_width))
    return rows * 3


# ============================================================
#         АУДИО: тегирование
# ============================================================
def apply_tags(audio_path, cover_path, title, performer):
    out_path = os.path.splitext(audio_path)[0] + "_tagged.mp3"
    cmd = [FFMPEG_EXE_PATH, "-y", "-i", audio_path]

    if cover_path and os.path.exists(cover_path):
        cmd += ["-i", cover_path]
        cmd += [
            "-map", "0:a", "-map", "1:v",
            "-c:v", "mjpeg",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)",
        ]
    else:
        cmd += ["-map", "0:a"]

    cmd += [
        "-c:a", "libmp3lame", "-b:a", "192k",
        "-id3v2_version", "3",
        "-metadata", f"title={title}",
        "-metadata", f"artist={performer}",
        out_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-500:] if result.stderr else "ffmpeg failed")
    return out_path


def get_duration(path):
    try:
        cmd = [
            FFPROBE_EXE_PATH, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return None


# ============================================================
#                     ХЕНДЛЕРЫ
# ============================================================
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я умею три вещи:\n\n"
        "📷 *Отправь фото* — разрежу на части 3×N\n"
        "🔗 *Отправь ссылку* на TikTok/YouTube/FB/IG — скачаю видео или MP3\n"
        "🎵 *Отправь аудиофайл* — поставлю обложку, название и автора"
    )


@dp.message(F.text == "/cancel")
async def cmd_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    for key in ("audio_path", "cover_path", "photo_path"):
        p = data.get(key)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    await state.clear()
    await message.answer("✅ Отменено.")


@dp.message(F.audio)
async def process_audio_for_tag(message: Message, state: FSMContext):
    audio = message.audio
    old = await state.get_data()
    for key in ("audio_path", "cover_path"):
        p = old.get(key)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    file_info = await bot.get_file(audio.file_id)
    ext = os.path.splitext(audio.file_name or "audio.mp3")[1] or ".mp3"
    local_path = f"downloads/tag_input_{audio.file_id}{ext}"
    await bot.download_file(file_info.file_path, local_path)

    await state.update_data(audio_path=local_path)
    await state.set_state(BotStates.waiting_for_cover)
    await message.answer(
        "🖼 *Пришлите картинку звука* (обложку).\n\n"
        "Она будет вставлена в файл как превью — Telegram покажет её в плеере."
    )


@dp.message(BotStates.waiting_for_cover, F.photo)
async def process_cover_for_audio(message: Message, state: FSMContext):
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    cover_path = f"downloads/cover_{photo.file_id}.jpg"
    await bot.download_file(file_info.file_path, cover_path)

    await state.update_data(cover_path=cover_path)
    await state.set_state(BotStates.waiting_for_meta)
    await message.answer(
        "📝 Теперь пришлите *название* и *автора* одним сообщением, разделив символом `|`:\n\n"
        "`Название песни | Исполнитель`"
    )


@dp.message(BotStates.waiting_for_cover)
async def process_cover_wrong_type(message: Message):
    await message.answer("🖼 Нужна именно *картинка*, пришлите фото.")


@dp.message(BotStates.waiting_for_meta)
async def process_meta_for_audio(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if "|" not in text:
        await message.answer(
            "❌ Не вижу разделитель `|`. Пришлите в формате:\n\n"
            "`Название песни | Исполнитель`"
        )
        return

    title, performer = [p.strip() for p in text.split("|", 1)]
    if not title:
        await message.answer("❌ Название не может быть пустым.")
        return
    if not performer:
        performer = "Unknown"

    data = await state.get_data()
    audio_path = data.get("audio_path")
    cover_path = data.get("cover_path")

    if not audio_path or not os.path.exists(audio_path):
        await message.answer("❌ Ошибка: аудиофайл не найден. Пришлите аудио заново.")
        await state.clear()
        return

    status = await message.answer("⏳ Ставлю метки...")
    out_path = None
    try:
        loop = asyncio.get_event_loop()
        out_path = await loop.run_in_executor(None, apply_tags, audio_path, cover_path, title, performer)
        duration = await loop.run_in_executor(None, get_duration, out_path)

        kwargs = {
            "audio": FSInputFile(out_path, filename=f"{title}.mp3"),
            "title": title,
            "performer": performer,
            "caption": "✅ Метки установлены!",
        }
        if duration:
            kwargs["duration"] = int(duration)
        if cover_path and os.path.exists(cover_path):
            kwargs["thumbnail"] = FSInputFile(cover_path)

        await message.answer_audio(**kwargs)
        await status.delete()

    except Exception as e:
        err = str(e)[:300].replace("`", "'")
        try:
            await status.edit_text(f"❌ Ошибка при обработке:\n\n`{err}`")
        except Exception:
            await message.answer(f"❌ Ошибка при обработке:\n\n`{err}`")
    finally:
        for p in (audio_path, cover_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        await state.clear()


@dp.message(F.voice | F.video_note)
async def reject_voice(message: Message):
    await message.answer(
        "❌ Голосовые сообщения и видеокружки не поддерживаются.\n\n"
        "Отправьте *аудиофайл* (mp3, m4a, ogg и т.п.)."
    )


@dp.message(F.photo)
async def process_photo(message: Message, state: FSMContext):
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    local_path = f"temp_photos/{photo.file_id}.jpg"
    await bot.download_file(file_info.file_path, local_path)

    await state.update_data(photo_path=local_path)
    await state.set_state(BotStates.waiting_for_parts)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Готово (Авто)", callback_data="auto_split")
    await message.answer(
        "На сколько частей поделить фото?\n\n"
        "ℹ *Введите число цифрами, кратное 3 (3, 6, 9, 12...), или нажмите кнопку авто-расчета.*",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data == "auto_split", BotStates.waiting_for_parts)
async def process_auto_split_callback(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    photo_path = user_data.get("photo_path")
    if not photo_path or not os.path.exists(photo_path):
        await callback.answer("Ошибка: файл не найден.", show_alert=True)
        await state.clear()
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    loop = asyncio.get_event_loop()
    total_parts = await loop.run_in_executor(None, calculate_auto_parts, photo_path)
    await callback.message.answer(
        f"🤖 *Авто-расчет:* фото будет разделено на *{total_parts}* частей "
        f"(3 в ширину, {total_parts // 3} в длину)."
    )
    await execute_splitting(callback.message, state, photo_path, total_parts)
    await callback.answer()


@dp.message(BotStates.waiting_for_parts)
async def process_parts_count(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("Пожалуйста, отправьте число цифрами.")
        return
    total_parts = int(message.text)
    if total_parts < 3 or total_parts % 3 != 0:
        await message.answer("❌ Число должно без остатка делиться на 3.")
        return
    user_data = await state.get_data()
    photo_path = user_data.get("photo_path")
    await execute_splitting(message, state, photo_path, total_parts)


async def execute_splitting(message_obj: Message, state: FSMContext, photo_path: str, total_parts: int):
    status_msg = await message_obj.answer("✂ Нарезаю картинку тютелька в тютельку...")
    try:
        loop = asyncio.get_event_loop()
        parts = await loop.run_in_executor(None, split_image, photo_path, total_parts)
        for part_file in parts:
            if os.path.exists(part_file):
                await message_obj.answer_photo(FSInputFile(part_file))
                os.remove(part_file)
        await status_msg.delete()
        await message_obj.answer("✅ Все фрагменты отправлены!")
    except Exception as e:
        await message_obj.answer(f"Ошибка при обработке фото: {e}")
    finally:
        if os.path.exists(photo_path):
            os.remove(photo_path)
        await state.clear()


@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def ask_media_type(message: Message, state: FSMContext):
    url = message.text.strip()
    await state.update_data(download_url=url)
    await state.set_state(BotStates.waiting_for_media_type)

    builder = InlineKeyboardBuilder()
    builder.button(text="🎵 Аудио (MP3)", callback_data="get_audio")
    builder.button(text="🎬 Видео (MP4)", callback_data="get_video")
    await message.answer("Что вы хотите скачать по этой ссылке?", reply_markup=builder.as_markup())


@dp.callback_query(F.data.in_({"get_audio", "get_video"}), BotStates.waiting_for_media_type)
async def process_download(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    url = user_data.get("download_url")
    mode = callback.data

    await callback.message.edit_reply_markup(reply_markup=None)
    status_msg = await callback.message.answer("⏳ Подключаюсь к источнику...")
    await callback.answer()

    loop = asyncio.get_event_loop()
    is_tiktok = "tiktok.com" in url or "vm.tiktok.com" in url or "vt.tiktok.com" in url
    is_youtube = "youtube.com" in url or "youtu.be" in url

    final_filename = None
    thumb_path = None

    try:
        # ============================================================
        #       TIKTOK → через tikwm.com
        # ============================================================
        if is_tiktok:
            try:
                await status_msg.edit_text("📥 Скачиваю через TikTok API...")
            except Exception:
                pass

            final_filename, title, duration, _ = await loop.run_in_executor(
                None, tiktok_via_api, url, mode
            )

            if not os.path.exists(final_filename):
                raise FileNotFoundError(f"API вернул путь, но файл не найден: {final_filename}")

            if mode == "get_audio":
                try:
                    await status_msg.edit_text("📤 Отправляю в Telegram...")
                except Exception:
                    pass
                kwargs = {
                    'audio': FSInputFile(final_filename, filename=f"{title}.mp3"),
                    'title': title,
                    'caption': "🎵 Звуковая дорожка готова!",
                }
                if duration:
                    kwargs['duration'] = int(duration)
                await callback.message.answer_audio(**kwargs)
            else:
                try:
                    await status_msg.edit_text("📤 Отправляю в Telegram...")
                except Exception:
                    pass
                await callback.message.answer_video(
                    video=FSInputFile(final_filename, filename=f"{title}.mp4"),
                    caption="🎬 Видео успешно скачано!",
                    duration=int(duration) if duration else None,
                )

            await status_msg.delete()
            return

        # ============================================================
        #       YOUTUBE → через Piped API
        # ============================================================
        if is_youtube:
            try:
                await status_msg.edit_text("📥 Скачиваю через YouTube API...")
            except Exception:
                pass

            final_filename, title, duration, uploader = await loop.run_in_executor(
                None, youtube_via_piped, url, mode
            )

            if not os.path.exists(final_filename):
                raise FileNotFoundError(f"API вернул путь, но файл не найден: {final_filename}")

            if mode == "get_audio":
                try:
                    await status_msg.edit_text("📤 Отправляю в Telegram...")
                except Exception:
                    pass
                kwargs = {
                    'audio': FSInputFile(final_filename, filename=f"{title}.mp3"),
                    'title': title,
                    'caption': "🎵 Звуковая дорожка готова!",
                }
                if duration:
                    kwargs['duration'] = int(duration)
                if uploader:
                    kwargs['performer'] = clean_meta(uploader, max_len=64)
                await callback.message.answer_audio(**kwargs)
            else:
                try:
                    await status_msg.edit_text("📤 Отправляю в Telegram...")
                except Exception:
                    pass
                await callback.message.answer_video(
                    video=FSInputFile(final_filename, filename=f"{title}.mp4"),
                    caption="🎬 Видео успешно скачано!",
                    duration=int(duration) if duration else None,
                )

            await status_msg.delete()
            return

        # ============================================================
        #       ВСЁ ОСТАЛЬНОЕ (Instagram/FB/...) → через yt-dlp
        # ============================================================
        ydl_opts = {
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'noprogress': True,
            'noplaylist': True,
            'ffmpeg_location': FFMPEG_DIR,
        }

        if mode == "get_audio":
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            ydl_opts.update({
                'format': 'best[filesize<45M]/bestvideo[filesize<45M]+bestaudio/best',
                'merge_output_format': 'mp4',
            })

        last_edit = {"t": 0.0}

        def _fmt_size(b):
            if not b:
                return "?"
            for unit in ("B", "KB", "MB", "GB"):
                if b < 1024:
                    return f"{b:.1f}{unit}"
                b /= 1024
            return f"{b:.1f}TB"

        def _progress_hook(d):
            if d.get("status") != "downloading":
                return
            now = loop.time()
            if now - last_edit["t"] < 1.2:
                return
            last_edit["t"] = now
            pct = d.get("_percent_str", "?").strip()
            speed = d.get("_speed_str", "?").strip()
            eta = d.get("_eta_str", "?").strip()
            total = _fmt_size(d.get("total_bytes") or d.get("total_bytes_estimate"))
            done = _fmt_size(d.get("downloaded_bytes"))
            text = (
                f"📥 *Скачивание...*\n"
                f"`{pct}` от `{total}`\n"
                f"📦 Скачано: `{done}`\n"
                f"⚡ Скорость: `{speed}`\n"
                f"⏱ Осталось: `{eta}`"
            )
            asyncio.run_coroutine_threadsafe(_safe_edit(status_msg, text), loop)

        ydl_opts['progress_hooks'] = [_progress_hook]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            filename = ydl.prepare_filename(info)

        base_path, _ = os.path.splitext(filename)
        if mode == "get_audio":
            candidates = [base_path + ".mp3", filename + ".mp3"]
        else:
            candidates = [filename, base_path + ".mp4", base_path + ".mkv", base_path + ".webm"]

        final_filename = next((p for p in candidates if os.path.exists(p)), None)
        if not final_filename:
            raise FileNotFoundError(f"Файл не найден. Ожидался один из: {candidates}")

        duration = info.get('duration')

        def _clean_yt(s, max_len=100):
            if not s:
                return ""
            s = str(s).strip()
            s = re.sub(r"https?://\S+", " ", s)
            s = EMOJI_RE.sub(" ", s)
            s = " ".join(s.split())
            return s[:max_len].strip()

        title = _clean_yt(info.get('title')) or "media"
        performer = _clean_yt(
            info.get('artist') or info.get('uploader')
            or info.get('channel') or info.get('creator') or '', max_len=64
        ) or "Unknown"

        if mode == "get_audio":
            thumb_url = info.get('thumbnail')
            if thumb_url:
                try:
                    thumb_path = f"downloads/{info['id']}_thumb.jpg"
                    req = urllib.request.Request(thumb_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        data = r.read()
                    tmp_raw = f"downloads/{info['id']}_thumb_raw"
                    with open(tmp_raw, 'wb') as f:
                        f.write(data)
                    img = Image.open(tmp_raw).convert("RGB")
                    img.save(thumb_path, "JPEG", quality=90)
                    os.remove(tmp_raw)
                except Exception as te:
                    print(f"⚠ Не удалось получить обложку: {te}")
                    thumb_path = None

            try:
                await status_msg.edit_text("📤 Отправляю в Telegram...")
            except Exception:
                pass

            kwargs = {
                'audio': FSInputFile(final_filename, filename=f"{title}.mp3"),
                'title': title,
                'performer': performer,
                'caption': "🎵 Звуковая дорожка готова!",
            }
            if duration:
                kwargs['duration'] = int(duration)
            if thumb_path and os.path.exists(thumb_path):
                kwargs['thumbnail'] = FSInputFile(thumb_path)
            await callback.message.answer_audio(**kwargs)
        else:
            try:
                await status_msg.edit_text("📤 Отправляю в Telegram...")
            except Exception:
                pass
            await callback.message.answer_video(
                video=FSInputFile(final_filename, filename=f"{title}.mp4"),
                caption="🎬 Видео успешно скачано!",
                duration=int(duration) if duration else None,
            )

        await status_msg.delete()

    except Exception as e:
        print(f"Ошибка в процессе: {e}")
        err_text = str(e)[:300].replace('`', "'")
        try:
            await status_msg.edit_text(f"❌ Не удалось скачать медиа.\n\n`{err_text}`")
        except Exception:
            await callback.message.answer(f"❌ Не удалось скачать медиа.\n\n`{err_text}`")
    finally:
        for path in (final_filename, thumb_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        await state.clear()


async def _safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass


# ============================================================
#                     ЗАПУСК
# ============================================================
async def main():
    print("🚀 Бот успешно подключен к Telegram и слушает команды...")
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот остановлен.")
