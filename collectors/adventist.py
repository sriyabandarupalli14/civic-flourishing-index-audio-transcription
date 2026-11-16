"""Adventist WordPress catalog and sermon detail extraction."""

import re
from collections import deque
from contextlib import ExitStack
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from bs4 import BeautifulSoup

from .schema import Collection, Sermon
from .shared import fetch_metadata, is_media, public_url

AUDIO = {".mp3", ".m4a", ".ogg", ".wav"}
VIDEO = {".mp4", ".webm", ".m3u8"}


def catalog_entries(soup, page_url, archive_url, *, numbered):
    """Return direct children of the catalog path in document order."""
    archive = urlsplit(archive_url)
    path = re.escape(archive.path.rstrip("/")) + r"/[^/]+/?"
    selector = "a[href]" if numbered else "h2 a[href], h3 a[href]"
    for anchor in soup.select(selector):
        reference = anchor["href"]
        target = public_url(page_url, reference)
        parts = urlsplit(target)
        if (parts.hostname == archive.hostname and re.fullmatch(path, parts.path)
                and not (numbered and ("?" in reference or "#" in reference))):
            yield target, anchor.get_text(" ", strip=True)


def catalog_page_links(soup, page_url, archive_url):
    """Normalize only catalog pagination parameters, never media URLs."""
    archive = urlsplit(archive_url)
    for anchor in soup.select("a[href]"):
        target = urlsplit(public_url(page_url, anchor["href"]))
        if (target.hostname != archive.hostname
                or target.path.rstrip("/") != archive.path.rstrip("/")):
            continue
        params = sorted((key, values[0]) for key, values in parse_qs(target.query).items()
                        if re.fullmatch(r"query-\d+-page", key))
        if params:
            yield archive_url + "?" + urlencode(params)


def discover_archive(archive_url, fetch, result, *, page_parameter=None, max_pages=None):
    """Support sequential pages and links exposed by the catalog."""
    if page_parameter and not max_pages:
        raise ValueError("Sequential Adventist pagination requires a page limit")
    if page_parameter:
        pending = deque(
            archive_url if n == 1 else archive_url + "?" + urlencode({page_parameter: n})
            for n in range(1, max_pages + 1)
        )
    else:
        pending = deque([archive_url])
    queued = set(pending)
    posts = {}
    checked = empty_streak = 0
    stop = "no further pagination links"
    while pending:
        url = pending.popleft()
        base, html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        checked += 1
        before = len(posts)
        for target, title in catalog_entries(soup, base, archive_url, numbered=bool(page_parameter)):
            posts.setdefault(target, title)
        if page_parameter:
            empty_streak = empty_streak + 1 if before == len(posts) else 0
            if empty_streak == 2:
                stop = "two consecutive pages without new entries"
                break
            if not pending:
                stop = "configured page cap"
                result.limitations.append(f"Sequential catalog reached the {max_pages}-page cap")
        else:
            for target in catalog_page_links(soup, base, archive_url):
                if target not in queued:
                    queued.add(target)
                    pending.append(target)
    result.coverage.append(dict(archive_pages=checked, detail_pages=len(posts), stop=stop))
    return posts


def extract_metadata(soup, fallback_title, *, player_date=False):
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else fallback_title
    if player_date:
        time = soup.find("time")
        return title, (time.get("datetime") or "")[:10] if time else "", ""

    lines = []
    if heading:
        for text in heading.find_all_next(string=True):
            if text.parent.name in {"script", "style"}:
                continue
            value = text.strip()
            if value == "Related Content":
                break
            if value:
                lines.append(value)
    date = speaker = ""
    for index, line in enumerate(lines):
        label = line.rstrip(":").lower()
        if label == "date":
            for value in lines[index + 1:index + 5]:
                try:
                    date = datetime.strptime(value, "%B %d, %Y").date().isoformat()
                    break
                except ValueError:
                    continue
        if label in {"presenter", "presenters"} and index + 1 < len(lines):
            value = lines[index + 1]
            if value.lower() not in {"series", "tags", "date"}:
                speaker = value
    return title, date, speaker


def extract_media_urls(soup, base, *, primary_player=False):
    if primary_player:
        video = soup.find("video")
        if video:
            source = video.find("source")
            url = public_url(base, video.get("src") or (source.get("src") if source else None))
            if url:
                return [url]
        return []

    # The Union Springs player may sit outside the main content.
    audio = video = ""
    for element in soup.select("audio[src], video[src], source[src], a[href]"):
        url = public_url(base, element.get("src") or element.get("href"))
        suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
        if not audio and suffix in AUDIO:
            audio = url
        if not video and suffix in VIDEO:
            video = url
    return [url for url in (audio, video) if url]


def route_player_request(route):
    """Permit player setup while blocking recording requests and binary fetches."""
    request = route.request
    if (request.method == "GET" and request.resource_type in {"document", "script", "stylesheet"}
            and "range" not in request.headers and not is_media(request.url)):
        route.continue_()
    else:
        route.abort()


def open_player_page(stack):
    from playwright.sync_api import sync_playwright

    playwright = stack.enter_context(sync_playwright())
    browser = playwright.chromium.launch(headless=True)
    stack.callback(browser.close)
    context = browser.new_context(service_workers="block")
    context.route("**/*", route_player_request)
    return context.new_page()


def dynamic_player_url(page, url):
    """Try the first player's resolved URL, then muted initialization as in the notebook."""
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    get_src = """() => {
        const video = document.querySelector('video');
        if (!video) return '';
        return [video.currentSrc, video.getAttribute('src'),
                video.querySelector('source')?.getAttribute('src')]
            .find(value => value && !value.startsWith('blob:')) || '';
    }"""
    source = public_url(page.url, page.evaluate(get_src))
    if not source:
        page.evaluate("""() => document.querySelectorAll('video').forEach(video => {
            video.muted = true;
            video.play().catch(() => {});
        })""")
        page.wait_for_timeout(4000)
        source = public_url(page.url, page.evaluate(get_src))
    return source


def extract_sermon_page(url, fallback_title, fetch, *, player=None):
    base, html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    title, date, speaker = extract_metadata(soup, fallback_title, player_date=player is not None)
    row = Sermon(url, url, title=title, date=date, speaker=speaker,
                 date_basis="displayed entry date" if date else "")
    row.media_urls = extract_media_urls(soup, base, primary_player=player is not None)
    if player is not None and not row.media_urls:
        resolved = player(base)
        if resolved:
            row.media_urls.append(resolved)
    return row


def collect_adventist(archive_url, *, page_parameter=None, max_pages=None,
                     dynamic_player=False, fetch=fetch_metadata, player_factory=open_player_page):
    """Collect one Adventist archive with its registered pagination and player policy."""
    result = Collection()
    if dynamic_player:
        from playwright.sync_api import Error as BrowserError
        player_errors = (BrowserError,)
    else:
        player_errors = ()
    detail_errors = (requests.RequestException, ValueError) + player_errors

    with ExitStack() as stack:
        page = None

        def browser_page():
            nonlocal page
            if page is None:
                page = player_factory(stack)
            return page

        def render(url):
            live_page = browser_page()
            live_page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return live_page.url, live_page.content()

        def resolve_player(url):
            try:
                return dynamic_player_url(browser_page(), url)
            except player_errors as error:
                result.limitations.append(f"Player initialization failed ({type(error).__name__}): {url}")
                return ""

        def fetch_page(url):
            try:
                return fetch(url)
            except (requests.RequestException, ValueError):
                if dynamic_player:
                    return render(url)
                raise

        posts = discover_archive(archive_url, fetch_page, result,
                                 page_parameter=page_parameter, max_pages=max_pages)
        if not posts and dynamic_player:
            result.limitations.append("Static archive exposed no entries; tried browser rendering")
            posts = discover_archive(archive_url, render, result,
                                     page_parameter=page_parameter, max_pages=max_pages)
        if not posts:
            raise ValueError("No Adventist sermon entries found")

        for url, title in posts.items():
            try:
                row = extract_sermon_page(url, title, fetch_page,
                                          player=resolve_player if dynamic_player else None)
            except detail_errors as error:
                if not dynamic_player:
                    raise
                result.limitations.append(f"Detail page failed ({type(error).__name__}): {url}")
                row = Sermon(url, url, title=title)
            result.sermons[row.source_id] = row

    missing = sum(not row.media_urls for row in result.sermons.values())
    if missing:
        result.limitations.append(f"No media reference found for {missing} records")
    if any("x-amz-signature" in url.lower() for row in result.sermons.values() for url in row.media_urls):
        result.limitations.append("Signed media URLs may expire; refresh from the individual sermon page")
    return result
