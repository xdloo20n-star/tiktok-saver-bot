import asyncio
import json
import logging
import os
import re
import shutil
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field

import yt_dlp

from config import DOWNLOADS_DIR, DOWNLOAD_TIMEOUT_SEC, MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)

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


# ── URL helpers ───────────────────────────────────────────────────────────────

def _clean_url(url: str) -> str:
    """Strip tracking query params."""
    return url.split("?")[0]


def _photo_to_video_url(url: str) -> str:
    """Rewrite /photo/<id> → /video/<id> so yt-dlp's TikTok extractor accepts it."""
    return re.sub(r"/photo/(\d+)", r"/video/\1", _clean_url(url))


def _extract_photo_url_from_error(exc: Exception) -> str | None:
    """If yt-dlp error message contains a /photo/ URL, return it (cleaned)."""
    match = re.search(r"https?://\S+/photo/\d+", str(exc))
    return _clean_url(match.group(0)) if match else None


# ── Low-level fetch ───────────────────────────────────────────────────────────

def _fetch_url_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_CDN_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


# ── yt-dlp paths ─────────────────────────────────────────────────────────────

def _extract_info(ydl_url: str) -> dict | None:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        return ydl.extract_info(ydl_url, download=False)


def _extract_image_url(img: object) -> str | None:
    if isinstance(img, str):
        return img
    if isinstance(img, dict):
        if url := img.get("url"):
            return url if isinstance(url, str) else None
        for key in ("url_list", "urls", "download_url_list"):
            lst = img.get(key)
            if isinstance(lst, list) and lst:
                return lst[0]
    if isinstance(img, list) and img:
        return _extract_image_url(img[-1])
    return None


def _sync_download_images_from_info(images: list, output_dir: str) -> list[str]:
    paths = []
    for i, img in enumerate(images):
        img_url = _extract_image_url(img)
        if not img_url:
            continue
        try:
            data = _fetch_url_bytes(img_url)
        except Exception as exc:
            logger.warning("yt-dlp image %d failed: %s", i, exc)
            continue
        path = os.path.join(output_dir, f"photo_{i:03d}.jpg")
        with open(path, "wb") as f:
            f.write(data)
        paths.append(path)
    return paths


def _sync_download_audio_ytdlp(url: str, output_dir: str) -> str | None:
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
        logger.warning("yt-dlp audio failed: %s", exc)
    return None


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


# ── tikwm.com fallback for photo posts ───────────────────────────────────────
# yt-dlp doesn't recognise /photo/ URLs; tikwm.com is a purpose-built
# TikTok API that returns individual images + audio URL directly.

def _sync_fetch_via_tikwm(photo_url: str, output_dir: str) -> PhotoResult:
    logger.info("Using tikwm.com for photo post: %s", photo_url)

    data = urllib.parse.urlencode({"url": photo_url, "hd": "1"}).encode()
    req = urllib.request.Request(
        "https://www.tikwm.com/api/",
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    if result.get("code") != 0:
        raise DownloadError(
            f"tikwm.com вернул ошибку: {result.get('msg', 'неизвестно')}"
        )

    post = result.get("data") or {}
    image_urls: list[str] = post.get("images") or []

    if not image_urls:
        raise DownloadError("tikwm.com: пост не содержит фотографий")

    # Download images in order
    image_paths = []
    for i, img_url in enumerate(image_urls):
        try:
            raw = _fetch_url_bytes(img_url)
        except Exception as exc:
            logger.warning("tikwm image %d failed: %s", i, exc)
            continue
        path = os.path.join(output_dir, f"photo_{i:03d}.jpg")
        with open(path, "wb") as f:
            f.write(raw)
        image_paths.append(path)

    if not image_paths:
        raise DownloadError("Не удалось скачать ни одной фотографии")

    # Download background audio (direct MP3 link from tikwm)
    audio_path = None
    music_url: str | None = post.get("music")
    if music_url:
        try:
            raw = _fetch_url_bytes(music_url)
            audio_path = os.path.join(output_dir, "audio.mp3")
            with open(audio_path, "wb") as f:
                f.write(raw)
        except Exception as exc:
            logger.warning("tikwm audio failed: %s", exc)

    return PhotoResult(image_paths=image_paths, audio_path=audio_path, dir=output_dir)


# ── Router ────────────────────────────────────────────────────────────────────

def _sync_fetch(url: str, output_dir: str) -> ContentResult:
    photo_url: str | None = _clean_url(url) if "/photo/" in url else None
    ydl_url = _photo_to_video_url(url)

    info: dict | None = None
    try:
        info = _extract_info(ydl_url)
    except Exception as exc:
        # yt-dlp may follow a short-URL redirect to a /photo/ URL it doesn't
        # support (UnsupportedError ≠ DownloadError). Parse the URL from the
        # error message and retry with /video/ to get metadata.
        resolved = _extract_photo_url_from_error(exc)
        if resolved:
            photo_url = resolved
            ydl_url = _photo_to_video_url(resolved)
            logger.info("Photo URL from error, retrying as: %s", ydl_url)
            try:
                info = _extract_info(ydl_url)
            except Exception as retry_exc:
                logger.warning("Retry also failed: %s", retry_exc)
                info = None
        else:
            raise

    # If yt-dlp returned images, use them
    images = (info or {}).get("images")
    if images:
        logger.info("yt-dlp returned %d image(s)", len(images))
        image_paths = _sync_download_images_from_info(images, output_dir)
        if not image_paths:
            raise DownloadError("Не удалось скачать фотографии")
        audio_path = _sync_download_audio_ytdlp(ydl_url, output_dir)
        return PhotoResult(image_paths=image_paths, audio_path=audio_path, dir=output_dir)

    # If we know it's a photo post (URL had /photo/), use tikwm.com
    if photo_url:
        return _sync_fetch_via_tikwm(photo_url, output_dir)

    # Regular video
    if not info:
        raise DownloadError("Не удалось получить информацию о контенте")

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
    except (FileTooLargeError, VideoUnavailableError, DownloadError):
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        msg = str(exc).lower()
        if "private" in msg or "unavailable" in msg or "does not exist" in msg:
            raise VideoUnavailableError("Видео недоступно или является приватным") from exc
        logger.exception("Unexpected download error for %s", url)
        raise DownloadError(f"Ошибка скачивания: {exc}") from exc


def cleanup_result(result: ContentResult) -> None:
    shutil.rmtree(result.dir, ignore_errors=True)
