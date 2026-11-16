"""Metadata collector for the youtube archive."""

import json
import re
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from playwright.async_api import Error as PlaywrightError, async_playwright
from yt_dlp import YoutubeDL

from .metadata import MetadataClient, metadata_browser_page
from .records import format_collection
from .shared import public_url

async def render_until_stable(url, max_scrolls=80, pause=1.2, patience=3):
    """Open a page in a headless browser, scroll to the bottom, and return the final HTML.

    Scrolls one screen at a time so lazy content loads, and clicks any
    "load more" style button. Stops when the page height and the number of
    media elements are unchanged for `patience` checks in a row while at the
    bottom, or after `max_scrolls` scrolls.
    """
    # Runs inside the browser: page height, media element count, and whether we are at the bottom
    state_js = """() => ({
        height: document.body.scrollHeight,
        media: document.querySelectorAll(
            'iframe, video, audio, a[href$=".mp3"], a[href$=".mp4"], a[href$=".m4a"]').length,
        at_bottom: window.scrollY + window.innerHeight >= document.body.scrollHeight - 5
    })"""

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await metadata_browser_page(browser, viewport={"width": 1280, "height": 800})
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)   # let the first scripts finish

        last, quiet_checks = None, 0
        for _ in range(max_scrolls):
            for btn in await page.get_by_role(
                    "button", name=re.compile(r"load more|show more|view more", re.I)).all():
                try:
                    await btn.click(timeout=1000)
                except Exception:
                    pass
            await page.mouse.wheel(0, 700)   # roughly one screen
            await page.wait_for_timeout(int(pause * 1000))

            now = await page.evaluate(state_js)
            same = last is not None and (now["height"], now["media"]) == (last["height"], last["media"])
            quiet_checks = quiet_checks + 1 if (same and now["at_bottom"]) else 0   # only counts at the bottom
            last = now
            if quiet_checks >= patience:
                break

        html = await page.content()
        await browser.close()
    return html


def youtube_ids(html):
    """Return the YouTube video ids found in a page, in page order, without repeats."""
    html = html.replace("\\/", "/")   # undo escaped slashes inside scripts
    ids = re.findall(
        r"(?:youtube(?:-nocookie)?\.com/(?:embed|live|shorts)/|youtu\.be/|youtube\.com/watch\?v=)([\w-]{11})", html)
    return list(dict.fromkeys(ids))


def wix_youtube_ids(html, page_url, fetch):
    """Read public Wix feature-prefetch JSON for hidden video components."""
    soup = BeautifulSoup(html, "html.parser")
    allowed = ("wixstatic.com", "wix.com", "parastorage.com", "wixmp.com")
    ids = []
    for link in soup.select('link[href][id^="features_"]'):
        url = public_url(page_url, link["href"])
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https" or not any(host == d or host.endswith("." + d) for d in allowed):
            continue
        try:
            payload = fetch(url).json()
        except (requests.RequestException, ValueError):
            continue
        pending = [payload]
        while pending:
            obj = pending.pop()
            if isinstance(obj, dict):
                kind = str(obj.get("videoType") or obj.get("type") or "").lower()
                video = obj.get("youtubeId") or obj.get("videoId")
                if (isinstance(video, str) and re.fullmatch(r"[\w-]{11}", video)
                        and (obj.get("youtubeId") or "youtube" in kind)):
                    ids.append(video)
                pending.extend(reversed(list(obj.values())))
            elif isinstance(obj, list):
                pending.extend(reversed(obj))
            elif isinstance(obj, str):
                ids.extend(youtube_ids(obj))
    return list(dict.fromkeys(ids))


def read_wix_videos(page_url):
    with MetadataClient() as client:
        page = client.get(page_url, timeout=25)
        return wix_youtube_ids(page.text, page.url, lambda url: client.get(url, timeout=25))


def youtube_metadata(links, *, downloader=YoutubeDL):
    """Look up title, upload date, and duration for each YouTube link (no download)."""
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        # In testing, YouTube only answered when the android client was requested.
        # If this stops working, remove this line to see the default behavior.
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }
    rows = []
    with downloader(opts) as ydl:
        for link in links:
            row = {"audio_url": link, "sermon_title": "", "sermon_date": "",
                   "duration_sec": "", "data_note": ""}
            try:
                info = ydl.extract_info(link, download=False)
                d = info.get("upload_date") or ""   # comes as YYYYMMDD
                row["sermon_title"] = info.get("title", "")
                row["sermon_date"] = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else ""
                row["duration_sec"] = info.get("duration") or ""
                row["data_note"] = "date is YouTube upload date, may differ from service date"
            except Exception as e:
                row["data_note"] = f"metadata unavailable: {type(e).__name__}"
            rows.append(row)
    return rows


async def collect_youtube(url, *, fallback_urls=(), render=render_until_stable,
                          downloader=YoutubeDL, wix_lookup=read_wix_videos):
    """Type 1. Return one row per YouTube video embedded on the sermons page at `url`."""
    coverage = []
    links = []
    for page_url in dict.fromkeys((url, *fallback_urls)):
        try:
            ids = youtube_ids(await render(page_url))
        except PlaywrightError as error:
            coverage.append({"archive_page": page_url, "error": type(error).__name__})
            ids = []
        route = "rendered embeds"
        if not ids and page_url in fallback_urls:
            try:
                ids = wix_lookup(page_url)
                route = "public Wix component metadata"
            except (requests.RequestException, ValueError) as error:
                coverage.append({"archive_page": page_url, "error": type(error).__name__})
        coverage.append({"archive_page": page_url, "embedded_video_ids": len(ids), "route": route})
        if ids:
            links = ["https://www.youtube.com/watch?v=" + video for video in ids]
            break
    rows = youtube_metadata(links, downloader=downloader)
    for row in rows:
        row["source_page"] = row["audio_url"]
        row["video_url"] = row.pop("audio_url")
        row["date_basis"] = "YouTube upload date" if row["sermon_date"] else ""
    if not rows:
        raise ValueError("No YouTube videos detected on the rendered archive")
    return format_collection(rows, url, coverage=coverage,
                             limitations=["Watch-page URLs only; no audio resolution or download"])
