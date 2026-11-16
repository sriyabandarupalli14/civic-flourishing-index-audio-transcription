"""Church-specific discovery and parsing rules for the WordPress adapters."""

import re
from html import unescape
from urllib.parse import urlsplit

from .shared import fetch_metadata, public_url, selected_text
from .wordpress import (
    WordPressSermon, audio_references, collect_embedded_playlist, collect_posts,
    numbered_page, parse_date, post_records, same_site_links,
)


def gateway_date(title):
    title = unescape(title or "").strip()
    iso = re.match(r"^(\d{4}-\d{2}-\d{2})(?=\s|$)", title)
    if iso:
        return parse_date(iso[1], ("%Y-%m-%d",))
    words = re.match(r"^([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", title, re.I)
    return parse_date(" ".join(words.groups()), ("%B %d %Y",)) if words else ""


def gateway_speaker(title):
    title = unescape(title or "").strip()
    suffix = re.search(r"\(([^()]+)\)\s*$", title)
    if suffix:
        return suffix[1].strip()
    title = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[-–—:]?\s*", "", title)
    title = re.sub(r"^[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\s*[-–—:]?\s*",
                   "", title, flags=re.I).strip(" -–—:|")
    role = re.match(r"^(?:Pastor|Reverend|Rev\.?)\s+(.+)$", title, re.I)
    return role[1].strip() if role else ""


def gateway_duration(value):
    if value:
        try:
            parts = [int(part) for part in str(value).split(":")]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
        except (ValueError, TypeError):
            pass
    return ""


def gateway_track(track, base, number):
    meta = track.get("meta") or {}
    title = unescape(track.get("title") or meta.get("title") or "").strip()
    date, posted = gateway_date(title), meta.get("date") or ""
    notes = []
    if posted:
        notes.append(f"playlist posted date: {posted}")
        posted_iso = parse_date(posted, ("%B %d, %Y",))
        if not posted_iso:
            notes.append("posted date format unparsed")
        elif date and posted_iso != date:
            notes.append("posted date differs from title date")
    media = public_url(base, track.get("src"))
    return WordPressSermon(
        str(number), base, title=title, date=date, date_basis="title date" if date else "",
        speaker=gateway_speaker(title), media_urls=[media] if media else [],
        duration_sec=gateway_duration(meta.get("length_formatted")), data_note="; ".join(notes),
    )


def collect_gateway(archive_url, *, fetch=fetch_metadata):
    return collect_embedded_playlist(archive_url, parse_track=gateway_track, fetch=fetch)


def branch_cove_archive(soup, page, archive):
    posts, following = [], []
    for anchor, target in same_site_links(soup, page, archive):
        path, label = urlsplit(target).path, anchor.get_text(" ", strip=True)
        if re.match(r"^/\d{4}/\d{2}/\d{2}/", path) and re.match(r"^Sermon\b", label, re.I):
            posts.append((target, label))
        if re.fullmatch(r"/posts/page/\d+/?", path):
            following.append(target)
    return posts, following


def branch_cove_detail(soup, url, fallback_title):
    article = soup.find("article") or soup
    heading = article.select_one("h1.entry-title, h1, h2.entry-title")
    title = heading.get_text(" ", strip=True) if heading else fallback_title
    content = article.select_one(".entry-content") or article.select_one(".post-content") or article
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", title)
    date = parse_date(match[0], ("%Y-%m-%d",)) if match else ""
    speaker = ""
    for line in content.get_text("\n", strip=True).splitlines():
        match = re.search(r"\b(?:Pastor|Elder|Rev(?:erend)?\.?)\s+(.+)$", line, re.I)
        if match:
            speaker = match[1].strip()
            break
    return post_records(url, title, date, "title date", speaker,
                        audio_references(content, url, trust_audio_elements=True),
                        [f"source post: {url}", "duration not collected"],
                        "no direct audio link detected; review required")


def collect_branch_cove(archive_url, *, fetch=None, workers=4):
    return collect_posts(archive_url, branch_cove_archive, branch_cove_detail,
                         fetch=fetch, workers=workers)


def vernon_display_date(text):
    match = re.search(r"\b([A-Za-z]+ \d{1,2}, \d{4})\b", text)
    return parse_date(match[1], ("%b %d, %Y", "%B %d, %Y")) if match else ""


def vernon_speaker(text):
    match = re.search(r"\bby\s+([^\n|]+)", text, re.I)
    return match[1].strip() if match else ""


def vernon_archive(soup, page, archive):
    posts = []
    host = urlsplit(archive).hostname
    for heading in soup.select("h3"):
        anchor = heading.find("a", href=True)
        if not anchor:
            continue
        target = public_url(page, anchor["href"])
        parts = urlsplit(target)
        if (parts.hostname != host or not re.fullmatch(r"/sermons/[^/]+/?", parts.path)
                or parts.path.rstrip("/").endswith("/feed")):
            continue
        fragments = []
        for element in heading.next_siblings:
            if getattr(element, "name", None) in ("h2", "h3", "hr"):
                break
            text = element.get_text("\n", strip=True) if hasattr(element, "get_text") else str(element).strip()
            if text:
                fragments.append(text)
        metadata = "\n".join(fragments)
        posts.append((target, (anchor.get_text(" ", strip=True),
                               vernon_display_date(metadata), vernon_speaker(metadata))))
    following = [numbered_page(link, archive, paged_query=True)
                 for _, link in same_site_links(soup, page, archive)]
    return posts, [url for url in following if url]


def vernon_detail(soup, url, metadata):
    title, displayed, speaker = metadata
    text = soup.get_text("\n", strip=True)
    displayed = displayed or vernon_display_date(text)
    speaker = speaker or vernon_speaker(text)
    match = re.match(r"^(\d{1,2}-\d{1,2}-\d{2,4})\b", title)
    title_date = parse_date(match[1], ("%m-%d-%Y", "%m-%d-%y")) if match else ""
    date = title_date or displayed
    notes = ["Church website identifies it as Vernon Church of Christ",
             f"source post: {url}", "duration not collected"]
    if displayed:
        notes.append(f"displayed entry date: {displayed}")
    if date and displayed != date:
        notes.append("title date used instead of displayed entry date")
    return post_records(url, title, date, "title date" if title_date else "displayed entry date",
                        speaker, audio_references(soup, url), notes,
                        "no direct audio detected; review required")


def collect_vernon(archive_url, *, fetch=None, workers=4):
    return collect_posts(archive_url, vernon_archive, vernon_detail, fetch=fetch, workers=workers)


PICKENS_DATES = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d")


def pickens_archive(soup, page, archive):
    posts = []
    for card in soup.select(".wpfc-sermon"):
        anchor = card.select_one(".wpfc-sermon-title a[href], a.wpfc-sermon-title-text[href]")
        if not anchor:
            continue
        target = public_url(page, anchor["href"])
        parts = urlsplit(target)
        if parts.hostname != urlsplit(archive).hostname or not parts.path.startswith("/sermons/"):
            continue
        title = anchor.get_text(" ", strip=True)
        date = parse_date(selected_text(card, ".wpfc-sermon-meta-date"), PICKENS_DATES)
        posts.append((target, (title, date or parse_date(title, PICKENS_DATES))))
    following = [numbered_page(link, archive) for _, link in same_site_links(soup, page, archive)]
    return posts, [url for url in following if url]


def pickens_detail(soup, url, metadata):
    article = soup.select_one(".wpfc-sermon-single") or soup.find("article") or soup.find("main") or soup
    heading = article.select_one(".wpfc-sermon-single-title")
    title = heading.get_text(" ", strip=True) if heading else metadata[0]
    date = (metadata[1] or parse_date(title, PICKENS_DATES)
            or parse_date(selected_text(article, ".wpfc-sermon-single-meta-date"), PICKENS_DATES))
    speaker = selected_text(article, ".wpfc-sermon-single-meta-preacher a, "
                            ".wpfc-sermon-single-meta-speaker a, "
                            'a[href*="/preachers/"], a[href*="/speakers/"]')
    return post_records(url, title, date, "displayed lesson metadata", speaker,
                        audio_references(article, url),
                        ["Sunday School Online lesson", f"source post: {url}",
                         "date from displayed lesson metadata", "duration not collected"],
                        "no direct audio detected; review required")


def collect_pickens(archive_url, *, fetch=None, workers=4):
    return collect_posts(archive_url, pickens_archive, pickens_detail,
                         fetch=fetch, retry=True, workers=workers)
