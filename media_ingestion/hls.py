"""Resolve HLS playlists and stage only the segments needed for local decoding."""

import math
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from .transport import MediaError, require_http, transfer


@dataclass
class Playlist:
    segments: list = field(default_factory=list)
    ended: bool = False
    version: str = "3"


def parse_playlist(body, base):
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise MediaError("invalid_hls_manifest") from None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise MediaError("invalid_hls_manifest")

    audio, variants = [], []
    pending = None
    for line in lines[1:]:
        if line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line:
            match = re.search(r'(?:^|,)URI="([^"]+)"', line)
            if match:
                audio.append(("DEFAULT=YES" not in line, urljoin(base, match[1])))
        elif line.startswith("#EXT-X-STREAM-INF:"):
            rate = re.search(r"(?:^|[:,])BANDWIDTH=(\d+)", line)
            pending = int(rate[1]) if rate else 0
        elif pending is not None and not line.startswith("#"):
            variants.append((pending, urljoin(base, line)))
            pending = None
    if variants or audio:
        return sorted(audio or variants)[0][1]

    result = Playlist()
    duration, init, gap, discontinuity = None, "", False, False
    for line in lines[1:]:
        if line.startswith(("#EXT-X-BYTERANGE", "#EXT-X-SKIP", "#EXT-X-GAP")):
            gap = True
        elif line.startswith("#EXT-X-KEY:") and not re.search(r"METHOD=NONE(?:,|$)", line):
            raise MediaError("encrypted_hls_unsupported")
        elif line.startswith("#EXT-X-MAP:"):
            match = re.search(r'URI="([^"]+)"', line)
            if not match or "BYTERANGE" in line:
                raise MediaError("unsupported_hls_map")
            init = urljoin(base, match[1])
        elif line.startswith("#EXTINF:"):
            try:
                duration = float(line.removeprefix("#EXTINF:").split(",")[0])
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError
            except ValueError:
                raise MediaError("invalid_hls_segment_duration") from None
        elif line.startswith("#EXT-X-VERSION:"):
            value = line.split(":", 1)[1]
            if value.isdigit():
                result.version = value
        elif line == "#EXT-X-DISCONTINUITY":
            discontinuity = True
        elif line == "#EXT-X-ENDLIST":
            result.ended = True
        elif not line.startswith("#") and duration is not None:
            if gap:
                raise MediaError("byte_range_or_gap_hls_unsupported")
            target = urljoin(base, line)
            require_http(target)
            result.segments.append((duration, target, init, discontinuity))
            duration, discontinuity = None, False
    if not result.segments:
        raise MediaError("empty_hls_playlist")
    return result


def load_playlist(session, source):
    url, seen = source.url, set()
    for _ in range(4):
        if url in seen:
            break
        seen.add(url)
        body, base, _ = transfer(session, url, headers=source.headers,
                                 limit=1024 * 1024, expected="manifest")
        result = parse_playlist(body, base)
        if isinstance(result, Playlist):
            return result
        url = result
    raise MediaError("hls_nested_playlist_limit")


def stage_hls(session, source, playlist, workdir, *, max_bytes, max_seconds, sample_seconds=None):
    if not playlist.ended and sample_seconds is None:
        raise MediaError("unbounded_live_hls_requires_sample")
    target_seconds = sample_seconds or max_seconds
    if sample_seconds is None and sum(s[0] for s in playlist.segments) > max_seconds:
        raise MediaError("audio_duration_limit")
    total, remaining = 0.0, max_bytes
    cached_maps = {}
    lines = ["#EXTM3U", f"#EXT-X-VERSION:{playlist.version}",
             f"#EXT-X-TARGETDURATION:{math.ceil(max(s[0] for s in playlist.segments))}",
             "#EXT-X-MEDIA-SEQUENCE:0"]
    previous_map = ""
    for number, (duration, url, init, discontinuity) in enumerate(playlist.segments):
        if total >= target_seconds:
            break
        if number >= 4000 or remaining <= 0:
            raise MediaError("hls_staging_limit")
        if discontinuity:
            lines.append("#EXT-X-DISCONTINUITY")
        if init != previous_map and init:
            if init not in cached_maps:
                init_name = f"init-{len(cached_maps)}.mp4"
                with (workdir / init_name).open("wb") as handle:
                    _, _, size = transfer(session, init, headers=source.headers, limit=remaining, sink=handle)
                remaining -= size
                if remaining <= 0:
                    raise MediaError("byte_limit_exceeded")
                cached_maps[init] = init_name
            lines.append(f'#EXT-X-MAP:URI="{cached_maps[init]}"')
        previous_map = init
        name = f"segment-{number}.{'m4s' if init else 'ts'}"
        with (workdir / name).open("wb") as handle:
            _, _, size = transfer(session, url, headers=source.headers, limit=remaining, sink=handle)
        remaining -= size
        lines.extend((f"#EXTINF:{duration},", name))
        total += duration
    lines.append("#EXT-X-ENDLIST")
    path = workdir / "source.m3u8"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, max_bytes - remaining
