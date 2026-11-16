"""Source-independent metadata transport, URL handling, and HTML text extraction."""

import time
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

import requests

MEDIA_SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac",
                  ".mp4", ".m4v", ".webm", ".m3u8", ".mpd"}


def public_url(base, value):
    if not value or value.strip().lower() in {"undefined", "null", "#"}:
        return ""
    url = urljoin(base, value.strip())
    parts = urlsplit(url)
    if parts.scheme in {"https", "http"} and parts.hostname and not parts.username:
        return parts._replace(fragment="").geturl()
    return ""


def is_media(url):
    return Path(urlsplit(url).path).suffix.lower() in MEDIA_SUFFIXES


def fetch_metadata(url, *, blocked_query_keys=(), get=None, pause=0.8):
    """Read bounded HTML or JSON, rejecting media before reading its body."""
    query = dict(parse_qsl(urlsplit(url).query))
    if is_media(url) or any(key in query for key in blocked_query_keys):
        raise ValueError("Recording URLs are outside metadata collection")
    if pause:
        time.sleep(pause)
    get = get or requests.get
    with get(url, stream=True, timeout=(8, 25), headers={
        "User-Agent": "CFI-Research-Metadata/1.0",
        "Accept": "text/html, application/json, application/xhtml+xml",
    }) as response:
        response.raise_for_status()
        mime = response.headers.get("Content-Type", "").split(";")[0].lower()
        if mime not in {"text/html", "application/xhtml+xml", "application/json", "text/plain"}:
            raise ValueError(f"Unexpected metadata response type: {mime or 'missing'}")
        chunks, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > 12 * 1024 * 1024:
                raise ValueError("Metadata response exceeded 12 MiB")
            chunks.append(chunk)
        return response.url, b"".join(chunks).decode("utf-8-sig", errors="replace")


def selected_text(node, selector):
    element = node.select_one(selector)
    return element.get_text(" ", strip=True) if element else ""
