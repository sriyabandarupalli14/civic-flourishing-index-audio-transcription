"""Metadata collector for the static audio archive."""

import re
from urllib.parse import urljoin, urlsplit, unquote
from bs4 import BeautifulSoup

from .records import format_collection

def collect_redeeming_grace(page_url, place_id, church_name, *, session):
    """Collect sermon metadata and MP3 links from the sermons page."""

    response = session.get(
        page_url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )

    soup = BeautifulSoup(response.text, "html.parser")
    rows = []
    seen_urls = set()

    for link in soup.select("a[href]"):
        audio_url = urljoin(response.url, link["href"].strip())
        audio_path = urlsplit(audio_url).path

        # Exclude files that aren't MP3s.
        if not audio_path.lower().endswith(".mp3"):
            continue

        if audio_url in seen_urls:
            continue
        seen_urls.add(audio_url)

        filename = unquote(audio_path.rsplit("/", 1)[-1])
        filename_title = re.sub(
            r"\.mp3$", "", filename, flags=re.IGNORECASE
        )

        # Example: Message by Dr. Jeff R. Steele_dont_quit.mp3
        match = re.match(
            r"^message\s+by\s+([^_]+)_(.+)$",
            link.parent.get_text(" ", strip=True),
            flags=re.IGNORECASE,
        )

        speaker = ""
        title = filename_title

        if match:
            speaker = match.group(1).strip()
            title = filename_title

        title = re.sub(r"_+", " ", title)
        title = re.sub(r"\s+", " ", title).strip()

        rows.append({
            "place_id": place_id,
            "church_name": church_name,
            "sermon_title": title,
            "speaker": speaker,
            "sermon_date": "",
            "audio_url": audio_url,
            "duration_sec": "",
            "data_note": (
                "Title derived from MP3 filename; speaker parsed from nearby link text; "
                "sermon date and duration unavailable in listing"
            ),
        })

    if not rows:
        raise ValueError("No MP3 download links found. Inspect the page.")

    return format_collection(rows, page_url, coverage=[{"archive_pages": 1}],
                             limitations=[])
