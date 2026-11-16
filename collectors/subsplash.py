"""Metadata collector for the subsplash archive."""

import time
from datetime import datetime
from urllib.parse import urlsplit

from .records import format_collection

def collect_amazing_grace(archive_url, place_id, church_name, *, session, website_id="42076", app_id="CRCXMM"):
    origin = urlsplit(archive_url)
    base = f"{origin.scheme}://{origin.netloc}"

    # These POST requests only read the public media listing.

    items_by_id = {}
    page = 1
    expected_total = None

    while True:
        time.sleep(0.4)

        response = session.post(
            f"{base}/controllers/subsplash",
            data={
                "action": "getMediaItems",
                "website_id": website_id,
                "method": "recent",
                "value": "default",
                "page": page,
                "limit": 12,
            },
            timeout=45,
        )
        payload = response.json()

        expected_total = int(payload["total"])
        items = payload.get("_embedded", {}).get("media-items", [])

        if not items:
            if len(items_by_id) < expected_total:
                raise ValueError(
                    "Archive ended before all advertised items were "
                    "collected. No CSV saved."
                )
            break

        new_count = 0

        for item in items:
            media_id = item.get("id")

            if not media_id:
                raise ValueError("Media item has no ID. No CSV saved.")

            if item.get("app_key") != app_id:
                raise ValueError(
                    "Unexpected church media source. No CSV saved."
                )

            if media_id not in items_by_id:
                items_by_id[media_id] = item
                new_count += 1

        if len(items_by_id) >= expected_total:
            break

        if new_count == 0:
            raise ValueError(
                "Pagination repeated before reaching the archive total. "
                "No CSV saved."
            )

        page += 1

    rows = []

    for number, (media_id, item) in enumerate(
        items_by_id.items(), start=1
    ):
        time.sleep(0.4)

        response = session.get(
            f"https://feeds.subsplash.com/api/v1/media-detail/{media_id}",
            timeout=45,
        )
        detail = response.json()

        recordings = detail.get("media") or []

        audio_url = next(
            (
                recording["url"]
                for recording in recordings
                if recording.get("format", "").lower()
                in ("mp3", "m4a", "ogg", "wav")
                and recording.get("url")
            ),
            "",
        )

        video_url = next(
            (
                recording["url"]
                for recording in recordings
                if recording.get("format", "").lower() == "mp4"
                and recording.get("url")
            ),
            "",
        )

        if not video_url:
            video_url = next(
                (
                    recording["url"]
                    for recording in recordings
                    if recording.get("format", "").lower() == "m3u8"
                    and recording.get("url")
                ),
                "",
            )

        sermon_date = ""
        date_value = item.get("date") or detail.get("date") or ""

        if date_value:
            try:
                sermon_date = datetime.fromisoformat(
                    date_value.replace("Z", "+00:00")
                ).date().isoformat()
            except ValueError:
                pass

        short_code = item.get("short_code") or detail.get("sapid") or ""
        slug = item.get("slug") or ""

        source_url = (
            f"{base}/media/{short_code}/{slug}"
            if short_code and slug
            else f"{base}/recent-media"
        )

        notes = [
            f"source post: {source_url}",
            f"Subsplash media ID: {media_id}",
            "deduplicated by media ID",
            "date taken from media metadata",
            "duration not collected",
        ]

        if not audio_url:
            notes.append("no separate audio URL supplied")

        if not audio_url and not video_url:
            notes.append("no recording URL found; review required")

        rows.append({
            "source_id": media_id,
            "date_basis": "Subsplash media date" if sermon_date else "",
            "place_id": place_id,
            "church_name": church_name,
            "sermon_title": (
                item.get("title") or detail.get("title") or ""
            ),
            "speaker": item.get("speaker") or "",
            "sermon_date": sermon_date,
            "audio_url": audio_url,
            "duration_sec": "",
            "data_note": "; ".join(notes),
            "video_url": video_url,
        })

    if not rows:
        raise ValueError("No media items collected. No CSV saved.")

    return format_collection(rows, archive_url, coverage=[{"advertised_total": expected_total, "archive_pages": page}],
                             limitations=[])
