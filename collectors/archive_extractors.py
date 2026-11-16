"""Platform parsers used to replay public HTML and JSON metadata captures."""

import base64
import binascii
import re
from datetime import datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .shared import is_media, public_url, selected_text

AUDIO = {".mp3", ".m4a", ".wav", ".ogg", ".aac"}
PLAYER = re.compile(
    r"(?:youtu(?:be\.com|\.be)|vimeo\.com|soundcloud\.com|subsplash\.com|video\.ibm\.com|"
    r"facebook\.com/.*/videos|facebook\.com/share/v/)", re.I,
)
ITEM = re.compile(r"embed|watch\?|youtu\.be/|player|/media/|/recorded/|/videos/|/share/v/|vimeo\.com/\d|soundcloud\.com/.+/", re.I)
PRINTED_DATE = re.compile(r"\b(?:[A-Za-z]{3,9} \d{1,2}, \d{4}|\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2}))\b")


def observation(page, media="", title="", date="", speaker="", *, date_meaning="",
                platform_id="", media_kind=None):
    path = urlsplit(media).path.lower()
    audio = any(path.endswith(suffix) for suffix in AUDIO)
    if media_kind is None:
        if path.endswith((".m3u8", ".mpd")):
            media_kind = "stream_manifest"
        elif is_media(media):
            media_kind = "direct_file_reference"
        else:
            media_kind = "player_reference" if media else ""
    return {
        "source_page_url": page, "media_url": media, "sermon_title": title,
        "sermon_date": date, "speaker": speaker, "date_meaning": date_meaning,
        "media_kind": media_kind, "audio_url": media if audio else "",
        "video_url": media if not audio else "", "platform_recording_id": platform_id,
    }


def printed_date(text):
    match = PRINTED_DATE.search(text)
    if match:
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
            try:
                return datetime.strptime(match[0], fmt).date().isoformat()
            except ValueError:
                continue
    return ""


def html_references(soup, url):
    """Extract individual references and nearby source text without fetching media."""
    seen = set()
    for node in soup.select("[href], [src], [data-src], [data-href]"):
        raw = next((node.get(key) for key in ("href", "src", "data-src", "data-href")
                    if node.get(key)), "")
        media = public_url(url, raw)
        if media in seen or not (is_media(media) or (PLAYER.search(media) and ITEM.search(media))):
            continue
        seen.add(media)
        box = node
        for _ in range(4):
            if not box.parent or box.name in {"article", "tr", "li"}:
                break
            box = box.parent
        title = node.get_text(" ", strip=True) if node.name == "a" else ""
        if not title or re.fullmatch("download|listen|watch|audio|video|play|mp3", title, re.I):
            title = selected_text(box, "h1,h2,h3,h4,.sermon-title,.title") or selected_text(soup, "h1")
        stamp = box.select_one("time[datetime]")
        yield observation(url, media, title, stamp["datetime"] if stamp else "",
                          selected_text(box, '.sermon-speaker,.speaker,.preacher,[itemprop="author"]'),
                          date_meaning="source time element" if stamp else "")


def flat_youtube(payload):
    """Reuse the Stage 1 YouTube ID parser on a saved channel-tab payload."""
    from .youtube import youtube_ids

    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        video = entry.get("id") or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            ids = youtube_ids(entry.get("url") or entry.get("webpage_url") or "")
            video = ids[0] if ids else ""
        if not video:
            continue
        url = "https://www.youtube.com/watch?v=" + video
        day = entry.get("upload_date") or ""
        date = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else ""
        row = observation(url, url, entry.get("title") or "", date, platform_id=video,
                          date_meaning="platform upload date" if date else "")
        row["duration_sec"] = entry.get("duration") or ""
        yield row


def jwplayer_references(soup, url):
    hidden = [str(node["value"]) for node in soup.select('input[type="hidden"][value]')]
    text = [str(soup)]
    for value in hidden:
        try:
            text.append(base64.b64decode(value, validate=True).decode("utf-8", errors="ignore"))
        except (binascii.Error, ValueError):
            continue
    urls = dict.fromkeys(re.findall(
        r"""https://content\.jwplatform\.com/manifests/[\w-]+\.m3u8(?:\?[^\s"'<>\\]+)?""",
        " ".join(text),
    ))
    title = next((h.get_text(" ", strip=True) for h in soup.select("h1,h2,h3")
                  if "Flow" in h.get_text() or re.search(r"\d-\d", h.get_text())), "")
    date = re.search(r"\b(?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}",
                     soup.get_text(" ", strip=True))
    for media in urls:
        yield observation(url, media, title, date[0] if date else "", date_meaning="displayed video date")


def drive_media(items, folder_url):
    for item in items or []:
        if not isinstance(item, list) or len(item) < 4:
            continue
        key, title, mime = item[0], item[2], item[3]
        if not isinstance(key, str) or not isinstance(title, str) or not isinstance(mime, str):
            continue
        if mime.startswith(("audio/", "video/")) or is_media(title):
            url = f"https://drive.google.com/file/d/{key}/view"
            row = observation(folder_url, url, title, platform_id=key)
            row["source_mime_type"] = mime
            yield row


def wordpress_attachments(items, site):
    for item in items:
        if not isinstance(item, dict):
            continue
        url = public_url(site, item.get("source_url"))
        if url:
            title = BeautifulSoup((item.get("title") or {}).get("rendered", ""), "html.parser").get_text(" ", strip=True)
            row = observation(f"{site.rstrip('/')}/?attachment_id={item['id']}", url, title,
                              item.get("date") or "", platform_id=str(item["id"]),
                              date_meaning="attachment upload date, not service date")
            row["content_review"] = ("sermon-page attachment" if item.get("post") == 31282
                                     else "attachment association requires review")
            yield row


def rss_enclosures(xml, feed, playlist_id=""):
    for item in ElementTree.fromstring(xml).iter("item"):
        fields = {child.tag.rsplit("}", 1)[-1]: child.text or "" for child in item}
        for enclosure in item.findall("enclosure"):
            media = public_url(feed, enclosure.get("url"))
            if not media:
                continue
            ident = re.search(r"/(\d{7,})", urlsplit(media).path)
            row = observation(fields.get("link") or feed, media,
                              fields.get("title", ""), fields.get("pubDate", ""),
                              fields.get("author", ""), date_meaning="RSS publication date",
                              platform_id=ident[1] if ident else "")
            row.update(duration_sec=fields.get("duration", ""), playlist_id=playlist_id,
                       source_archive_url=feed)
            yield row
