"""Offline replay routes for church entries 21–44."""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from . import adventist, sermonnet
from .archive_cache import FOLLOWUP, PASS, SPRINT, MissingCapture
from .archive_extractors import (
    drive_media, flat_youtube, html_references, jwplayer_references,
    observation, printed_date, rss_enclosures, wordpress_attachments,
)
from .shared import is_media, public_url, selected_text


# A route description is independent of the count or status in the saved ledger.
ROUTES = {
    21: "IBM Video recording and embedded-player discovery",
    22: "Official YouTube streams and videos; bounded initial tab, full follow-up and recent tab metadata",
    23: "Static download cards, RSS, search partitions and filter results",
    24: "Sermon.net posting collections and RSS; public and embedded Google Drive listings",
    25: "Subsplash SnapPages listing plus individual detail responses and all media variants",
    26: "Official YouTube channel streams and videos; absent Shorts tab",
    27: "Tithely dated catalog links and individual sermon pages",
    28: "WordPress media-text sermon cards, including cards without a media link",
    29: "Tithely dated catalog links and individual sermon pages",
    30: "WordPress sermon-page embeds plus REST audio and video attachments",
    31: "Site embeds and client inspection; unrelated YouTube channel evidence held outside collection",
    32: "Candidate YouTube tabs and Vimeo broadcaster discovery; associations require review",
    33: "Original detail pages, public historical index and candidate replacement source",
    34: "Series Engine unfiltered catalog offsets and message-detail pages",
    35: "Static lesson details discovered through listings, pagination, sitemap and podcast routes",
    36: "JWPlayer HLS manifests from visible HTML and decoded ASP.NET ViewState",
    37: "Church audio cards and unresolved public Google Drive folder listings",
    38: "Linked static sermon posts and direct/download reference variants",
    39: "Public historical index and saved sermon-page snapshots; current domain evidence",
    40: "Adventist catalog pagination and detail pages; shared with master entry #11",
    41: "Subsplash SnapPages listing, detail responses and all variants including metadata-only items",
    42: "Legacy static audio listing and linked archive and gospel-meeting pages",
    43: "Official YouTube stream tab and recent checks",
    44: "Adventist catalog pagination and detail pages; shared with master entry #2",
}


@dataclass
class Replay:
    rows: list[dict] = field(default_factory=list)
    checked: int = 0
    missing: int = 0
    modes: list[str] = field(default_factory=list)

    def capture(self, cache, url):
        try:
            result = cache.fetch(url)
        except MissingCapture:
            self.missing += 1
            return None
        self.checked += 1
        return result

    def payload(self, cache, batch, church, filename):
        result = cache.data(batch, church.place_id, filename)
        if result is None:
            self.missing += 1
        else:
            self.checked += 1
        return result


def _captured_urls(cache, church, *, targeted=False):
    if targeted:
        proof = cache.data(PASS, church.place_id, "targeted_outcome.json", {}) or {}
        rows = proof.get("pages") or []
    else:
        rows = cache.rows(PASS, church.place_id, "pages.csv")
    return list(dict.fromkeys(
        item["requested_url"] for item in rows
        if str(item.get("status")) == "200" and item.get("requested_url")
    ))


def replay_youtube(cache, church, output):
    sources = {
        22: ((FOLLOWUP, "youtube_streams.json"), (FOLLOWUP, "youtube_videos.json"),
             (SPRINT, "youtube_recent_streams_payload.json"), (SPRINT, "youtube_recent_videos_payload.json")),
        26: ((SPRINT, "youtube_sprint_streams.json"), (SPRINT, "youtube_sprint_videos.json")),
        31: ((SPRINT, "youtube_sprint_streams.json"), (SPRINT, "youtube_sprint_videos.json"),
             (SPRINT, "youtube_sprint_shorts.json")),
        32: ((SPRINT, "youtube_sprint_videos.json"),),
        43: ((FOLLOWUP, "youtube_streams.json"), (SPRINT, "youtube_recent_streams_payload.json"),
             (SPRINT, "youtube_recent_videos_payload.json")),
    }
    output.modes.append("YouTube flat channel-tab parser")
    for batch, file in sources[church.number]:
        payload = output.payload(cache, batch, church, file)
        if not isinstance(payload, dict):
            continue
        for row in flat_youtube(payload):
            row["cache_route"] = file
            if church.number == 31:
                row["scope_disposition"] = "excluded_unrelated_channel"
            elif church.number == 32:
                row["scope_disposition"] = "identity_review"
            output.rows.append(row)


def replay_monroeville(cache, church, output):
    configuration = output.payload(cache, FOLLOWUP, church, "sermonnet_config.json")
    if configuration:
        output.modes.append("Stage 1 Sermon.net adapter and cached API")
        def fetch(url):
            if url == church.archive_url:
                return url, "var StudioAppData = " + json.dumps(configuration)
            cached = output.capture(cache, url)
            if cached is None:
                raise MissingCapture("Sermon.net API capture missing")
            return cached

        result = sermonnet.collect_sermonnet(church.archive_url, timezone="America/Chicago", fetch=fetch)
        for record in result.sermons.values():
            for media in record.media_urls or [record.sermon_url]:
                output.rows.append(observation(record.sermon_url, media, record.title,
                                               record.date, record.speaker, platform_id=record.source_id))
    feeds = output.payload(cache, FOLLOWUP, church, "sermonnet_feed_counts.json") or []
    for item in feeds:
        url = item["url"]
        captured = output.capture(cache, url)
        if captured:
            output.rows.extend(rss_enclosures(captured[1], url, item["playlist_id"]))
    if feeds:
        output.modes.append("Sermon.net RSS enclosure replay")
    output.modes.append("Decoded Google Drive folder listing parser")
    folder = cache.root / FOLLOWUP / church.place_id
    for path in sorted(folder.glob("drive_*.json")):
        value = output.payload(cache, FOLLOWUP, church, path.name)
        if isinstance(value, list) and value:
            folder_url = "https://drive.google.com/drive/folders/" + path.stem.removeprefix("drive_")
            output.rows.extend(drive_media(value[0], folder_url))

    embedded = output.payload(cache, SPRINT, church, "drive_embedded_proof.json") or []
    for entry in embedded:
        url = entry["request"].get("requested_url")
        cached = output.capture(cache, url) if url else None
        if cached is None:
            continue
        soup = BeautifulSoup(cached[1], "html.parser")
        for link in soup.select("a[href]"):
            title = link.get_text(" ", strip=True)
            target = public_url(url, link["href"])
            match = re.search(r"/file/d/([^/]+)", target)
            if match and is_media(title):
                output.rows.append(observation(url, target, title, platform_id=match[1]))
    if embedded:
        output.modes.append("Google Drive embedded folder HTML")


def replay_subsplash(cache, church, output):
    """Use the same listing/detail shape as Stage 1 and emit all observed media variants."""
    from .subsplash import collect_amazing_grace

    output.modes.append("Subsplash listing and all-variant detail replay")
    folder = cache.root / PASS / church.place_id
    listing_paths = sorted(folder.glob("subsplash_*.json"), key=lambda path: int(path.stem.split("_")[-1]))
    origin = urlsplit(church.archive_url)
    base = f"{origin.scheme}://{origin.netloc}"
    for path in listing_paths:
        payload = output.payload(cache, PASS, church, path.name) or {}
        for item in payload.get("_embedded", {}).get("media-items", []):
            ident = item.get("id")
            if not ident:
                continue
            detail = output.capture(cache, f"https://feeds.subsplash.com/api/v1/media-detail/{ident}")
            if detail is None:
                continue
            variants = json.loads(detail[1]).get("media") or [{}]
            source = f"{base}/media/{item.get('short_code', '')}/{item.get('slug', '')}"
            for variant in variants:
                if variant.get("url") or len(variants) == 1:
                    row = observation(source, variant.get("url") or "", item.get("title") or "",
                                      item.get("date") or "", item.get("speaker") or "",
                                      platform_id=ident, date_meaning="platform media date")
                    row["platform_app_key"] = item.get("app_key") or ""
                    output.rows.append(row)
    # Shared collector compatibility: only run its preferred-variant projection
    # when the original application identifier is uniform across the capture.
    identifiers = {row["platform_app_key"] for row in output.rows}
    if len(identifiers) == 1 and all(row["platform_recording_id"] for row in output.rows):
        class Session:
            def post(self, url, *, data, **kwargs):
                return Payload(json.loads(listing_paths[data["page"] - 1].read_text()))
            def get(self, url, **kwargs):
                return Payload(json.loads(cache.fetch(url)[1]))
        class Payload:
            def __init__(self, value):
                self.value = value
            def json(self):
                return self.value

        try:
            result = collect_amazing_grace(church.archive_url, church.place_id, church.name,
                                           session=Session(), app_id=next(iter(identifiers)),
                                           pause=lambda _: None)
            output.modes.append(f"Stage 1 preferred-variant adapter: {len(result.sermons)} items")
        except (ValueError, IndexError, KeyError):
            output.modes.append("Stage 1 projection could not replay every listing/detail")


def _detail_urls(cache, church):
    n = church.number
    if n in {27, 29}:
        return [u for u in _captured_urls(cache, church, targeted=True)
                if re.match(r"/media/\d{4}-\d\d-\d\d-", urlsplit(u).path)]
    if n == 34:
        return [u for u in _captured_urls(cache, church, targeted=True) if "enmse_av=1" in u]
    if n == 35:
        traversal = cache.data(FOLLOWUP, church.place_id, "market_traversal.json", {}) or {}
        return list(dict.fromkeys([*_captured_urls(cache, church), *traversal.get("detail_urls", [])]))
    if n == 36:
        return [u for u in _captured_urls(cache, church) if "/video.aspx?" in u]
    if n in {40, 44}:
        return [u for u in _captured_urls(cache, church, targeted=True)
                if re.fullmatch(r"/online-sermons/[^/]+/?", urlsplit(u).path)]
    if n in {38, 42}:
        return _captured_urls(cache, church)
    return [church.archive_url]


def _static_rows(soup, url, number):
    if number == 23:
        for card in soup.select(".widget-card__content"):
            anchor = card.select_one('a[href*=".mp3"]')
            if anchor:
                yield observation(url, public_url(url, anchor["href"]),
                                  selected_text(card, ".widget-card__title"),
                                  selected_text(card, ".widget-card__date"),
                                  selected_text(card, ".widget-card__speaker"))
        return
    if number == 28:
        for card in soup.select(".wp-block-media-text__content"):
            title = selected_text(card, "h1,h2,h3,h4,h5,h6")
            source = card.select_one("audio[src], audio source[src]")
            if title or source:
                media = public_url(url, source["src"]) if source else ""
                yield observation(url, media, title, selected_text(card, "figcaption"),
                                  date_meaning="displayed recording date")
        return
    if number == 36:
        yield from jwplayer_references(soup, url)
        return
    rows = list(html_references(soup, url))
    title = selected_text(soup, "h1")
    date, speaker, meaning = "", "", ""
    if number in {27, 29}:
        date = printed_date(soup.get_text(" ", strip=True))
        speaker = selected_text(soup, '[class*="speaker-name"],.media-speaker')
        meaning = "displayed sermon date"
        if not rows:
            rows = [observation(url)]
    elif number == 34:
        title = selected_text(soup, ".enmse-modern-message-title")
        meta = selected_text(soup, ".enmse-modern-message-meta")
        if " - " in meta:
            speaker, date = meta.rsplit(" - ", 1)
        meaning = "displayed message date"
    elif number == 35:
        title = selected_text(soup, ".d-title") or title
        speaker = selected_text(soup, ".d-speaker,.speaker,.sermon-speaker")
        date = selected_text(soup, ".d-meta span")
    elif number == 38:
        date, meaning = printed_date(title), "date used as sermon page title"
    elif number in {40, 44}:
        title, date, speaker = adventist.extract_metadata(soup, title)
        meaning = "displayed sermon Date field"
        if not rows:
            rows = [observation(url)]
    for row in rows:
        if number in {27, 29, 34, 35, 38, 40, 44}:
            row.update(sermon_title=title or row["sermon_title"], sermon_date=date,
                       speaker=speaker, date_meaning=meaning)
        if number == 42:
            row["sermon_date"] = printed_date(row["sermon_title"])
            row["date_meaning"] = "date printed in media link title" if row["sermon_date"] else ""
        if number == 37:
            node = next((node for node in soup.select("audio[src]")
                         if public_url(url, node["src"]) == row["media_url"]), None)
            if node:
                row.update(sermon_title=node.get("data-track", ""), speaker=node.get("data-artist", ""))
        if number in {21, 31, 33}:
            row["scope_disposition"] = "unresolved_route_candidate"
        yield row


def supplemental_html(cache, church, output):
    """Read route proofs for captures outside the original listing crawl."""
    number = church.number
    if number == 23:
        feed = "https://www.oakgrovepm.com/podcast.cfm?content=download"
        captured = output.capture(cache, feed)
        if captured and "<rss" in captured[1][:300]:
            output.rows.extend(rss_enclosures(captured[1], feed))
            output.modes.append("Oak Grove RSS enclosure replay")
        filtered = output.payload(cache, SPRINT, church, "oak_filters_proof.json",) or {}
        partitions = output.payload(cache, FOLLOWUP, church, "oak_partitions.json") or []
        urls = [row.get("url") for row in filtered.get("requests", [])]
        urls += [row.get("url") for row in partitions]
        for url in dict.fromkeys(u for u in urls if u):
            captured = output.capture(cache, url)
            if captured:
                output.rows.extend(_static_rows(BeautifulSoup(captured[1], "html.parser"), url, number))
        folder = cache.root / FOLLOWUP / church.place_id
        for path in sorted(folder.glob("oak_search_*.html")):
            proof = output.payload(cache, FOLLOWUP, church, path.with_suffix(".json").name) or {}
            if str(proof.get("status")) == "200":
                soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                output.rows.extend(_static_rows(soup, proof["url"], number))
        output.modes.append("Cached Oak Grove filters and form partitions")
    if number in {21, 31, 33, 37}:
        names = {
            21: ("public_route_discovery_proof.json",),
            31: ("public_route_discovery_proof.json",),
            33: ("replacement_site_proof.json", "historical_public_archive_proof.json"),
            37: ("drive_sprint_proof.json",),
        }[number]
        for file in names:
            proof = output.payload(cache, SPRINT, church, file) or {}
            for item in [*proof.get("pages", []), *proof.get("failures", [])]:
                url = item.get("url") or (item.get("request") or item).get("requested_url")
                if not url or url == church.archive_url:
                    continue
                captured = output.capture(cache, url)
                if captured:
                    rows = list(_static_rows(BeautifulSoup(captured[1], "html.parser"), url, number))
                    for row in rows:
                        row["scope_disposition"] = "unresolved_route_candidate"
                    output.rows.extend(rows)
            if proof.get("index_request"):
                request = proof["index_request"]
                if request.get("requested_url"):
                    output.capture(cache, request["requested_url"])
        output.modes.append("Supplemental archive discovery evidence")


def replay_static(cache, church, output):
    output.modes.append(church.platform + " cached HTML parser")
    for url in _detail_urls(cache, church):
        captured = output.capture(cache, url)
        if captured:
            base, html = captured
            if html.lstrip().startswith("<?xml"):
                # Sitemap evidence is used to discover detail URLs upstream;
                # do not interpret the sitemap as a sermon detail document.
                continue
            # The evidence URL remains the one followed by the source-specific collector.
            output.rows.extend(_static_rows(BeautifulSoup(html, "html.parser"), url or base, church.number))
    supplemental_html(cache, church, output)
    if church.number == 30:
        proof = output.payload(cache, SPRINT, church, "wordpress_public_media_proof.json") or {}
        for page in proof.get("pages", []):
            url = page["request"].get("requested_url")
            captured = output.capture(cache, url) if url else None
            if captured:
                output.rows.extend(wordpress_attachments(json.loads(captured[1]), church.archive_url.split("/about-us/")[0]))


def replay_historical(cache, church, output):
    notes = output.payload(cache, SPRINT, church, "historical_expanded_proof.json") or {}
    output.modes.append("saved public historical sermon-page snapshots")
    for item in notes.get("inspected", []):
        url, original = item.get("snapshot"), item.get("original")
        if not url or not original:
            continue
        captured = output.capture(cache, url)
        if captured:
            soup = BeautifulSoup(captured[1], "html.parser")
            for row in html_references(soup, original):
                row["historical_snapshot_url"] = url
                output.rows.append(row)


def run_replay(cache, church):
    result = Replay()
    if church.number in {22, 26, 31, 32, 43}:
        replay_youtube(cache, church, result)
        if church.number == 31:
            replay_static(cache, church, result)
        if church.number == 32:
            proof = result.payload(cache, SPRINT, church, "vimeo_broadcaster_proof.json") or {}
            result.modes.append("Archived Vimeo oEmbed broadcaster evidence")
            for entry in proof.get("requests", []):
                url = entry["request"].get("requested_url")
                if url:
                    result.capture(cache, url)
            seed = proof.get("seed_metadata") or {}
            if seed.get("video_id") and "upper room" in (seed.get("title") or "").casefold():
                url = f"https://vimeo.com/{seed['video_id']}"
                candidate = observation(url, url, seed["title"], platform_id=str(seed["video_id"]))
                candidate["scope_disposition"] = "identity_review"
                result.rows.append(candidate)
    elif church.number == 24:
        replay_monroeville(cache, church, result)
    elif church.number in {25, 41}:
        replay_subsplash(cache, church, result)
    elif church.number == 39:
        replay_historical(cache, church, result)
    else:
        replay_static(cache, church, result)
    return result
