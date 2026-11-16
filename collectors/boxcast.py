"""Metadata collector for the boxcast archive."""

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import requests

from .records import format_collection

def collect_grace_montgomery(source_page, place_id, church_name, *, session, recover_pagination=False, channel_id="fbexcfs5ufi8aldoipyb",
                            date_timezone="America/Chicago"):
    api_base = "https://api.boxcast.com"

    def get_json(url, params=None):
        time.sleep(0.4)
        response = session.get(
            url,
            params=params,
            timeout=45,
        )
        return response.json()

    def parse_datetime(value):
        if not value:
            return None

        try:
            result = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )

            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)

            return result

        except ValueError:
            return None

    broadcasts = {}
    page = 1
    repeated_page = False

    while True:

        items = get_json(
            f"{api_base}/channels/{channel_id}/broadcasts",
            params={
                "p": page,
                "l": 20,
                "s": "-starts_at",
            },
        )

        if not isinstance(items, list):
            raise ValueError(
                "Unexpected BoxCast response. No CSV saved."
            )

        if not items:
            break

        new_items = 0

        for item in items:
            broadcast_id = item.get("id")

            if broadcast_id and broadcast_id not in broadcasts:
                broadcasts[broadcast_id] = item
                new_items += 1

        if new_items == 0:
            repeated_page = True
            break

        page += 1

    recovery_complete = False
    recovery_error = ""
    if repeated_page and recover_pagination:
        try:
            offset, seen_offsets = 0, set()
            while offset not in seen_offsets:
                seen_offsets.add(offset)
                time.sleep(0.4)
                reply = session.get(f"{api_base}/channels/{channel_id}/broadcasts",
                                    params={"l": 100, "p": offset, "s": "-starts_at"}, timeout=45)
                heading = reply.headers.get("x-pagination")
                if not heading:
                    raise ValueError("Missing BoxCast pagination header")
                paging = json.loads(heading)
                items = reply.json()
                if not isinstance(items, list):
                    raise ValueError("Unexpected BoxCast recovery payload")
                for item in items:
                    if item.get("id"):
                        broadcasts.setdefault(item["id"], item)
                offset = paging.get("next")
                if offset is None:
                    recovery_complete = True
                    break
            if not recovery_complete:
                recovery_error = "Header pagination repeated"
        except (requests.RequestException, ValueError) as error:
            recovery_error = f"Header-based pagination failed ({type(error).__name__})"

    if not broadcasts:
        raise ValueError("No broadcasts found. No CSV saved.")

    rows = []
    now = datetime.now(timezone.utc)

    for number, (broadcast_id, item) in enumerate(
        broadcasts.items(),
        start=1,
    ):
        starts_at = parse_datetime(item.get("starts_at"))

        # Exclude future scheduled broadcasts.
        if starts_at and starts_at > now:
            continue

        notes = [
            f"source page: {source_page}",
            f"BoxCast broadcast ID: {broadcast_id}",
            "full broadcast; may include worship and announcements",
            "video URL may require refreshing through BoxCast",
        ]

        if repeated_page and not recovery_complete:
            notes.append(
                "pagination returned a repeated page; "
                "full archive coverage unverified"
            )

        video_url = ""

        try:
            view = get_json(
                f"{api_base}/broadcasts/{broadcast_id}/view",
                params={
                    "channel_id": channel_id,
                    "host": urlsplit(source_page).hostname,
                },
            )

            if not isinstance(view, dict):
                raise ValueError(
                    "Unexpected playback response"
                )

            status = str(view.get("status") or "")

            if status in ("live", "upcoming"):
                continue

            if status == "recorded":
                video_url = view.get("playlist") or ""

            notes.append(
                f"BoxCast playback status: {status}"
            )

        except (
            requests.RequestException,
            ValueError,
        ) as error:
            notes.append(
                f"playback lookup failed: "
                f"{type(error).__name__}"
            )

        title = item.get("name") or ""
        speaker = ""

        # Extract explicitly named speakers from the title.
        match = re.search(
            r"\|\s*(?:Pastor|Rev\.?|Reverend)\s+(.+)$",
            title,
            flags=re.IGNORECASE,
        )

        if match:
            speaker = match.group(1).strip()

        sermon_date = ""

        if starts_at:
            sermon_date = starts_at.astimezone(
                ZoneInfo(date_timezone)
            ).date().isoformat()

            notes.append(
                "date is broadcast start date "
                f"in {date_timezone}"
            )

        if not video_url:
            notes.append(
                "no playable video link retrieved; "
                "review required"
            )

        rows.append({
            "source_id": broadcast_id,
            "date_basis": f"BoxCast start date ({date_timezone})" if sermon_date else "",
            "place_id": place_id,
            "church_name": church_name,
            "sermon_title": title,
            "speaker": speaker,
            "sermon_date": sermon_date,
            "audio_url": "",
            "duration_sec": "",
            "data_note": "; ".join(notes),
            "video_url": video_url,
        })

    if not rows:
        raise ValueError(
            "No past broadcast entries found. No CSV saved."
        )

    return format_collection(rows, source_page, coverage=[{"broadcasts_found": len(broadcasts), "pagination_repeated": repeated_page,
                                        "header_pagination_completed": recovery_complete}],
                             limitations=(["Archive pagination repeated; completeness unverified"] if repeated_page and not recovery_complete else []) +
                             ([recovery_error] if recovery_error else []) + (["Some playback metadata could not be resolved"] if any(not r["video_url"] for r in rows) else []))
