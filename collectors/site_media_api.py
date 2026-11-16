"""Metadata collector for the site media api archive."""

import ast, json, re
from datetime import datetime
from html import unescape
from urllib.parse import quote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from .records import format_collection

def collect_victory_baptist(source_url, place_id, church_name, *, session, directory_id="171", date_timezone="America/Chicago"):
    endpoint = urljoin(source_url, "/inc/xmlxfer")

    response = session.get(
        endpoint,
        params={
            "rtype": "lib.media",
            "cid": directory_id,
            "cmd": "getlist",
            "sort": "2",
            "tag": "",
        },
        timeout=60,
    )

    # The site's response uses single-quoted strings.
    # literal_eval parses these safely without executing JavaScript.
    try:
        payload = json.loads(response.text)
    except ValueError:
        payload = ast.literal_eval(response.text.strip())

    tracks = payload.get("list") or []
    expected_total = int(payload["total"])

    unique_tracks = {}

    for track in tracks:
        if not isinstance(track, dict):
            continue

        media_id = str(track.get("IDENTIFIER") or "")

        if not media_id:
            raise ValueError("Record without a media ID. No CSV saved.")

        unique_tracks.setdefault(media_id, track)

    if len(unique_tracks) != expected_total:
        raise ValueError(
            f"Expected {expected_total} records, found "
            f"{len(unique_tracks)} unique records. No CSV saved."
        )

    # Ask the player for one real URL to establish the audio directory.
    first_id, _ = next(
        (
            (media_id, track)
            for media_id, track in unique_tracks.items()
            if urlsplit(str(track.get("T1") or "")).path.lower().endswith(".mp3")
        ),
        (None, None),
    )

    if first_id is None:
        raise ValueError("No MP3 records found. No CSV saved.")

    response = session.get(
        endpoint,
        params={
            "rtype": "lib.media",
            "cid": directory_id,
            "mid": first_id,
            "cmd": "geturl",
            "idx": "0",
        },
        timeout=45,
    )

    match = re.search(r"play\.url:([^;]+)", response.text)

    if not match:
        raise ValueError("Could not resolve the audio directory.")

    first_url = urljoin(source_url, unescape(match.group(1).strip()))
    media_directory = first_url.rsplit("/", 1)[0] + "/"

    if not urlsplit(media_directory).path.endswith(f"/pdir/{directory_id}/"):
        raise ValueError("Unexpected media directory. No CSV saved.")

    rows = []

    for media_id, track in unique_tracks.items():
        title = unescape(str(track.get("TITLE") or "")).strip()
        filename = str(track.get("T1") or "").strip()
        notes = [
            f"source page: {source_url}",
            f"media ID: {media_id}",
        ]

        speaker = ""
        if " - " in title:
            speaker = title.rsplit(" - ", 1)[1].strip()

        audio_url = ""

        if urlsplit(filename).path.lower().endswith(".mp3"):
            if filename.startswith(("http://", "https://", "//")):
                audio_url = urljoin(source_url, filename)
            else:
                audio_url = media_directory + quote(filename, safe="/")

            notes.append(
                "audio URL uses filename and player-resolved directory"
            )
        else:
            notes.append("no MP3 filename supplied; review required")

        sermon_date = ""

        try:
            timestamp = float(track["DATE"]) / 1000
            sermon_date = datetime.fromtimestamp(
                timestamp,
                tz=ZoneInfo(date_timezone),
            ).date().isoformat()

            notes.append(
                f"date from listing timestamp in {date_timezone}"
            )
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            notes.append("listing date unavailable or unparsed")

        duration_sec = ""

        try:
            duration_ms = float(track.get("DURATION") or "")
            if duration_ms > 0:
                duration_sec = round(duration_ms / 1000)
                notes.append(
                    "duration converted from source milliseconds"
                )
        except (ValueError, TypeError):
            pass

        scripture = unescape(str(track.get("DESC1") or "")).strip()

        if scripture:
            notes.append(f"listing description: {scripture}")

        rows.append({
            "source_id": media_id,
            "date_basis": f"API listing date ({date_timezone})" if sermon_date else "",
            "place_id": place_id,
            "church_name": church_name,
            "sermon_title": title,
            "speaker": speaker,
            "sermon_date": sermon_date,
            "audio_url": audio_url,
            "duration_sec": duration_sec,
            "data_note": "; ".join(notes),
        })

    return format_collection(rows, source_url, coverage=[{"advertised_total": expected_total}],
                             limitations=[])
