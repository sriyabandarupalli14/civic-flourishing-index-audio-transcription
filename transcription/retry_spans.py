"""Prepare bounded alternate decodes without replacing any original transcript text."""

import math
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from media_ingestion.pipeline import hash_file


def candidate_windows(quality, duration, limit):
    windows = []
    for flag in quality.get("segment_flags", []):
        if "invalid_timestamps" in flag["reasons"]:
            continue
        start = max(0, flag["start_seconds"] - 1)
        end = min(duration, flag["end_seconds"] + 1, start + 45)
        if end > start and not any(start < previous_end and end > previous_start
                                   for previous_start, previous_end in windows):
            windows.append((start, end))
            if len(windows) == limit:
                break
    return windows


def alternate_decodes(audio, result, model, limit):
    if not 1 <= limit <= 3 or hash_file(audio) != result["audio_sha256"]:
        raise ValueError("Expected verified audio and a retry limit of one to three")
    with wave.open(str(audio), "rb") as source, TemporaryDirectory() as name:
        if (source.getframerate(), source.getsampwidth(), source.getnchannels()) != (16000, 2, 1):
            raise ValueError("Expected Stage 3 PCM16 mono audio")
        windows = candidate_windows(result["quality"], source.getnframes() / 16000, limit)
        rows = []
        path = Path(name) / "segment.wav"
        for start, end in windows:
            first, last = math.floor(start * 16000), math.ceil(end * 16000)
            source.setpos(first)
            with wave.open(str(path), "wb") as clip:
                clip.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                clip.writeframes(source.readframes(last - first))
            alternative = model.transcribe(path, expected_sha256=hash_file(path))
            alternative.pop("audio_file", None)
            rows.append({"recording_start_seconds": first / 16000, "recording_end_seconds": last / 16000,
                         "candidate": alternative, "review_status": "alternate_decode_pending_review"})
        return rows
