"""Normalize metadata without overwriting source evidence."""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

MISSING = {"", "n/a", "na", "none", "null", "nan", "undefined", "unknown", "not available", "-", "—"}
AUDIO = (".mp3", ".m4a", ".aac", ".aiff", ".wav", ".ogg", ".flac", ".opus")
VIDEO = (".mp4", ".m4v", ".webm", ".mov", ".mkv")
STREAM = (".m3u8", ".mpd")
IDENTITY_REVIEW_CHURCHES = {
    "2": "shared_adventist_archive", "10": "campus_attribution_unverified",
    "11": "shared_adventist_archive", "23": "church_location_unresolved",
    "32": "candidate_channel_association", "36": "church_location_unresolved",
    "40": "shared_adventist_archive", "44": "shared_adventist_archive",
}
MEDIA_FIELDS = (
    "media_url", "audio_url", "video_url", "player_url", "resolved_url",
    "original_audio_url", "candidate_original_audio_url",
)
ARRAY_FIELDS = ("media_urls", "all_media_references_json", "unresolved_urls")


def text(value):
    if value is None:
        return ""
    value = " ".join(unicodedata.normalize("NFC", str(value)).split())
    return "" if value.casefold() in MISSING else value


def first(row, *fields):
    return next((value for key in fields if (value := text(row.get(key)))), "")


def normalize_date(value):
    if not value:
        return ""
    for parse in (date.fromisoformat, datetime.fromisoformat, parsedate_to_datetime):
        try:
            parsed = parse(value)
            return (parsed.date() if isinstance(parsed, datetime) else parsed).isoformat()
        except (ValueError, TypeError, OverflowError):
            continue
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%A, %B %d, %Y",
                "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def normalize_duration(value):
    if not value:
        return ""
    try:
        units = [Decimal(part) for part in value.split(":")]
        if len(units) > 3 or any(not part.is_finite() or part < 0 for part in units):
            return ""
        if len(units) > 1 and any(part >= 60 for part in units[1:]):
            return ""
        if any(part != part.to_integral_value() for part in units[:-1]):
            return ""
        seconds = sum(part * 60**i for i, part in enumerate(reversed(units)))
        return format(seconds.normalize(), "f")
    except (InvalidOperation, ValueError):
        return ""


def url_parts(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                and not any(char.isspace() for char in url)):
            return parsed
    except ValueError:
        pass
    return None


def provider_identity(url):
    parts = url_parts(url)
    if parts is None:
        return None
    host, path = parts.hostname, parts.path
    query = parse_qs(parts.query)
    if host in {"youtu.be", "www.youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com",
                "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        if host in {"youtu.be", "www.youtu.be"}:
            key = path.strip("/").split("/")[0]
        elif path.rstrip("/") == "/watch":
            key = query.get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:embed|shorts|live)/([\w-]{11})/?", path)
            key = match[1] if match else ""
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", key):
            return ("youtube", "video", key)
    elif host == "drive.google.com":
        match = re.match(r"/file/d/([A-Za-z0-9_-]+)(?:/|$)", path)
        if match:
            return ("google_drive", "public_file", match[1])
    elif host in {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}:
        match = re.fullmatch(r"/(?:video/)?(\d+)/?", path)
        if match:
            return ("vimeo", "video", match[1])
    elif host.endswith(".sermon.net"):
        match = re.match(r"/main/[^/]+/(\d{7,})(?:/|$)|^/(\d{7,})/controller/", path)
        if match:
            return ("sermonnet", host, match[1] or match[2])
    elif host.endswith(".faithworshipcenterintl.com") or host == "faithworshipcenterintl.com":
        key = query.get("v", [""])[0]
        if path.lower() == "/video.aspx" and re.fullmatch(r"[a-fA-F0-9-]{36}", key):
            return ("web_video_catalog", host, key.casefold())
    elif host in {"hopecommunitypca.com", "www.hopecommunitypca.com"}:
        message = query.get("enmse_mid", [""])[0]
        if path.rstrip("/") == "/sermons" and query.get("enmse_av") == ["1"] and message.isdigit():
            return ("series_engine", "hopecommunitypca.com", message)
    elif host in {"oakgrovepm.com", "www.oakgrovepm.com"}:
        key = query.get("download_id", [""])[0]
        if key.isdigit():
            return ("faithconnector", "www.oakgrovepm.com", key)
    return None


def reference_kind(url):
    parts = url_parts(url)
    if parts is None:
        return "invalid_reference"
    path = parts.path.lower()
    if path.endswith((".js", ".css", ".svg")):
        return "non_media_asset"
    if path.endswith(AUDIO):
        return "audio_file"
    if path.endswith(VIDEO):
        return "video_file"
    if path.endswith(STREAM):
        return "stream"
    query = parse_qs(parts.query)
    if set(query) & {"download", "download_mp3", "download_id"}:
        return "unresolved_endpoint"
    if provider_identity(url):
        return "player"
    if ((parts.hostname == "soundcloud.com" and len(path.strip("/").split("/")) == 2
         and not path.endswith(("/tracks", "/albums", "/sets")))
            or (parts.hostname == "video.ibm.com" and re.fullmatch(r"/recorded/\d+/?", path))
            or (parts.hostname in {"facebook.com", "www.facebook.com", "m.facebook.com"}
                and (re.search(r"/videos/\d+", path) or path.startswith("/share/v/")))
            or ((parts.hostname == "subsplash.com" or parts.hostname.endswith(".subsplash.com"))
                and "/media/" in path)):
        return "player"
    return "unresolved_endpoint"


def extract_references(row, flags):
    links = {}
    for key in MEDIA_FIELDS:
        value = row.get(key, "").strip()
        if value.casefold() not in MISSING:
            links[value] = reference_kind(value)
    for key in ARRAY_FIELDS:
        raw = row.get(key, "")
        if not raw:
            continue
        try:
            values = json.loads(raw)
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ValueError("Expected a string array")
        except (ValueError, TypeError):
            flags.add("invalid_reference_array")
            continue
        for value in values:
            url = value.strip()
            if url.casefold() not in MISSING:
                kind = reference_kind(url)
                if key == "unresolved_urls" and kind not in {"invalid_reference", "non_media_asset"}:
                    kind = "unresolved_endpoint"
                links[url] = kind if key == "unresolved_urls" else links.get(url, kind)
    if any(kind == "invalid_reference" for kind in links.values()):
        flags.add("invalid_reference")
    return links


def explicit_ids(row, church_number):
    identifiers = set()
    platform = text(row.get("platform"))
    scope = text(row.get("platform_scope"))
    key = text(row.get("platform_recording_id"))
    if platform and scope and key:
        if platform == "web_video_catalog":
            key = key.casefold()
        identifiers.add((platform.casefold(), scope.casefold(), key))
    parent = text(row.get("parent_sermon_recording_key"))
    # Only source-observed parent links already established by the November collector.
    if (platform == "wordpress_attachment" and parent.startswith("wordpress_sermon_post:")
            and text(row.get("source_parent_post")) and parent.rsplit(":", 1)[-1] == text(row.get("source_parent_post"))):
        identifiers = {tuple(parent.split(":", 2))}
    note = row.get("data_note", "")
    hooks = {
        "12": (r"BoxCast broadcast ID: ([A-Za-z0-9_-]+)", "boxcast", "broadcast"),
        "14": (r"Subsplash media ID: ([A-Za-z0-9_-]+)", "subsplash", "media-item"),
        "16": (r"(?:^|; )media ID: (\d+)", "churchsquare", "vbcmoulton.org"),
        "19": (r"SermonCloud sermon ID: (\d+)", "sermoncloud", "covenant-life-ministries"),
    }
    if church_number in {"7", "15"} and first(row, "collection_origin") == "prior_verified_live":
        source_id = first(row, "source_id")
        if source_id.isdigit():
            provider, scope = (("sermonnet", "graceclanton.sermon.net") if church_number == "7"
                               else ("sermon_browser", "www.prattvillecommunitychurch.org"))
            identifiers.add((provider, scope, source_id))
    if rule := hooks.get(church_number):
        pattern, provider, namespace = rule
        if match := re.search(pattern, note):
            identifiers.add((provider, namespace, match[1]))
    return identifiers


@dataclass
class Observation:
    line: int
    church_id: str
    church_number: str
    church_name: str
    title: str
    speaker: str
    date: str
    date_basis: str
    duration: str
    page: str
    refs: dict
    identities: set
    flags: set = field(default_factory=set)


def normalize(row, line):
    flags = set()
    ids = {value for key in ("registry_place_id", "place_id", "placeid")
           if (value := text(row.get(key)))}
    church = first(row, "registry_place_id", "place_id", "placeid")
    if len(ids) > 1:
        flags.add("conflicting_church_ids")
    if not church:
        flags.add("missing_church_id")
    number = first(row, "church_number")
    if number in IDENTITY_REVIEW_CHURCHES:
        flags.add(IDENTITY_REVIEW_CHURCHES[number])
    status = first(row, "identity_review").casefold()
    identity_note = first(row, "identity_note").casefold()
    if any(term in status or term in identity_note for term in ("unresolved", "shared-source", "shared source", "manual confirmation")):
        flags.add("source_identity_review")
    if first(row, "platform") == "wordpress_attachment":
        flags.add("attachment_requires_sermon_review")
    if first(row, "content_review") or first(row, "historical_snapshot_url"):
        flags.add("source_content_review")
    if "inactive_church" in first(row, "collection_origin"):
        flags.add("inactive_church_archive")
    raw_date = first(row, "date", "sermon_date", "publish_date", "source_date_raw")
    raw_duration = first(row, "duration_sec", "source_duration_raw")
    day, duration = normalize_date(raw_date), normalize_duration(raw_duration)
    if raw_date and not day:
        flags.add("unparsed_date")
    if raw_duration and not duration:
        flags.add("unparsed_duration")
    page = first(row, "source_page_url", "sermon_url", "source_item_url")
    refs = extract_references(row, flags)
    identifiers = explicit_ids(row, number)
    inferred = {identity for url in [page, *refs] if (identity := provider_identity(url))}
    # An explicit source identity takes precedence over other namespaces. Disagreement
    # within its own namespace remains visible and prevents automatic bridging.
    if identifiers:
        namespaces = {key[:2] for key in identifiers}
        identifiers |= {key for key in inferred if key[:2] in namespaces}
    else:
        identifiers = inferred
    if len(identifiers) > 1:
        flags.add("multiple_recording_identifiers")
    return Observation(line, church, number, first(row, "registry_church_name", "church_name"),
                       first(row, "title", "sermon_title"), first(row, "speaker", "speaker_name"),
                       day, first(row, "date_basis", "date_meaning"), duration, page, refs,
                       identifiers, flags)


def exact_payload(row, observation):
    """Match normalized content, exact references and scoped source IDs."""
    if (not observation.church_id or observation.flags & {
            "conflicting_church_ids", "multiple_recording_identifiers"}):
        return ("input_record", observation.line)
    if not observation.identities and not any(
        kind in {"audio_file", "video_file", "player"} for kind in observation.refs.values()
    ):
        # Exact raw duplicates still qualify. Weak or missing identifiers do
        # not justify collapsing otherwise separate observations.
        return ("raw", tuple(sorted(row.items())))
    return (
        "normalized", observation.church_id, tuple(sorted(observation.identities)),
        observation.title, observation.speaker,
        observation.date or first(row, "date", "sermon_date", "publish_date", "source_date_raw"),
        observation.date_basis, observation.duration or first(row, "duration_sec", "source_duration_raw"),
        tuple(sorted(observation.refs.items())), tuple(sorted(observation.flags)),
    )
