"""WordPress Sermon Browser archive, post, and download-link markup."""

import re
from functools import partial
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .schema import Collection, Sermon
from .shared import fetch_metadata, is_media, public_url, selected_text

DOWNLOAD_QUERY_KEY = "download_mp3"
fetch_sermon_browser_metadata = partial(fetch_metadata, blocked_query_keys=(DOWNLOAD_QUERY_KEY,))

def extract_media_references(row, node, base):
    selector = ("input.mp3[value], audio[src], video[src], audio source[src], "
                "video source[src], a.download_btn[href], .btn_container.video a[href]")
    for element in node.select(selector):
        link = public_url(base, element.get("src") or element.get("value") or element.get("href"))
        if link:
            links = row.media_urls if is_media(link) else row.unresolved_urls
            if link not in links:
                links.append(link)


def collect_sermon_browser(archive_url, *, fetch=fetch_sermon_browser_metadata):
    """Traverse public sermon cards, pagination, and individual WordPress posts."""
    result = Collection()
    archive_host = urlsplit(archive_url).hostname.removeprefix("www.")
    pending, visited = [archive_url], set()
    while pending and len(visited) < 100:
        url = pending.pop(0)
        visited.add(url)
        try:
            base, html = fetch(url)
        except (requests.RequestException, ValueError) as error:
            result.limitations.append(f"Archive page failed ({type(error).__name__}): {url}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        if len(visited) == 1:
            archive_host = urlsplit(base).hostname.removeprefix("www.")
        cards = soup.select('li[id^="sermon_"]')
        for card in cards:
            post = card.select_one("[data-href].fb-share-button, .title a[href]")
            link = public_url(base, (post.get("data-href") or post.get("href")) if post else "")
            if not link:
                download = card.select_one("a.download_btn[href]")
                if download:
                    parts = urlsplit(public_url(base, download["href"]))
                    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != DOWNLOAD_QUERY_KEY])
                    candidate = urlunsplit(parts._replace(query=query))
                    if re.match(r"^/sermons/[^/]+/?$", parts.path) and not is_media(candidate):
                        link = candidate
            key = card["id"].removeprefix("sermon_")
            row = result.sermons.setdefault(key, Sermon(key, link))
            row.title = row.title or selected_text(card, ".title")
            row.date = row.date or selected_text(card, ".date")
            if row.date:
                row.date_basis = "displayed archive date"
            row.speaker = row.speaker or selected_text(card, ".preacher a, .preacher").removeprefix("By:").strip()
            extract_media_references(row, card, base)
        for anchor in soup.select('a[rel="next"][href], a.page-numbers[href], .pagination a[href]'):
            target = public_url(base, anchor["href"])
            parts = urlsplit(target)
            if (parts.hostname and parts.hostname.removeprefix("www.") == archive_host
                    and re.match(r"^/(?:media/)?sermons(?:/page/\d+)?/?$", parts.path)
                    and target not in visited and target not in pending):
                pending.append(target)
        result.coverage.append(dict(archive_page=base, cards=len(cards)))
    if pending:
        result.limitations.append("Archive pagination reached 100 pages")
    if not result.sermons:
        result.limitations.append("No sermon cards found on the inspected archive pages")

    for row in result.sermons.values():
        detail = urlsplit(row.sermon_url)
        if (not detail.hostname or detail.hostname.removeprefix("www.") != archive_host
                or is_media(row.sermon_url)
                or DOWNLOAD_QUERY_KEY in dict(parse_qsl(detail.query))):
            continue
        try:
            base, html = fetch(row.sermon_url)
        except (requests.RequestException, ValueError) as error:
            result.limitations.append(f"Detail page for {row.source_id} failed ({type(error).__name__})")
            continue
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("article") or soup.select_one("main") or soup
        row.title = row.title or selected_text(content, ".entry-title, h1")
        row.speaker = row.speaker or selected_text(content, ".preacher a, .preacher").removeprefix("By:").strip()
        if not row.date:
            row.date = selected_text(content, ".date, time")
            if row.date:
                row.date_basis = "displayed post date"
        extract_media_references(row, content, base)
    return result
