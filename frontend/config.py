"""Frontend settings, read from environment variables / frontend/.env."""

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))


def _prefer_ipv4(url: str) -> str:
    """'localhost' -> '127.0.0.1'. On Windows 'localhost' resolves to IPv6 ::1 first;
    uvicorn listens on IPv4 only, so every request waited ~2 s for the IPv6 attempt."""
    parts = urlsplit(url)
    if parts.hostname != "localhost":
        return url
    netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


BACKEND_URL = _prefer_ipv4(os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))
# Importing a large Tally export (upload + parse) can take minutes.
UPLOAD_TIMEOUT_SECONDS = float(os.getenv("UPLOAD_TIMEOUT_SECONDS", "1800"))
# How long read results are cached. Every write through this app clears them at once.
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))
