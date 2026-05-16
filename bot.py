import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from config import BOT_TOKEN, MAX_FILE_SIZE_BYTES, PROXY_URL
from downloader import (
    DownloadError,
    FileTooLargeError,
    VideoUnavailableError,
    download_video,
)
from validators import extract_tiktok_url, is_tiktok_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, **{"proxy": PROXY_URL} if PROXY_URL else {})
dp = Dispatcher()

# Track users with an active download to enforce one-request-at-a-time
_active_users: set[int] = set()

START_TEXT = (
    "Привет! 👋\n\n"
    "Я умею скачивать видео из TikTok и отправлять их тебе.\n\n"
    "Просто отправь мне ссылку на видео — и готово.\n\n"
    "Поддерживаемые форматы ссылок:\n"
    "• https://www.tiktok.com/@user/video/...\n"
    "• https://vm.tiktok.com/...\n"
    "• https://vt.tiktok.com/...\n\n"
    "/help — справка"
)

HELP_TEXT = (
    "Что я умею:\n"
    "• Скачивать публичные TikTok-видео\n"
    "• Отправлять видео прямо в чат\n\n"
    "Ограничения:\n"
    f"• Максимальный размер: {MAX_FILE_SIZE_BYTES // (1024*1024)} МБ\n"
    "• Только публичные видео\n"
    "• Один запрос за раз\n"
    "• Видео не сохраняются на сервере\n\n"
    "Просто отправь ссылку на TikTok-видео."
)


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@dp.message(F.text)
async def handle_message(message: Message) -> None:
    user_id = message.from_user.id
    text = message.text or ""

    if not is_tiktok_url(text):
        await message.answer(
            "Это не похоже на ссылку TikTok.\n"
            "Отправь ссылку вида https://www.tiktok.com/@user/video/...\n"
            "или /help для справки."
        )
        return

    if user_id in _active_users:
        await message.answer("Подожди, я ещё обрабатываю твой предыдущий запрос.")
        return

    url = extract_tiktok_url(text)
    _active_users.add(user_id)
    status_msg = await message.answer("Скачиваю видео...")

    file_path: str | None = None
    try:
        result = await download_video(url)
        file_path = result.file_path

        await status_msg.edit_text("Отправляю видео...")
        video_file = FSInputFile(file_path)
        await message.answer_video(video_file)

    except FileTooLargeError as exc:
        await status_msg.edit_text(f"Видео слишком большое: {exc}")
    except VideoUnavailableError as exc:
        await status_msg.edit_text(f"Видео недоступно: {exc}")
    except DownloadError as exc:
        logger.error("DownloadError for user %s, url %s: %s", user_id, url, exc)
        await status_msg.edit_text(f"Ошибка скачивания: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error for user %s, url %s", user_id, url)
        await status_msg.edit_text("Произошла неожиданная ошибка. Попробуй позже.")
    finally:
        _active_users.discard(user_id)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as exc:
                logger.warning("Could not remove temp file %s: %s", file_path, exc)


async def main() -> None:
    logger.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
