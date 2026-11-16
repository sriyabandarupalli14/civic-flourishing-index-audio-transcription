"""Refresh public media references using stable source evidence."""

import json
import re
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .transport import MediaError, kind_for, transfer


def refresh_public_reference(session, recording, previous):
    identity = recording.identity
    if (len(identity) == 3 and identity[:2] == ("boxcast", "broadcast")
            and re.fullmatch(r"[A-Za-z0-9_-]+", identity[2])
            and urlsplit(previous).hostname == "play.boxcast.com"
            and recording.church_number == "12"):
        query = urlencode({"channel_id": "fbexcfs5ufi8aldoipyb", "host": "www.gracechurchal.com"})
        endpoint = f"https://api.boxcast.com/broadcasts/{identity[2]}/view?{query}"
        body, _, _ = transfer(session, endpoint, limit=1024 * 1024, expected="json")
        try:
            payload = json.loads(body)
            candidate = payload.get("playlist", "") if payload.get("status") == "recorded" else ""
        except (ValueError, AttributeError):
            raise MediaError("invalid_boxcast_metadata") from None
        if kind_for(candidate) == "hls" and urlsplit(candidate).hostname == "play.boxcast.com":
            return candidate

    old = urlsplit(previous)
    if old.hostname and old.hostname.endswith(".wasabisys.com"):
        for page in recording.pages:
            parts = urlsplit(page)
            if (parts.hostname not in {"firstus.adventistchurch.org", "mpwc.adventistchurch.org"}
                    or not re.fullmatch(r"/online-sermons/[^/]+/?", parts.path)):
                continue
            body, final, _ = transfer(session, page, limit=2 * 1024 * 1024, expected="html")
            soup = BeautifulSoup(body, "html.parser")
            for node in soup.select("audio[src], video[src], audio source[src], video source[src]"):
                candidate = urljoin(final, node["src"])
                updated = urlsplit(candidate)
                if (updated.hostname, updated.path) == (old.hostname, old.path):
                    return candidate
    return None
