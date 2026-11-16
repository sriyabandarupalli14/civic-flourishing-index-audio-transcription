"""Metadata collector for the nextjs archive."""

import json, re, time
from datetime import datetime
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup

from .records import format_collection

def collect_highlands(archive_url, place_id, church_name, *, session, page_limit=None):
    origin = urlsplit(archive_url)
    base = f"{origin.scheme}://{origin.netloc}"
    hostname = urlsplit(base).hostname

    decoder = json.JSONDecoder()

    def walk(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)

        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    def extract_data(soup):
        """Decode embedded Next.js data."""
        chunks = []

        for script in soup.find_all("script", src=False):
            text = script.get_text()

            for match in re.finditer(r"self\.__next_f\.push\(", text):
                try:
                    payload, _ = decoder.raw_decode(
                        text[match.end():].lstrip()
                    )
                except ValueError:
                    continue

                if (
                    isinstance(payload, list)
                    and len(payload) > 1
                    and payload[0] == 1
                    and isinstance(payload[1], str)
                ):
                    chunks.append(payload[1])

        decoded = "".join(chunks)
        objects = []
        position = 0

        while position < len(decoded):
            match = re.search(r"[\{\[]", decoded[position:])

            if not match:
                break

            start = position + match.start()

            try:
                value, end = decoder.raw_decode(decoded, start)
                objects.extend(walk(value))
                position = end
            except ValueError:
                position = start + 1

        return objects, decoded

    def duration_seconds(value):
        """Convert seconds, MM:SS, or HH:MM:SS to seconds."""
        if value is None or value == "":
            return ""

        if isinstance(value, (int, float)):
            return int(value)

        try:
            parts = [int(part) for part in str(value).split(":")]

            if len(parts) in (2, 3):
                total = 0
                for part in parts:
                    total = total * 60 + part
                return total

        except (ValueError, TypeError):
            pass

        return ""

    def media_page_url(address, source_url):
        """Accept website media pages, excluding files and asset paths."""
        if not isinstance(address, str) or not address.strip():
            return None

        target = urljoin(source_url, address.strip())
        parts = urlsplit(target)

        if parts.scheme not in ("http", "https"):
            return None

        if parts.hostname != hostname:
            return None

        path = parts.path.rstrip("/")

        if path != "/media" and not path.startswith("/media/"):
            return None

        # Exclude file URLs, JavaScript chunks, and recording paths.
        segments = path.split("/")[2:]

        if any("." in segment for segment in segments):
            return None

        if segments and segments[0].startswith("page-"):
            return None

        if any(
            re.fullmatch(
                r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                segment,
                flags=re.IGNORECASE,
            )
            for segment in segments
        ):
            return None

        # Preserve query strings in case the archive uses pagination.
        return base + path + (
            f"?{parts.query}" if parts.query else ""
        )

    pending = [
        archive_url,
        f"{base}/media/series",
        f"{base}/media/first-wednesday",
    ]

    queued = set(pending)
    visited = set()
    skipped = []
    messages = {}
    successful_pages = 0

    while pending and (page_limit is None or len(visited) < page_limit):
        page_url = pending.pop(0)

        if page_url in visited:
            continue

        visited.add(page_url)
        time.sleep(0.5)

        try:
            response = session.get(page_url, timeout=45)

            if urlsplit(response.url).path.rstrip("/") == "/404":
                raise ValueError("Redirected to the site's 404 page")

        except (requests.RequestException, ValueError) as error:
            skipped.append(page_url)
            continue

        successful_pages += 1
        soup = BeautifulSoup(response.text, "html.parser")
        objects, decoded = extract_data(soup)

        for item in objects:
            recordings = item.get("urls")

            if not item.get("title") or not isinstance(recordings, list):
                continue

            # Select message recordings, excluding worship recordings.
            message_recordings = [
                recording
                for recording in recordings
                if isinstance(recording, dict)
                and str(recording.get("type", "")).lower() == "message"
            ]

            if not message_recordings:
                continue

            slug = str(item.get("slug") or "")
            sermon_date = ""

            for date_format in ("%m-%d-%Y", "%Y-%m-%d"):
                try:
                    sermon_date = datetime.strptime(
                        slug, date_format
                    ).date().isoformat()
                    break
                except ValueError:
                    pass

            speaker = item.get("speaker") or ""

            if not isinstance(speaker, str):
                speaker = ""

            for recording in message_recordings:
                audio_url = (
                    recording.get("mp3_url")
                    or recording.get("m4a_url")
                    or ""
                )

                key = (
                    slug,
                    item["title"],
                    recording.get("id") or audio_url,
                )

                messages[key] = {
                    "date_basis": "date from message slug" if sermon_date else "",
                    "place_id": place_id,
                    "church_name": church_name,
                    "sermon_title": item["title"],
                    "speaker": speaker,
                    "sermon_date": sermon_date,
                    "audio_url": audio_url,
                    "duration_sec": duration_seconds(
                        recording.get("length")
                    ),
                    "data_note": (
                        "Shared Highlands archive; not verified as "
                        "Shoals-specific; message audio only; "
                        f"source page: {page_url}"
                    ),
                }

        # Collect actual HTML links.
        links = [
            link["href"]
            for link in soup.select("a[href]")
        ]

        # Decode complete strings instead of matching partial URL paths.
        for match in re.finditer(r'"(?:[^"\\]|\\.)*"', decoded):
            try:
                value = json.loads(match.group())
            except ValueError:
                continue

            if isinstance(value, str) and (
                value.startswith("/media/")
                or value.startswith(base + "/media/")
            ):
                links.append(value)

        # Follow individual message pages identified by embedded records.
        for item in objects:
            slug = item.get("slug")

            if (
                isinstance(slug, str)
                and isinstance(item.get("urls"), list)
                and re.fullmatch(r"[A-Za-z0-9_-]+", slug)
            ):
                links.append(f"/media/{slug}")

        for address in links:
            target = media_page_url(address, response.url)

            if target and target not in queued:
                pending.append(target)
                queued.add(target)

    if not messages:
        raise ValueError(
            "No message recordings extracted. No CSV saved."
        )

    coverage_note = (
        "Collected from discovered media pages; "
        "full historical archive coverage unverified; "
        f"skipped pages: {len(skipped)}"
    )

    for row in messages.values():
        row["data_note"] += f"; {coverage_note}"

    return format_collection(messages.values(), archive_url, coverage=[{"pages_attempted": len(visited), "pages_read": successful_pages, "pages_remaining": len(pending)}],
                             limitations=["Shoals campus attribution and historical completeness are unverified"] + ([f"{len(skipped)} archive pages skipped"] if skipped else []) + ([f"Verification limit reached with {len(pending)} pages still queued"] if pending else []))
