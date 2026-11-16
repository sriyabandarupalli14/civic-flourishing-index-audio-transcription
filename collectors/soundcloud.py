"""Metadata collector for the soundcloud archive."""

import re
import time

from yt_dlp import YoutubeDL

from .records import format_collection

SPEAKER_RE = re.compile(r"\(([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,3})\)\s*$")


def speaker_from_title(title):
    """Return the name in parentheses at the end of a title, or blank if there is none."""
    m = SPEAKER_RE.search(title or "")
    return m.group(1) if m else ""


def collect_soundcloud(tracks_url, limit=None, *, downloader=YoutubeDL, failure_budget=None):
    """Type 4. Return one row per track on a SoundCloud account's /tracks page.

    `limit` keeps only the first N tracks, which is useful for a quick test.
    """
    # Step 1: list every track (titles and links only, no details yet)
    with downloader({"quiet": True, "extract_flat": True, "skip_download": True}) as ydl:
        listing = ydl.extract_info(tracks_url, download=False)
    tracks = [e for e in listing.get("entries", []) if e]
    if limit:
        tracks = tracks[:limit]

    # Step 2: look up each track for its date, duration, and permanent page link
    rows = []
    consecutive = 0
    blocked_lookup = False
    with downloader({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        for t in tracks:
            title = t.get("title") or ""
            row = {"audio_url": t.get("url"), "sermon_title": title,
                   "speaker": speaker_from_title(title),
                   "sermon_date": "", "duration_sec": "", "data_note": ""}
            if blocked_lookup:
                row["data_note"] = "details skipped after repeated access failures"
                rows.append(row)
                continue
            try:
                info = ydl.extract_info(t["url"], download=False)
                d = info.get("upload_date") or ""   # comes as YYYYMMDD
                row["audio_url"] = info.get("webpage_url") or t["url"]
                row["sermon_date"] = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else ""
                row["duration_sec"] = round(info["duration"]) if info.get("duration") else ""
                row["data_note"] = "date is SoundCloud upload date, may differ from service date"
                consecutive = 0
            except Exception as e:
                row["data_note"] = f"details unavailable: {type(e).__name__}"
                consecutive = consecutive + 1 if "403" in str(e) else 0
                if failure_budget is not None and consecutive >= failure_budget:
                    blocked_lookup = True
            rows.append(row)
            time.sleep(0.3)   # be gentle with the site
    for row in rows:
        row["source_page"] = row["audio_url"] or tracks_url
        row["date_basis"] = "SoundCloud upload date" if row["sermon_date"] else ""
    if not rows:
        raise ValueError("No SoundCloud tracks discovered")
    missing = sum(row["data_note"].startswith(("details unavailable", "details skipped")) for row in rows)
    return format_collection(rows, tracks_url, coverage=[{"listed_tracks": len(tracks), "detail_failures": missing}],
                             limitations=["References are SoundCloud track pages"] +
                             (["Detail requests stopped after repeated HTTP 403 responses; remaining track links retained"]
                              if blocked_lookup else []))
