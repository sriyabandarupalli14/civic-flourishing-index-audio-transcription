"""Metadata resolution and bounded HTTP reads. Signed URLs stay intact."""

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

AUDIO = (".mp3", ".m4a", ".aac", ".aiff", ".wav", ".ogg", ".flac", ".opus")
VIDEO = (".mp4", ".m4v", ".webm", ".mov", ".mkv")


class MediaError(Exception):
    """Reason codes are safe to write to local logs."""

    def __init__(self, code, *, retryable=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def kind_for(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return "unsupported"
    if (parts.scheme not in {"http", "https"} or not host or parts.username
            or any(char.isspace() for char in url)):
        return "unsupported"
    path = parts.path.lower()
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(AUDIO):
        return "audio"
    if path.endswith(VIDEO):
        return "video"
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be",
                "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        return "youtube"
    if host in {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}:
        return "soundcloud"
    return "unsupported"


def expired(url, now=None):
    query = {k.lower(): v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    now = now or datetime.now(timezone.utc)
    for prefix in ("x-amz-", "x-goog-"):
        try:
            stamp = datetime.strptime(query[prefix + "date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            expiry = stamp + timedelta(seconds=int(query[prefix + "expires"]))
            return expiry <= now
        except (KeyError, ValueError, OverflowError):
            continue
    for key in ("expire", "expires"):
        if re.fullmatch(r"\d{10}", query.get(key, "")):
            return int(query[key]) <= now.timestamp()
    return False


def require_http(url):
    try:
        p = urlsplit(url)
        if p.scheme in {"http", "https"} and p.hostname and not p.username and not any(c.isspace() for c in url):
            return
    except ValueError:
        pass
    raise MediaError("invalid_http_reference")


@dataclass(frozen=True)
class Resolved:
    url: str
    kind: str
    headers: dict


def check_status(response):
    status = response.status_code
    if status < 200 or status >= 300:
        raise MediaError(f"http_{status}", retryable=status in {408, 425, 429, 500, 502, 503, 504})


def transfer(session, url, *, headers=None, limit, sink=None, prefix=False, expected="media"):
    """Read a bounded body, or only the prefix even when Range is ignored."""
    require_http(url)
    if expired(url):
        raise MediaError("expired_signed_url")
    headers = dict(headers or {})
    if prefix:
        headers["Range"] = f"bytes=0-{limit - 1}"
    try:
        with session.get(url, headers=headers, stream=True, timeout=(8, 20)) as response:
            check_status(response)
            require_http(response.url)
            if not prefix and expected == "media" and response.status_code == 206:
                raise MediaError("unexpected_partial_response", retryable=True)
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            length = response.headers.get("Content-Length", "")
            if not prefix and length.isdigit() and int(length) > limit:
                raise MediaError("byte_limit_exceeded")
            if expected in {"html", "json"} and mime and not (
                mime.startswith("text/") or "html" in mime or "json" in mime
            ):
                raise MediaError("unexpected_metadata_response")
            if ((expected in {"media", "manifest"} and ("html" in mime or "json" in mime or "xml" in mime))
                    or (expected == "media" and mime.startswith("text/"))):
                raise MediaError("non_media_response")
            chunks, size = [], 0
            deadline = time.monotonic() + 120
            for chunk in response.iter_content(min(limit, 65536)):
                if not chunk:
                    continue
                if time.monotonic() > deadline:
                    raise MediaError("read_deadline_exceeded", retryable=True)
                if prefix:
                    chunk = chunk[:limit - size]
                if not size and expected == "media":
                    start = chunk.lstrip().lower()
                    if start.startswith((b"<", b"{", b"[", b"#extm3u")):
                        raise MediaError("non_media_response")
                size += len(chunk)
                if size > limit:
                    raise MediaError("byte_limit_exceeded")
                if sink is None:
                    chunks.append(chunk)
                else:
                    sink.write(chunk)
                if prefix and size >= limit:
                    break
            if not size:
                raise MediaError("empty_response")
            if (not prefix and length.isdigit() and not response.headers.get("Content-Encoding")
                    and size != int(length)):
                raise MediaError("incomplete_response", retryable=True)
            return b"".join(chunks), response.url, size
    except requests.RequestException:
        raise MediaError("http_transport_error", retryable=True) from None


def head_status(session, resolved):
    """A HEAD result is diagnostic. A range GET supplies the access evidence."""
    try:
        with session.head(resolved.url, headers=resolved.headers, allow_redirects=True,
                          timeout=(8, 15)) as response:
            return response.status_code
    except requests.RequestException:
        return None


class QuietExtractor:
    def debug(self, message):
        pass

    warning = error = debug


def resolve_player(url, *, downloader=None):
    if downloader is None:
        from yt_dlp import YoutubeDL
        downloader = YoutubeDL
    from yt_dlp.utils import DownloadError

    options = dict(format="bestaudio/best", skip_download=True, noplaylist=True, quiet=True,
                   no_warnings=True, logger=QuietExtractor(), socket_timeout=12, retries=0,
                   extractor_retries=0, cachedir=False)
    try:
        with downloader(options) as extractor:
            info = extractor.extract_info(url, download=False)
    except (DownloadError, OSError):
        raise MediaError("player_extraction_failed") from None
    if not info or info.get("_type") in {"playlist", "multi_video"} or not info.get("url"):
        raise MediaError("player_did_not_resolve_individual_media")
    source = info["url"]
    require_http(source)
    protocol = info.get("protocol", "")
    if protocol.startswith("m3u8") or kind_for(source) == "hls":
        kind = "hls"
    elif protocol in {"http", "https"}:
        kind = "audio" if info.get("vcodec") == "none" else "video"
    else:
        raise MediaError("unsupported_player_transport")
    # Preserve extractor-required public request headers. Authentication is not supplied.
    allowed = {"user-agent", "accept", "accept-language", "origin", "referer"}
    headers = {k: v for k, v in (info.get("http_headers") or {}).items() if k.lower() in allowed}
    return Resolved(source, kind, headers)


def resolve(url):
    kind = kind_for(url)
    if kind in {"youtube", "soundcloud"}:
        return resolve_player(url)
    if kind == "unsupported":
        raise MediaError("unsupported_reference")
    return Resolved(url, kind, {})
