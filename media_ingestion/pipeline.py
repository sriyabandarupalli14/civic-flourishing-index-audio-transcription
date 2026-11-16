"""Select approved-scope candidates and checkpoint individual media jobs."""

import csv
import fcntl
import hashlib
import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.parse import urlsplit

from .audio import RATE, decode_audio
from .eligibility import KINDS, assess
from .hls import load_playlist, stage_hls
from .refresh import refresh_public_reference
from .transport import MediaError, expired, head_status, kind_for, resolve, transfer

REFERENCE_COLUMNS = ("audio_urls", "video_urls", "stream_urls", "player_urls")
REFRESH_ERRORS = {"expired_signed_url", "http_401", "http_403", "http_404", "http_410"}


def array(row, key):
    values = json.loads(row.get(key) or "[]")
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError(f"Invalid manifest array: {key}")
    return values


@dataclass(frozen=True)
class Recording:
    identifier: str
    church_id: str
    church_number: str
    references: tuple
    identity: tuple = ()
    pages: tuple = ()
    review_flags: tuple = ()

    title_variants: tuple = ()
    source_record: dict = field(default_factory=dict, compare=False, repr=False)

    def held_reason(self):
        return assess(self).primary_hold


def load_manifest(path):
    recordings = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        required = {"recording_id", "church_id", "church_number", "review_flags", *REFERENCE_COLUMNS}
        if not required <= set(rows.fieldnames or []):
            raise ValueError("Missing required Stage 2 manifest columns")
        for row in rows:
            key = row["recording_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key) or key in recordings:
                raise ValueError("Invalid or duplicate recording ID")
            urls = tuple(dict.fromkeys(url for column in REFERENCE_COLUMNS for url in array(row, column)))
            variants = json.loads(row.get("metadata_variants") or "{}")
            if not isinstance(variants, dict):
                raise ValueError("Invalid metadata variants")
            titles = variants.get("title", [])
            if not isinstance(titles, list) or any(not isinstance(title, str) for title in titles):
                raise ValueError("Invalid title variants")
            recordings[key] = Recording(key, row["church_id"], row["church_number"], urls,
                                        tuple(array(row, "platform_identifier")),
                                        tuple(array(row, "source_pages")), tuple(array(row, "review_flags")),
                                        tuple(titles), row)
    return recordings


def hash_file(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def hash_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def atomic_json(path, payload):
    with NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def lock(path):
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MediaError("recording_locked", retryable=True) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 128 * 1024 * 1024
    max_seconds: int = 10800
    sample_seconds: float | None = None
    attempts: int = 2
    max_references: int = 3

    def __post_init__(self):
        import math

        if (self.max_bytes <= 0 or self.max_seconds <= 0 or not 1 <= self.attempts <= 5
                or not 1 <= self.max_references <= 10
                or (self.sample_seconds is not None
                    and (not math.isfinite(self.sample_seconds)
                         or not 1 / RATE <= self.sample_seconds <= self.max_seconds))):
            raise ValueError("Invalid media limits")


def reference_fingerprint(recording):
    return hash_text(json.dumps([recording.church_id, recording.church_number, recording.identity,
                                 recording.references, recording.pages]))


def task_key(recording, kind, limits):
    return hash_text(json.dumps([reference_fingerprint(recording), kind, limits.max_bytes,
                                limits.max_seconds, limits.sample_seconds, RATE, "mono_pcm_v1"]))[:16]


def valid_checkpoint(metadata, audio, key):
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
        decoded = data["audio"]
        if (data["task_key"] == key and data["bytes"] == audio.stat().st_size
                and data["sha256"] == hash_file(audio) and decoded["sample_rate"] == RATE
                and decoded["channels"] == 1 and decoded["samples"] > 0
                and decoded["duration_seconds"] == decoded["samples"] / RATE):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


class MediaPipeline:
    def __init__(self, session, output, *, resolver=resolve, refresh=refresh_public_reference,
                 decoder=decode_audio, wait=time.sleep):
        self.session = session
        self.output = output
        self.resolver = resolver
        self.refresh = refresh
        self.decoder = decoder
        self.wait = wait

    def run(self, recording, *, mode, kind=None, limits=Limits(), log=None):
        if mode not in {"probe", "ingest"} or (kind is not None and kind not in KINDS):
            raise ValueError("Invalid media mode or type")
        def event(status, **info):
            entry = {"recording_id": recording.identifier, "status": status, "mode": mode, **info}
            if log is not None:
                log(entry)
            return entry

        if reason := recording.held_reason():
            return event("held", reason=reason)
        self.output.mkdir(parents=True, exist_ok=True)
        key = task_key(recording, kind, limits)
        prefix = self.output / f"{recording.identifier}-{key}"
        audio = prefix.with_suffix(".wav")
        metadata = prefix.with_suffix(".checkpoint.json")
        candidates = [(source, kind_for(source)) for source in recording.references
                      if kind_for(source) in KINDS and (kind is None or kind_for(source) == kind)]
        if not candidates:
            return event("failed", reason="no_reference_of_requested_type")

        with lock(prefix.with_suffix(".lock")):
            if mode == "ingest" and (saved := valid_checkpoint(metadata, audio, key)):
                return event("reused", audio_file=audio.name, **saved["audio"])
            last_error = "no_successful_reference"
            # Expired links are tried after the current alternatives, but still
            # get a chance to refresh from a verified individual source route.
            candidates.sort(key=lambda pair: (expired(pair[0]), KINDS.index(pair[1])))
            for original, category in candidates[:limits.max_references]:
                info = {"reference_type": category, "host": urlsplit(original).hostname,
                        "reference_sha256": hash_text(original)}
                current, refreshed = original, False
                for attempt in range(1, limits.attempts + 1):
                    try:
                        measured = self._attempt(current, mode, limits, audio)
                    except MediaError as error:
                        last_error = error.code
                        event("attempt_failed", attempt=attempt, reason=error.code,
                              refreshed=refreshed, **info)
                        if error.code in REFRESH_ERRORS and not refreshed:
                            try:
                                fresh = self.refresh(self.session, recording, original)
                            except MediaError as refresh_error:
                                event("refresh_failed", reason=refresh_error.code, **info)
                                fresh = None
                            if fresh and fresh != current:
                                current, refreshed = fresh, True
                                # Refresh is a separate recovery step even when
                                # the transient retry count has been exhausted.
                                try:
                                    measured = self._attempt(current, mode, limits, audio)
                                except MediaError as refresh_error:
                                    last_error = refresh_error.code
                                    event("attempt_failed", attempt=attempt, reason=last_error,
                                          refreshed=True, **info)
                                    error = refresh_error
                                else:
                                    return self._success(event, mode, measured, info, refreshed, audio,
                                                         metadata, key, recording, limits)
                        if error.retryable and attempt < limits.attempts:
                            self.wait(min(2 ** (attempt - 1), 8))
                            continue
                        break
                    else:
                        return self._success(event, mode, measured, info, refreshed, audio,
                                             metadata, key, recording, limits)
            return event("failed", reason=last_error)

    def _attempt(self, url, mode, limits, audio):
        if expired(url):
            raise MediaError("expired_signed_url")
        source = self.resolver(url)
        if expired(source.url):
            raise MediaError("expired_signed_url")
        playlist = load_playlist(self.session, source) if source.kind == "hls" else None
        if mode == "probe":
            if playlist:
                first = playlist.segments[0][1]
                transfer(self.session, first, headers=source.headers, limit=4096, prefix=True)
                return {"probe_method": "HLS playlist and first segment prefix", "playlist_ended": playlist.ended}
            head = head_status(self.session, source)
            _, _, size = transfer(self.session, source.url, headers=source.headers, limit=4096, prefix=True)
            return {"probe_method": "HEAD and ranged GET prefix", "head_status": head, "prefix_bytes": size}

        with TemporaryDirectory(prefix=".ingest-", dir=self.output) as name:
            directory = Path(name)
            if playlist:
                staged, size = stage_hls(self.session, source, playlist, directory,
                                         max_bytes=limits.max_bytes, max_seconds=limits.max_seconds,
                                         sample_seconds=limits.sample_seconds)
            else:
                staged = directory / "source.media"
                with staged.open("wb") as handle:
                    _, _, size = transfer(self.session, source.url, headers=source.headers,
                                          limit=limits.max_bytes, sink=handle,
                                          prefix=limits.sample_seconds is not None)
            wav = directory / "normalized.wav"
            measured = self.decoder(staged, wav, max_seconds=limits.max_seconds,
                                    sample_seconds=limits.sample_seconds)
            wav.replace(audio)
        return {**measured, "source_bytes": size, "source_scope": "sample" if limits.sample_seconds is not None else "full"}

    def _success(self, event, mode, measured, info, refreshed, audio, metadata, key, recording, limits):
        if mode == "probe":
            return event("accessible_reference", refreshed=refreshed, **info, **measured)
        checkpoint = {"task_key": key, "recording_id": recording.identifier,
                      "manifest_reference_sha256": reference_fingerprint(recording), "bytes": audio.stat().st_size,
                      "sha256": hash_file(audio), "audio": measured, "source": info,
                      "scope": "sample" if limits.sample_seconds is not None else "full"}
        atomic_json(metadata, checkpoint)
        return event("decoded_audio", refreshed=refreshed, audio_file=audio.name, **info, **measured)
