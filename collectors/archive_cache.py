"""Read-only November evidence store. Missing responses never fall back to HTTP."""

import csv
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


PASS = "collection_pass_2026-11-02"
FOLLOWUP = "collection_followup_2026-11-02"
SPRINT = "collection_sprint_2026-11-09"


class MissingCapture(ValueError):
    """A successful saved response is unavailable."""


def _lookup_key(url):
    parts = urlsplit(url)
    # Used only for locating request captures. Extracted media URLs remain intact.
    return parts.netloc, parts.path.rstrip("/"), tuple(sorted(parse_qsl(parts.query, keep_blank_values=True)))


class ArchiveCache:
    def __init__(self, root):
        self.root = Path(root)
        self.exact = {}
        self.normalized = {}
        for batch in (PASS, FOLLOWUP, SPRINT):
            for file in sorted((self.root / batch / "pages").glob("*.json")):
                meta = json.loads(file.read_text(encoding="utf-8"))
                payload = file.with_suffix(".html")
                if str(meta.get("status")) != "200" or not payload.is_file():
                    continue
                for key in ("requested_url", "final_url"):
                    if url := meta.get(key):
                        item = (meta, payload)
                        self.exact.setdefault(url, item)
                        self.normalized.setdefault(_lookup_key(url), item)

    def fetch(self, url):
        item = self.exact.get(url) or self.normalized.get(_lookup_key(url))
        if not item:
            raise MissingCapture("Successful response absent from the cache")
        meta, path = item
        return meta.get("final_url") or url, path.read_text(encoding="utf-8")

    def data(self, batch, place_id, name, default=None):
        file = self.root / batch / place_id / name
        if not file.is_file():
            return default
        return json.loads(file.read_text(encoding="utf-8"))

    def rows(self, batch, place_id, name):
        file = self.root / batch / place_id / name
        if not file.is_file():
            return []
        with file.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
