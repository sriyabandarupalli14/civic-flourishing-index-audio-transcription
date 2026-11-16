"""Metadata collector for the sermoncloud archive."""

import time
from datetime import datetime
from html import unescape

from .records import format_collection

def collect_covenant_life(source_page, place_id, church_name, *, session, account="covenant-life-ministries"):
    endpoint = (
        "https://api.sermoncloud.com/"
        f"{account}/sermons"
    )

    records = {}
    page = 1
    expected_total = None

    while True:
        time.sleep(0.4)

        response = session.get(
            endpoint,
            params={"howmany": 100, "page": page},
            timeout=60,
        )
        payload = response.json()

        items = payload.get("data") or []
        expected_total = int(payload["total"])
        last_page = int(payload["last_page"])

        new_count = 0

        for item in items:
            sermon_id = str(item.get("id") or "")

            if not sermon_id:
                raise ValueError(
                    "Sermon without an ID. No CSV saved."
                )

            if sermon_id in records:
                continue

            new_count += 1
            media = item.get("media") or {}
            date_info = item.get("date") or {}

            sermon_date = ""

            if isinstance(date_info, dict):
                displayed_date = date_info.get("date") or ""

                for fmt in ("%b %d, %Y", "%B %d, %Y"):
                    try:
                        sermon_date = datetime.strptime(
                            displayed_date, fmt
                        ).date().isoformat()
                        break
                    except ValueError:
                        pass

            audio_url = media.get("audio") or ""
            video_url = media.get("video") or ""

            speaker = " ".join(
                str(item.get("preacher") or "").split()
            )

            notes = [
                f"source page: {source_page}",
                f"SermonCloud sermon ID: {sermon_id}",
                "date taken from displayed sermon metadata",
                "duration not collected",
                "media URLs retained as supplied by SermonCloud",
            ]

            if not audio_url and not video_url:
                notes.append(
                    "no audio or video URL supplied; review required"
                )

            records[sermon_id] = {
                "source_id": sermon_id,
            "date_basis": "displayed sermon metadata date" if sermon_date else "",
            "place_id": place_id,
                "church_name": church_name,
                "sermon_title": unescape(
                    str(item.get("title") or "")
                ).strip(),
                "speaker": speaker,
                "sermon_date": sermon_date,
                "audio_url": audio_url,
                "duration_sec": "",
                "data_note": "; ".join(notes),
                "video_url": video_url,
            }

        if page >= last_page:
            break

        if new_count == 0:
            raise ValueError(
                "Pagination stopped returning new sermons. "
                "No CSV saved."
            )

        page += 1

    if not records:
        raise ValueError("No sermons collected. No CSV saved.")

    if len(records) != expected_total:
        raise ValueError(
            f"Expected {expected_total} sermons but collected "
            f"{len(records)}. No CSV saved."
        )

    return format_collection(records.values(), source_page, coverage=[{"advertised_total": expected_total, "archive_pages": page}],
                             limitations=[])
