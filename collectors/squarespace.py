"""Metadata collector for the squarespace archive."""

import time
from html import unescape
from urllib.parse import unquote_plus, urljoin, urlsplit
from bs4 import BeautifulSoup

from .records import format_collection

def collect_gadsden_city(index_url, place_id, church_name, *, session):
    origin = urlsplit(index_url)
    base = f"{origin.scheme}://{origin.netloc}"
    index_url = base + "/sermons"

    def fetch(url):
        time.sleep(0.4)
        response = session.get(url, timeout=60)
        return BeautifulSoup(response.text, "html.parser")

    index = fetch(index_url)

    # Exclude navigation links when discovering sermon collections.
    navigation_paths = set()

    for link in index.select(
        "header a[href], nav a[href], footer a[href]"
    ):
        target = urljoin(index_url, link["href"])
        navigation_paths.add(urlsplit(target).path.rstrip("/"))

    excluded_paths = {
        "", "/Home", "/home", "/cart", "/donate", "/sermons",
        "/what-we-believe", "/where-we-meet", "/when-we-meet",
        "/testimonies", "/latest-news", "/contact",
    }

    collection_urls = {base + "/the-blood"}
    content = index.find("main") or index

    for link in content.select("a[href]"):
        address = link["href"].strip()

        if not address or address.startswith(("#", "mailto:", "tel:")):
            continue

        target = urljoin(index_url, address)
        parts = urlsplit(target)
        path = parts.path.rstrip("/")

        if parts.hostname != urlsplit(base).hostname:
            continue

        if path in excluded_paths or path in navigation_paths:
            continue

        if "." in path.rsplit("/", 1)[-1]:
            continue

        collection_urls.add(base + path)

    # Also check for recordings directly on the index.
    pages = [(index_url, index)]
    pages.extend(
        (url, None) for url in sorted(collection_urls)
    )

    rows_by_url = {}
    collections_without_audio = []

    for number, (page_url, soup) in enumerate(pages, start=1):

        if soup is None:
            soup = fetch(page_url)

        players = soup.select(".sqs-audio-embed[data-asset-url]")

        if not players and page_url != index_url:
            collections_without_audio.append(page_url)

        for player in players:
            audio_url = urljoin(
                page_url,
                player.get("data-asset-url", "").strip(),
            )

            if not urlsplit(audio_url).path.lower().endswith(
                (".mp3", ".m4a", ".ogg", ".wav")
            ):
                continue

            filename = unquote_plus(
                urlsplit(audio_url).path.rsplit("/", 1)[-1]
            )

            title = unescape(
                player.get("data-title") or filename
            ).strip()

            speaker = unescape(
                player.get("data-author") or ""
            ).strip()

            duration_sec = ""

            try:
                milliseconds = float(
                    player.get("data-length-in-milli-seconds") or ""
                )
                if milliseconds > 0:
                    duration_sec = round(milliseconds / 1000)
            except (ValueError, TypeError):
                pass

            if audio_url in rows_by_url:
                existing = rows_by_url[audio_url]
                if page_url not in existing["data_note"]:
                    existing["data_note"] += (
                        f"; also listed at: {page_url}"
                    )
                continue

            rows_by_url[audio_url] = {
                "place_id": place_id,
                "church_name": church_name,
                "sermon_title": title,
                "speaker": speaker,
                "sermon_date": "",
                "audio_url": audio_url,
                "duration_sec": duration_sec,
                "data_note": (
                    f"source collection: {page_url}; "
                    f"original filename: {filename}; "
                    "speaker and duration from audio-player metadata; "
                    "sermon date not supplied by player; "
                    "deduplicated by audio URL"
                ),
            }

    if not rows_by_url:
        raise ValueError("No audio recordings found. No CSV saved.")

    return format_collection(rows_by_url.values(), index_url, coverage=[{"collection_pages": len(pages), "without_audio": collections_without_audio}],
                             limitations=(["Some collection pages had no embedded audio"] if collections_without_audio else []))
