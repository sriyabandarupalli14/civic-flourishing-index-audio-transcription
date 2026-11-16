"""WordPress playlist, post archive, and Sermon Manager metadata collection."""

import json
import re
import time
from collections import deque
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from threading import Lock, local
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .schema import Collection, Sermon
from .shared import fetch_metadata, public_url

AUDIO_SUFFIXES = (".mp3", ".m4a", ".ogg", ".wav")
AUDIO_SELECTOR = "audio[src], audio source[src], a[href]"


@dataclass
class WordPressSermon(Sermon):
    duration_sec: int | str = ""
    data_note: str = ""
    place_id: str = ""
    church_name: str = ""


class WordPressCollection(Collection):
    record_type = WordPressSermon


def parse_date(value, formats):
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def audio_references(node, page_url, *, trust_audio_elements=False):
    urls = {}
    for tag in node.select(AUDIO_SELECTOR):
        target = public_url(page_url, tag.get("src") or tag.get("href"))
        if target and (urlsplit(target).path.lower().endswith(AUDIO_SUFFIXES)
                       or (trust_audio_elements and tag.name != "a")):
            urls[target] = None
    return list(urls)


def same_site_links(soup, page_url, archive_url):
    host = urlsplit(archive_url).hostname
    for anchor in soup.select("a[href]"):
        target = public_url(page_url, anchor["href"])
        if urlsplit(target).hostname == host:
            yield anchor, target


def numbered_page(target, archive_url, *, paged_query=False):
    parts = urlsplit(target)
    path = urlsplit(archive_url).path.rstrip("/")
    match = re.fullmatch(re.escape(path) + r"/page/(\d+)/?", parts.path)
    if match:
        number = int(match.group(1))
    elif paged_query and parts.path.rstrip("/") == path:
        value = parse_qs(parts.query).get("paged", [""])[0]
        number = int(value) if value.isdigit() else 0
    else:
        number = 0
    if number == 1:
        return archive_url
    if number > 1:
        return f"{archive_url.rstrip('/')}/page/{number}/"
    return ""


def discover_posts(archive_url, fetch, scan):
    pending = deque([archive_url])
    queued = {archive_url}
    posts = {}
    while pending:
        url = pending.popleft()
        _, html = fetch(url)
        found, following = scan(BeautifulSoup(html, "html.parser"), url, archive_url)
        for target, metadata in found:
            posts.setdefault(target, metadata)
        for target in following:
            if target not in queued:
                queued.add(target)
                pending.append(target)
    if not posts:
        raise ValueError("No WordPress sermon posts found")
    return posts, len(queued)


def make_wordpress_fetch(stack, *, retry=False):
    """Reuse a metadata-only session per thread and pace requests across the source."""
    state = local()
    lock = Lock()
    next_request = 0.0

    def fetch(url):
        nonlocal next_request
        if not hasattr(state, "session"):
            # Register each thread's session for cleanup after the executor joins.
            with lock:
                session = stack.enter_context(requests.Session())
                state.session = session
            if retry:
                session.mount("https://", HTTPAdapter(max_retries=Retry(
                    total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                )))
        with lock:
            time.sleep(max(0, next_request - time.monotonic()))
            next_request = time.monotonic() + 0.4
        return fetch_metadata(url, get=state.session.get, pause=0)

    return fetch


def collect_posts(archive_url, scan, parse_detail, *, fetch=None, retry=False, workers=4):
    result = WordPressCollection()
    with ExitStack() as stack:
        if fetch is None:
            fetch = make_wordpress_fetch(stack, retry=retry)
        posts, pages = discover_posts(archive_url, fetch, scan)
        result.coverage.append(dict(archive_pages=pages, detail_pages=len(posts)))

        def read(post):
            url, metadata = post
            try:
                _, html = fetch(url)
            except (requests.RequestException, ValueError) as error:
                return [], f"Detail page failed ({type(error).__name__}): {url}"
            records = parse_detail(BeautifulSoup(html, "html.parser"), url, metadata)
            return records, ""

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for records, limitation in pool.map(read, posts.items()):
                if limitation:
                    result.limitations.append(limitation)
                for record in records:
                    result.sermons[record.source_id] = record
    if not result.sermons:
        raise ValueError("No WordPress sermon detail pages could be collected")
    return result


def post_records(url, title, date, date_basis, speaker, audio, notes, missing_note):
    if not audio:
        notes = [*notes, missing_note]
    return [
        WordPressSermon(
            f"{url}#{index}", url, title=title, date=date,
            date_basis=date_basis if date else "", speaker=speaker,
            media_urls=[media] if media else [], data_note="; ".join(notes),
        )
        for index, media in enumerate(audio or [""], 1)
    ]


def collect_embedded_playlist(archive_url, *, parse_track, fetch=fetch_metadata):
    base, html = fetch(archive_url)
    block = re.search(
        r'<script\b[^>]*\bwp-playlist-script\b[^>]*>(.*?)</script>',
        html, flags=re.DOTALL | re.IGNORECASE,
    )
    if not block:
        raise ValueError("No WordPress playlist block found")
    tracks = json.loads(block.group(1).strip()).get("tracks", [])
    if not tracks:
        raise ValueError("The playlist block contains no tracks")
    result = WordPressCollection()
    result.coverage.append(dict(playlist_blocks_read=1, tracks=len(tracks)))
    for number, track in enumerate(tracks, 1):
        row = parse_track(track, base, number)
        result.sermons[row.source_id] = row
    return result
