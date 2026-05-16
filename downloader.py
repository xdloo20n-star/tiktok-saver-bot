import asyncio
import logging
import os
import re
import shutil
import urllib.request
import uuid
from dataclasses import dataclass, field

import yt_dlp

from config import DOWNLOADS_DIR, DOWNLOAD_TIMEOUT_SEC, MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)

# TikTok CDN requires browser-like headers; plain requests get 403
_CDN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://www.tiktok.com/",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}


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


# ── URL normalisation ─────────────────────────────────────────────────────────

def _photo_to_video_url(url: str) -> str:
    """
    yt-dlp's TikTok extractor only matches /video/ paths.
    TikTok uses the same numeric ID for photos and videos, so /photo/<id>
    can always be rewritten to /video/<id> — content type is determined
    by the API response (presence of 'images' field), not the URL path.
    Strip tracking query params to keep the URL clean.
    """
    url = url.split("?")[0]
    return re.sub(r"/photo/(\d+)", r"/video/\1", url)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_image_url(img: object) -> str | None:
    """Pull a usable URL out of whatever structure yt-dlp returns for one image."""
    if isinstance(img, str):
        return img
    if isinstance(img, dict):
        # direct 'url' field (most common)
        if url := img.get("url"):
            return url if isinstance(url, str) else None
        # fallback list
        for key in ("url_list", "urls", "download_url_list"):
            lst = img.get(key)
            if isinstance(lst, list) and lst:
                return lst[0]
    if isinstance(img, list) and img:
        # list of dicts (quality variants) → pick last (best)
        return _extract_image_url(img[-1])
    return None


def _fetch_url_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_CDN_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


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
        if os.path.exists(p := f"{output_path}.{ext}"):
            return p
    if os.path.exists(output_path):
        return output_path
    raise DownloadError("Файл после скачивания не найден")


# ── Slideshow ─────────────────────────────────────────────────────────────────

def _sync_download_images(images: list, output_dir: str) -> list[str]:
    paths = []
    for i, img in enumerate(images):
        img_url = _extract_image_url(img)
        if not img_url:
            logger.debug("Image %d: could not extract URL from %r", i, img)
            continue
        try:
            data = _fetch_url_bytes(img_url)
        except Exception as exc:
            logger.warning("Image %d download failed (%s): %s", i, img_url[:80], exc)
            continue
        img_path = os.path.join(output_dir, f"photo_{i:03d}.jpg")
        with open(img_path, "wb") as f:
            f.write(data)
        paths.append(img_path)
        logger.debug("Downloaded image %d → %s (%d bytes)", i, img_path, len(data))
    return paths


def _sync_download_audio(url: str, output_dir: str) -> str | None:
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
            if os.path.exists(p := f"{audio_out}.{ext}"):
                return p
    except Exception as exc:
        logger.warning("Audio download failed: %s", exc)
    return None


# ── Router ────────────────────────────────────────────────────────────────────

def _extract_info(ydl_url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        return ydl.extract_info(ydl_url, download=False)


def _sync_fetch(url: str, output_dir: str) -> ContentResult:
    # For direct /photo/ URLs, rewrite immediately.
    # For short URLs (vm/vt.tiktok.com), yt-dlp follows the redirect itself;
    # if it lands on a /photo/ URL it raises "Unsupported URL" — we catch
    # that, extract the resolved URL from the error message, and retry.
    ydl_url = _photo_to_video_url(url)

    try:
        info = _extract_info(ydl_url)
    except yt_dlp.utils.DownloadError as exc:
        exc_str = str(exc)
        if "Unsupported URL:" in exc_str and "/photo/" in exc_str:
            resolved = exc_str.split("Unsupported URL:", 1)[1].strip()
            ydl_url = _photo_to_video_url(resolved)
            logger.info("Photo post detected via redirect, retrying as: %s", ydl_url)
            info = _extract_info(ydl_url)
        else:
            raise

    if not info:
        raise DownloadError("Не удалось получить информацию о контенте")

    logger.debug("info keys: %s | _type: %s", list(info.keys()), info.get("_type"))

    images = info.get("images")
    logger.debug("images field: %s item(s)", len(images) if images else 0)

    if images:
        image_paths = _sync_download_images(images, output_dir)
        if not image_paths:
            raise DownloadError("Не удалось скачать ни одной фотографии из поста")
        audio_path = _sync_download_audio(ydl_url, output_dir)
        return PhotoResult(image_paths=image_paths, audio_path=audio_path, dir=output_dir)

    file_path = _sync_download_video(ydl_url, output_dir)
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
