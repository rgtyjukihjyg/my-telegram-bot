import os
import re
import asyncio
import urllib.request
import subprocess
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from PIL import Image
import yt_dlp
from static_ffmpeg import run as static_ffmpeg_run

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ Не задана переменная окружения BOT_TOKEN")

# --- Настройки для вебхука ---
WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = "/webhook"

# URL, который будет сгенерирован автоматически на Fly.io
# Если тестируешь локально, можешь временно указать свой ngrok-адрес
BASE_WEBHOOK_URL = os.environ.get("WEBHOOK_URL", f"https://{os.environ.get('FLY_APP_NAME', 'localhost')}.fly.dev")

# Таймаут бездействия в секундах (5 минут)
INACTIVITY_TIMEOUT = 300

# --- Инициализация ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()

# Глобальные переменные для отслеживания активности
last_activity = asyncio.get_event_loop().time()
shutdown_task = None

class BotStates(StatesGroup):
    waiting_for_parts = State()
    waiting_for_media_type = State()
    waiting_for_cover = State()
    waiting_for_meta = State()

os.makedirs("downloads", exist_ok=True)
os.makedirs("temp_photos", exist_ok=True)

# --- FFMPEG через static-ffmpeg ---
print("🔧 Проверяю ffmpeg / ffprobe...")
FFMPEG_EXE_PATH, FFPROBE_EXE_PATH = static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
FFMPEG_DIR = os.path.dirname(FFMPEG_EXE_PATH)
print(f"✅ ffmpeg:  {FFMPEG_EXE_PATH}")
print(f"✅ ffprobe: {FFPROBE_EXE_PATH}")


# ============================================================
#         УТИЛИТЫ ДЛЯ РАБОТЫ С АВТО-ВЫКЛЮЧЕНИЕМ
# ============================================================
def update_activity():
    """Обновляет время последней активности."""
    global last_activity
    last_activity = asyncio.get_event_loop().time()
    print(f"🕒 Активность обновлена. Выключение через {INACTIVITY_TIMEOUT} сек. простоя.")


async def shutdown_if_idle():
    """Проверяет бездействие и выключает приложение."""
    global shutdown_task
    await asyncio.sleep(INACTIVITY_TIMEOUT)
    
    current_time = asyncio.get_event_loop().time()
    idle_time = current_time - last_activity
    
    if idle_time >= INACTIVITY_TIMEOUT:
        print(f"💤 Простой {idle_time:.0f} сек. Выключаюсь...")
        # Отправляем сигнал на завершение работы aiohttp сервера
        # Это корректно завершит процесс на Fly.io
        os.kill(os.getpid(), 15)  # SIGTERM
    else:
        # Если активность была, перезапускаем таймер
        remaining = INACTIVITY_TIMEOUT - idle_time
        print(f"🔄 Таймер сброшен. Осталось {remaining:.0f} сек.")
        shutdown_task = asyncio.create_task(shutdown_if_idle())


def start_shutdown_timer():
    """Запускает или перезапускает таймер выключения."""
    global shutdown_task
    if shutdown_task and not shutdown_task.done():
        shutdown_task.cancel()
    shutdown_task = asyncio.create_task(shutdown_if_idle())


# ============================================================
#                     ХЕНДЛЕРЫ (с обновлением активности)
# ============================================================
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    update_activity()
    start_shutdown_timer()
    await message.answer(
        "👋 Привет! Я умею три вещи:\n\n"
        "📷 *Отправь фото* — разрежу на части 3×N\n"
        "🔗 *Отправь ссылку* на TikTok/YouTube/FB/IG — скачаю видео или MP3\n"
        "🎵 *Отправь аудиофайл* — поставлю обложку, название и автора"
    )


# ... (остальные хендлеры: process_audio_for_tag, process_cover_for_audio, и т.д.)
# ВАЖНО: В каждом хендлере, который получает сообщение от пользователя,
# нужно добавить в начале две строки:
#     update_activity()
#     start_shutdown_timer()
# Я добавлю их в самые важные хендлеры для примера, но ты должен добавить их во все.

@dp.message(F.audio)
async def process_audio_for_tag(message: Message, state: FSMContext):
    update_activity()
    start_shutdown_timer()
    # ... (дальнейший код без изменений)
    audio = message.audio
    # ... и так далее


@dp.message(F.photo)
async def process_photo(message: Message, state: FSMContext):
    update_activity()
    start_shutdown_timer()
    # ... (дальнейший код без изменений)
    photo = message.photo[-1]
    # ... и так далее


@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def ask_media_type(message: Message, state: FSMContext):
    update_activity()
    start_shutdown_timer()
    # ... (дальнейший код без изменений)
    url = message.text.strip()
    # ... и так далее


@dp.callback_query(F.data.in_({"get_audio", "get_video"}), BotStates.waiting_for_media_type)
async def process_download(callback: CallbackQuery, state: FSMContext):
    update_activity()
    start_shutdown_timer()
    # ... (дальнейший код без изменений)
    user_data = await state.get_data()
    # ... и так далее


# ... (и так для всех остальных хендлеров)


# ============================================================
#                     ЗАПУСК WEB-СЕРВЕРА
# ============================================================
async def on_startup(bot: Bot):
    """Устанавливаем вебхук при старте."""
    await bot.set_webhook(f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}", drop_pending_updates=True)
    print(f"✅ Вебхук установлен на {BASE_WEBHOOK_URL}{WEBHOOK_PATH}")
    # Запускаем таймер бездействия при старте
    update_activity()
    start_shutdown_timer()


async def on_shutdown(bot: Bot):
    """Удаляем вебхук при выключении."""
    await bot.delete_webhook()
    print("👋 Вебхук удалён. Бот выключен.")


def main():
    """Точка входа."""
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    # Создаём обработчик вебхуков
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=os.environ.get("WEBHOOK_SECRET", "my-secret")
    )

    # Регистрируем маршрут для вебхука
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    # Настраиваем aiohttp приложение
    setup_application(app, dp, bot=bot)

    # Запускаем сервер
    print(f"🚀 Запуск сервера на {WEBHOOK_HOST}:{WEBHOOK_PORT}")
    web.run_app(app, host=WEBHOOK_HOST, port=WEBHOOK_PORT)


if __name__ == '__main__':
    main()