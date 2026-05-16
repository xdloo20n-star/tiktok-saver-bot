import asyncio
import logging
import os
import uuid
from dataclasses import dataclass

import yt_dlp

from config import DOWNLOADS_DIR, DOWNLOAD_TIMEOUT_SEC, MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    file_path: str
    title: str


class DownloadError(Exception):
    pass


class FileTooLargeError(DownloadError):
    pass


class VideoUnavailableError(DownloadError):
    pass


def _build_ydl_opts(output_path: str) -> dict:
    return {
        "outtmpl": output_path,
        "format": "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }


def _sync_download(url: str, output_path: str) -> str:
    opts = _build_ydl_opts(output_path)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "video") if info else "video"

    # yt-dlp may append extension; find the actual file
    for ext in ("mp4", "mkv", "webm", "mov"):
        candidate = f"{output_path}.{ext}"
        if os.path.exists(candidate):
            return candidate

    if os.path.exists(output_path):
        return output_path

    raise DownloadError("Файл после скачивания не найден")


async def download_video(url: str) -> DownloadResult:
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    uid = uuid.uuid4().hex
    output_path = os.path.join(DOWNLOADS_DIR, uid)

    loop = asyncio.get_running_loop()
    try:
        file_path = await asyncio.wait_for(
            loop.run_in_executor(None, _sync_download, url, output_path),
            timeout=DOWNLOAD_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        _cleanup(output_path)
        raise DownloadError(f"Превышено время скачивания ({DOWNLOAD_TIMEOUT_SEC} сек)")
    except yt_dlp.utils.DownloadError as exc:
        _cleanup(output_path)
        msg = str(exc).lower()
        if "private" in msg or "unavailable" in msg or "does not exist" in msg:
            raise VideoUnavailableError("Видео недоступно или является приватным") from exc
        raise DownloadError(f"Ошибка скачивания: {exc}") from exc
    except Exception as exc:
        _cleanup(output_path)
        logger.exception("Unexpected download error for %s", url)
        raise DownloadError(f"Неизвестная ошибка при скачивании") from exc

    size = os.path.getsize(file_path)
    if size > MAX_FILE_SIZE_BYTES:
        _cleanup(file_path)
        max_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
        raise FileTooLargeError(f"Видео слишком большое (>{max_mb} МБ)")

    # Extract title from a best-effort re-read of info (already downloaded)
    title = "tiktok_video"
    return DownloadResult(file_path=file_path, title=title)


def _cleanup(path: str) -> None:
    for candidate in (path, f"{path}.mp4", f"{path}.mkv", f"{path}.webm"):
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
        except OSError:
            pass
