import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
MAX_FILE_SIZE_BYTES: int = int(os.getenv("MAX_FILE_SIZE_MB", "50")) * 1024 * 1024
DOWNLOAD_TIMEOUT_SEC: int = int(os.getenv("DOWNLOAD_TIMEOUT_SEC", "60"))
DOWNLOADS_DIR: str = os.path.join(os.path.dirname(__file__), "downloads")
PROXY_URL: str | None = os.getenv("PROXY_URL")  # e.g. socks5://user:pass@host:port
