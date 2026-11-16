"""Public Sermon.net player and playlist protocol."""

import json
import re
from datetime import datetime
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo

import requests

from .schema import Collection, Sermon
from .shared import fetch_metadata, is_media, public_url

def dictionaries(value):
    if isinstance(value, dict):
        yield value
        value = value.values()
    elif not isinstance(value, list):
        return
    for item in value:
        yield from dictionaries(item)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, dict)):
        for child in value.values() if isinstance(value, dict) else value:
            yield from strings(child)


def collect_sermonnet(archive_url, *, timezone, fetch=fetch_metadata):
    """Enumerate public player playlists and merge postings by platform ID."""
    base, html = fetch(archive_url)
    marker = re.search(r"\bvar\s+StudioAppData\s*=\s*", html)
    if not marker:
        raise ValueError("Sermon.net public player configuration missing")
    config, _ = json.JSONDecoder().raw_decode(html[marker.end():])
    playlists = {
        item["id"]: item for item in dictionaries(config)
        if {"id", "countPostings"} <= item.keys()
        and ("mediaCenterId" in item or "Postings" in item)
        and not (item.get("is_private") or item.get("is_protected"))
    }
    if not playlists:
        raise ValueError("No public Sermon.net playlists discovered")

    result = Collection()
    for playlist_id, playlist in playlists.items():
        title = playlist.get("title") or str(playlist_id)
        center = playlist.get("mediaCenterId") or config.get("currentMediaCenterId")
        expected = int(playlist["countPostings"])
        seen, excluded, recorded = set(), set(), set()
        for page in range(1, 101):
            if len(seen) >= expected:
                break
            query = urlencode(dict(playlistId=playlist_id, mediaCenterId=center,
                                   offset=page, limit=100))
            endpoint = urljoin(base, "/restapi/getPostingCollection?" + query)
            try:
                _, body = fetch(endpoint)
                payload = json.loads(body)
                postings = [item for group in payload["data"] for item in group["collection"]]
                expected = int(payload.get("count", expected))
            except (requests.RequestException, ValueError, KeyError, TypeError) as error:
                result.limitations.append(f"{title}: page {page} failed ({type(error).__name__})")
                break
            before = len(seen)
            for item in postings:
                detail = public_url(base, item.get("sharedLink"))
                key = str(item.get("id") or detail)
                if not key or key in seen:
                    continue
                seen.add(key)
                if (item.get("isLive") or item.get("type") == "live"
                        or item.get("private") or not detail):
                    excluded.add(key)
                    continue
                recorded.add(key)
                row = result.sermons.setdefault(key, Sermon(key, detail))
                row.playlists.append(title)
                row.title = row.title or item.get("title") or item.get("ogTitle") or ""
                speaker = item.get("speaker") or ""
                if isinstance(speaker, dict):
                    speaker = speaker.get("name") or ""
                row.speaker = row.speaker or speaker
                if not row.date and item.get("posted"):
                    row.date = datetime.fromtimestamp(
                        int(item["posted"]), ZoneInfo(timezone)
                    ).date().isoformat()
                    row.date_basis = f"platform posted date ({timezone})"
                sources = (item.get("mediaData") or {}).get("src") or {}
                if isinstance(sources, dict):
                    for kind in ("audio", "video"):
                        for value in strings(sources.get(kind)):
                            link = public_url(base, value)
                            if is_media(link) and link not in row.media_urls:
                                row.media_urls.append(link)
            if len(seen) == before:
                break
        result.coverage.append(dict(playlist=title, advertised=expected, seen=len(seen),
                                    recorded=len(recorded), excluded=len(excluded)))
        if len(seen) != expected:
            result.limitations.append(f"{title}: saw {len(seen)} of {expected} advertised postings")
    return result
