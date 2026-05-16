import re

TIKTOK_PATTERN = re.compile(
    r"https?://(www\.tiktok\.com/@[\w.]+/(video|photo)/\d+|vm\.tiktok\.com/\w+|vt\.tiktok\.com/\w+)",
    re.IGNORECASE,
)


def is_tiktok_url(text: str) -> bool:
    return bool(TIKTOK_PATTERN.search(text))


def extract_tiktok_url(text: str) -> str | None:
    match = TIKTOK_PATTERN.search(text)
    return match.group(0) if match else None
