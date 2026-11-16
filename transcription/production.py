"""Consume only eligible Stage 3 full audio with intact provenance."""

import json
from tempfile import TemporaryDirectory
from pathlib import Path

from media_ingestion.audio import decode_audio
from media_ingestion.pipeline import (
    Limits, atomic_json, hash_file, hash_text, lock, reference_fingerprint, task_key, valid_checkpoint,
)


def stage_verified_local(recording, media, source_url, source_sha256, output, provenance):
    """Import a checksum-pinned full local source into an isolated Stage 3 audio directory."""
    if (recording.held_reason() or source_url not in recording.references or not media.is_file()
            or hash_file(media) != source_sha256):
        raise ValueError("Local source must match an eligible recording, reference and checksum")
    limits = Limits(max_seconds=10800)
    key = task_key(recording, None, limits)
    output.mkdir(parents=True, exist_ok=True)
    prefix = output / f"{recording.identifier}-{key}"
    wav, checkpoint = prefix.with_suffix(".wav"), prefix.with_suffix(".checkpoint.json")
    with lock(prefix.with_suffix(".lock")), TemporaryDirectory(dir=output) as temporary:
        staged = Path(temporary) / "audio.wav"
        stats = decode_audio(media, staged, max_seconds=limits.max_seconds)
        state = {
            "recording_id": recording.identifier, "task_key": key, "scope": "full",
            "manifest_reference_sha256": reference_fingerprint(recording),
            "sha256": hash_file(staged), "bytes": staged.stat().st_size, "audio": stats,
            "source": {"origin": "local_full_media", "audio_sha256": source_sha256,
                       "reference_sha256": hash_text(source_url), "provenance": provenance},
        }
        staged.replace(wav)
        atomic_json(checkpoint, state)
    return state


def stage3_entries(recordings, audio_dir, selected=()):
    """Return checksummed transcription inputs and a separate exclusion inventory."""
    include = set(selected)
    if include - recordings.keys():
        raise ValueError("Unknown recording ID in selection")
    entries, exclusions = {}, []
    for path in sorted(audio_dir.glob("*.checkpoint.json")):
        key = path.name.removesuffix(".checkpoint.json")
        identifier, _, task = key.rpartition("-")
        if include and identifier not in include:
            continue
        recording = recordings.get(identifier)
        wav = path.with_name(key + ".wav")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if recording is None or recording.held_reason():
                reason = "held_or_unknown_recording"
            elif state["scope"] != "full":
                reason = "preview_only"
            elif (state["recording_id"] != recording.identifier
                  or state["manifest_reference_sha256"] != reference_fingerprint(recording)
                  or not valid_checkpoint(path, wav, task)):
                reason = "media_checkpoint_mismatch"
            else:
                entry = {"recording_id": recording.identifier, "audio_file": str(wav.resolve()),
                         "audio_sha256": state["sha256"], "audio_duration_seconds": state["audio"]["duration_seconds"],
                         "source_record": recording.source_record, "stage3_checkpoint": state}
                stamp = path.stat().st_mtime_ns
                if identifier not in entries or stamp > entries[identifier][0]:
                    entries[identifier] = (stamp, entry)
                continue
        except (OSError, ValueError, KeyError, TypeError):
            reason = "invalid_media_checkpoint"
        exclusions.append({"recording_id": identifier, "reason": reason})
    for key in sorted(include - entries.keys()):
        exclusions.append({"recording_id": key, "reason": "no_verified_full_audio"})
    return [entry for _, entry in entries.values()], exclusions
