import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InputMediaPhoto, Message

from config import BOT_TOKEN, MAX_FILE_SIZE_BYTES, PROXY_URL
from downloader import (
    ContentResult,
    DownloadError,
    FileTooLargeError,
    PhotoResult,
    VideoResult,
    VideoUnavailableError,
    cleanup_result,
    download_content,
)
from validators import extract_tiktok_url, is_tiktok_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, **{"proxy": PROXY_URL} if PROXY_URL else {})
dp = Dispatcher()

_active_users: set[int] = set()

START_TEXT = (
    "Привет! 👋\n\n"
    "Я умею скачивать контент из TikTok:\n"
    "• Видео — пришлю файл\n"
    "• Фото-карусель — пришлю все фото + фоновую музыку\n\n"
    "Просто отправь ссылку.\n\n"
    "Поддерживаемые форматы ссылок:\n"
    "• https://www.tiktok.com/@user/video/...\n"
    "• https://www.tiktok.com/@user/photo/...\n"
    "• https://vm.tiktok.com/...\n"
    "• https://vt.tiktok.com/...\n\n"
    "/help — справка"
)

HELP_TEXT = (
    "Что я умею:\n"
    "• Скачивать публичные TikTok-видео\n"
    "• Скачивать фото-карусели (все фото по порядку + музыка)\n\n"
    "Ограничения:\n"
    f"• Максимальный размер видео: {MAX_FILE_SIZE_BYTES // (1024 * 1024)} МБ\n"
    "• Только публичные посты\n"
    "• Один запрос за раз\n"
    "• Файлы не сохраняются на сервере\n\n"
    "Просто отправь ссылку на TikTok-пост."
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
    status_msg = await message.answer("Скачиваю...")

    result: ContentResult | None = None
    try:
        result = await download_content(url)

        if isinstance(result, VideoResult):
            await status_msg.edit_text("Отправляю видео...")
            await message.answer_video(FSInputFile(result.file_path))

        elif isinstance(result, PhotoResult):
            await status_msg.edit_text(
                f"Отправляю {len(result.image_paths)} фото"
                + (" и музыку..." if result.audio_path else "...")
            )
            # Telegram allows max 10 items per media group
            media = [InputMediaPhoto(media=FSInputFile(p)) for p in result.image_paths]
            for i in range(0, len(media), 10):
                await message.answer_media_group(media[i : i + 10])

            if result.audio_path:
                await message.answer_audio(
                    FSInputFile(result.audio_path),
                    title="Фоновая музыка",
                )

        await status_msg.delete()

    except FileTooLargeError as exc:
        await status_msg.edit_text(f"Видео слишком большое: {exc}")
    except VideoUnavailableError as exc:
        await status_msg.edit_text(f"Пост недоступен: {exc}")
    except DownloadError as exc:
        logger.error("DownloadError for user %s url %s: %s", user_id, url, exc)
        await status_msg.edit_text(f"Ошибка скачивания: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error for user %s url %s", user_id, url)
        await status_msg.edit_text("Произошла неожиданная ошибка. Попробуй позже.")
    finally:
        _active_users.discard(user_id)
        if result is not None:
            cleanup_result(result)


async def main() -> None:
    logger.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
