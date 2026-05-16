import asyncio
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field

import yt_dlp

from config import DOWNLOADS_DIR, DOWNLOAD_TIMEOUT_SEC, MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)


@dataclass
class VideoResult:
    file_path: str
    dir: str


@dataclass
class PhotoResult:
    image_paths: list[str] = field(default_factory=list)
    audio_path: str | None = None
    dir: str = ""


ContentResult = VideoResult | PhotoResult


class DownloadError(Exception):
    pass


class FileTooLargeError(DownloadError):
    pass


class VideoUnavailableError(DownloadError):
    pass


# ── Video ─────────────────────────────────────────────────────────────────────

def _sync_download_video(url: str, output_dir: str) -> str:
    output_path = os.path.join(output_dir, "video")
    opts = {
        "outtmpl": output_path,
        "format": "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    for ext in ("mp4", "mkv", "webm", "mov"):
        candidate = f"{output_path}.{ext}"
        if os.path.exists(candidate):
            return candidate

    if os.path.exists(output_path):
        return output_path

    raise DownloadError("Файл после скачивания не найден")


# ── Slideshow ─────────────────────────────────────────────────────────────────

def _sync_download_images(images: list, output_dir: str) -> list[str]:
    """Downloads each image from the slideshow using yt-dlp's own URL opener."""
    paths = []
    info_opts = {"quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        for i, img in enumerate(images):
            img_url = img.get("url") if isinstance(img, dict) else None
            if not img_url:
                continue
            try:
                data = ydl.urlopen(img_url).read()
            except Exception as exc:
                logger.warning("Could not download image %d: %s", i, exc)
                continue
            img_path = os.path.join(output_dir, f"photo_{i:03d}.jpg")
            with open(img_path, "wb") as f:
                f.write(data)
            paths.append(img_path)
    return paths


def _sync_download_audio(url: str, output_dir: str) -> str | None:
    """Extracts background audio to mp3. Returns path or None if unavailable."""
    audio_out = os.path.join(output_dir, "audio")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": audio_out,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        for ext in ("mp3", "m4a", "aac", "ogg"):
            candidate = f"{audio_out}.{ext}"
            if os.path.exists(candidate):
                return candidate
    except Exception as exc:
        logger.warning("Could not download audio for slideshow: %s", exc)
    return None


# ── Router ────────────────────────────────────────────────────────────────────

def _sync_fetch(url: str, output_dir: str) -> ContentResult:
    # One metadata fetch to determine content type
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise DownloadError("Не удалось получить информацию о контенте")

    images = info.get("images")

    if images:
        image_paths = _sync_download_images(images, output_dir)
        if not image_paths:
            raise DownloadError("Не удалось скачать фотографии из поста")
        audio_path = _sync_download_audio(url, output_dir)
        return PhotoResult(image_paths=image_paths, audio_path=audio_path, dir=output_dir)

    file_path = _sync_download_video(url, output_dir)
    size = os.path.getsize(file_path)
    if size > MAX_FILE_SIZE_BYTES:
        max_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
        raise FileTooLargeError(f"Видео слишком большое (>{max_mb} МБ)")
    return VideoResult(file_path=file_path, dir=output_dir)


# ── Public API ────────────────────────────────────────────────────────────────

async def download_content(url: str) -> ContentResult:
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    output_dir = os.path.join(DOWNLOADS_DIR, uuid.uuid4().hex)
    os.makedirs(output_dir)

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _sync_fetch, url, output_dir),
            timeout=DOWNLOAD_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise DownloadError(f"Превышено время скачивания ({DOWNLOAD_TIMEOUT_SEC} сек)")
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        msg = str(exc).lower()
        if "private" in msg or "unavailable" in msg or "does not exist" in msg:
            raise VideoUnavailableError("Видео недоступно или является приватным") from exc
        raise DownloadError(f"Ошибка скачивания: {exc}") from exc
    except (FileTooLargeError, VideoUnavailableError, DownloadError):
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        logger.exception("Unexpected download error for %s", url)
        raise DownloadError("Неизвестная ошибка при скачивании") from exc


def cleanup_result(result: ContentResult) -> None:
    shutil.rmtree(result.dir, ignore_errors=True)
