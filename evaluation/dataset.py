"""Plan a seeded, church-balanced sample and materialize exact local clips."""

import json
import re
import wave
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from media_ingestion.audio import RATE, decode_audio
from media_ingestion.pipeline import hash_file, hash_text, load_manifest
from media_ingestion.transport import MediaError, kind_for


def fingerprint(value):
    return hash_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def duplicate_keys(recording):
    source = recording.source_record
    # Conservative protection against leakage between provisionally split recordings.
    keys = {("url_path", urlsplit(url).netloc.lower(), urlsplit(url).path)
            for url in recording.references if kind_for(url) != "unsupported"}
    if recording.identity:
        keys.add(("platform_id", *recording.identity))
    if title := " ".join(source.get("title", "").casefold().split()):
        keys.add(("church_title", recording.church_id, title))
    return keys


def plan_dataset(manifest_path, settings, development_ids=(), *, failed_recordings=(), failed_churches=()):
    recordings = load_manifest(manifest_path)
    seed, church_target, per_church, development_count, start, duration = (
        settings[key] for key in ("seed", "churches", "clips_per_church", "development_per_church",
                                  "start_seconds", "duration_seconds")
    )
    if (not all(type(value) is int for value in (seed, church_target, per_church, development_count, start, duration))
            or church_target < 1 or not 0 < development_count < per_church or start < 0 or duration < 1):
        raise ValueError("Expected positive integer sample targets, both subsets, and a valid window")
    anchors = set(development_ids)
    excluded_ids, excluded_churches = set(failed_recordings), set(failed_churches)
    if anchors - recordings.keys():
        raise ValueError("A development recording is absent from the source manifest")
    pools = defaultdict(list)
    for recording in recordings.values():
        if recording.identifier in excluded_ids or recording.church_id in excluded_churches:
            if recording.identifier in anchors:
                raise ValueError("Cannot exclude a development anchor")
            continue
        if recording.held_reason():
            if recording.identifier in anchors:
                raise ValueError("A development recording is held by Stage 3")
            continue
        is_direct_audio = any(kind_for(url) == "audio" for url in recording.references)
        duration_hint = recording.source_record.get("duration_sec")
        if duration_hint and float(duration_hint) < start + duration and recording.identifier not in anchors:
            continue
        if is_direct_audio or recording.identifier in anchors:
            pools[recording.church_id].append(recording)
    anchor_churches = {recordings[key].church_id for key in anchors}
    if len(anchor_churches) > church_target:
        raise ValueError("Development anchors exceed the church target")

    def rank(label, key):
        return fingerprint([seed, label, key])

    seen_keys = set()
    for key in sorted(anchors):
        keys = duplicate_keys(recordings[key])
        if seen_keys & keys:
            raise ValueError("Development anchors may be duplicate sermons")
        seen_keys.update(keys)
    clips, chosen_churches = [], []
    for church in sorted(pools, key=lambda key: (key not in anchor_churches, rank("church", key))):
        fixed = sorted((r for r in pools[church] if r.identifier in anchors), key=lambda r: r.identifier)
        if len(fixed) > development_count:
            raise ValueError("Development anchors exceed the per-church development quota")
        local_keys = set()
        chosen = list(fixed)
        for recording in sorted(pools[church], key=lambda r: rank("recording", r.identifier)):
            if len(chosen) >= per_church:
                break
            keys = duplicate_keys(recording)
            if recording.identifier in anchors or keys & (seen_keys | local_keys):
                continue
            local_keys.update(keys)
            chosen.append(recording)
        if len(chosen) < per_church and church not in anchor_churches:
            continue
        chosen_churches.append(church)
        seen_keys.update(local_keys)
        for index, recording in enumerate(chosen):
            clips.append({
                "clip_id": f"{recording.identifier}-{start}s-{duration}s",
                "recording_id": recording.identifier, "church_id": church,
                "subset": "development" if index < development_count else "held_out",
                "start_seconds": start, "duration_seconds": duration,
                "source_record": recording.source_record,
            })
        if len(chosen_churches) == church_target:
            break
    body = {"schema_version": 1, "stage2_manifest_sha256": hash_file(manifest_path), "settings": settings,
            "development_ids": sorted(anchors), "failed_recordings": sorted(excluded_ids),
            "failed_churches": sorted(excluded_churches), "target_clips": church_target * per_church,
            "candidate_churches": len(pools), "clips": clips}
    return {**body, "plan_sha256": fingerprint(body)}


def decode_clip(source, destination, start, duration):
    """Use the Stage 3 decoder, then cut an exact sample-aligned window."""
    with TemporaryDirectory(dir=destination.parent) as temporary:
        prefix = Path(temporary) / "prefix.wav"
        clip = Path(temporary) / "clip.wav"
        extent = start + duration
        decoded = decode_audio(source, prefix, sample_seconds=extent, max_seconds=extent)
        if decoded["samples"] < extent * RATE:
            raise MediaError("audio_shorter_than_planned_window")
        with wave.open(str(prefix), "rb") as handle:
            handle.setpos(start * RATE)
            frames = handle.readframes(duration * RATE)
        with wave.open(str(clip), "wb") as handle:
            handle.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            handle.writeframes(frames)
        clip.replace(destination)


def verify_plan(plan):
    body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if fingerprint(body) != plan["plan_sha256"]:
        raise ValueError("Plan checksum mismatch")
    ids = set()
    for clip in plan["clips"]:
        recording = clip["recording_id"]
        start, duration = clip["start_seconds"], clip["duration_seconds"]
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", recording)
                or type(start) is not int or type(duration) is not int or start < 0 or duration < 1
                or recording in ids or clip["clip_id"] != f"{recording}-{start}s-{duration}s"
                or clip["subset"] not in {"development", "held_out"}):
            raise ValueError("Invalid or repeated planned clip")
        ids.add(recording)


def materialize(plan, sources, output):
    verify_plan(plan)
    clips_dir = output / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    by_id = {}
    for source in sources:
        key = source["recording_id"]
        if key in by_id:
            raise ValueError("Multiple local sources for one recording")
        by_id[key] = source

    rows = []
    used_audio, used_clips = set(), set()
    for clip in plan["clips"]:
        source = by_id.get(clip["recording_id"])
        row = {**clip, "status": "missing_local_audio"}
        rows.append(row)
        if not source:
            continue
        references = (url for field in ("audio_urls", "video_urls", "stream_urls", "player_urls")
                      for url in json.loads(clip["source_record"].get(field) or "[]"))
        if source.get("reference_sha256") not in {hash_text(url) for url in references}:
            row["status"] = "source_reference_mismatch"
            continue
        path = Path(source["audio_file"])
        row["audio_source"] = source
        if not path.is_file() or hash_file(path) != source["sha256"]:
            row["status"] = "source_checksum_mismatch"
            continue
        if source["sha256"] in used_audio:
            row["status"] = "duplicate_audio"
            continue
        destination = clips_dir / f"{clip['clip_id']}.wav"
        with TemporaryDirectory(dir=clips_dir) as temporary:
            staged = Path(temporary) / "clip.wav"
            try:
                decode_clip(path, staged, clip["start_seconds"], clip["duration_seconds"])
            except MediaError as error:
                row["status"] = error.code
                continue
            clip_sha256 = hash_file(staged)
            if clip_sha256 in used_clips:
                row["status"] = "duplicate_clip"
                continue
            staged.replace(destination)
        used_audio.add(source["sha256"])
        used_clips.add(clip_sha256)
        row.update(status="ready", clip_file=str(destination.relative_to(output)), clip_sha256=clip_sha256,
                   sample_rate=RATE, channels=1, sample_count=clip["duration_seconds"] * RATE)
    ready = [row for row in rows if row["status"] == "ready"]
    return {
        "plan_sha256": plan["plan_sha256"], "clips": rows,
        "coverage": {"target_clips": plan["target_clips"], "planned_clips": len(rows),
                     "planned_churches": len({row["church_id"] for row in rows}),
                     "planned_by_subset": dict(Counter(row["subset"] for row in rows)),
                     "ready_clips": len(ready), "ready_churches": len({row["church_id"] for row in ready}),
                     "ready_by_subset": dict(Counter(row["subset"] for row in ready)),
                     "statuses": dict(Counter(row["status"] for row in rows))},
    }
