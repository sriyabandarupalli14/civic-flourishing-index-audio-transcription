"""Run cached Whisper inference with audio, model and resource provenance."""

import inspect
import platform
import time
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from threading import Event, Thread

from media_ingestion.pipeline import hash_file

from .checkpoints import digest
from .quality import repetition_flags


@contextmanager
def measure_resources(interval=0.02):
    """Sample process RSS during a phase, including model-resident memory."""
    from psutil import Process

    process = Process()
    initial = process.memory_info().rss
    result = {"rss_start_bytes": initial, "rss_peak_sampled_bytes": initial,
              "rss_end_bytes": initial, "rss_sample_interval_seconds": interval}
    stop = Event()

    def sample():
        while not stop.wait(interval):
            result["rss_peak_sampled_bytes"] = max(result["rss_peak_sampled_bytes"],
                                                   process.memory_info().rss)

    thread = Thread(target=sample, daemon=True)
    thread.start()
    started = time.perf_counter()
    try:
        yield result
    finally:
        result["wall_seconds"] = time.perf_counter() - started
        stop.set()
        thread.join()
        result["rss_end_bytes"] = process.memory_info().rss
        result["rss_peak_sampled_bytes"] = max(result["rss_peak_sampled_bytes"], result["rss_end_bytes"])


class WhisperInference:
    def __init__(self, config, *, model_dir=None):
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model

        self.config = config
        settings = config["model"]
        snapshot = Path(model_dir) if model_dir is not None else Path(download_model(
            settings["name"], revision=settings.get("revision"),
            local_files_only=settings.get("local_files_only", True),
        ))
        if not (snapshot / "model.bin").is_file():
            raise ValueError("Expected a local CTranslate2 model directory containing model.bin")
        self.model_provenance = {
            "name": settings["name"], "requested_revision": settings.get("revision"),
            "snapshot": snapshot.name,
            "files_sha256": {p.name: hash_file(p) for p in sorted(snapshot.iterdir()) if p.is_file()},
        }
        self.software = {name: version(name) for name in ("faster-whisper", "ctranslate2", "av", "psutil")}
        self.hardware = {**config["hardware"], "system": platform.system(),
                         "architecture": platform.machine(), "python": platform.python_version()}
        with measure_resources() as self.initialization:
            self.model = WhisperModel(str(snapshot), local_files_only=True, **config["hardware"])
        self.configuration_sha256 = digest({"config": config, "model": self.model_provenance,
                                             "software": self.software, "hardware": self.hardware})

    def transcribe(self, audio, *, metadata=None, expected_sha256=None):
        audio_sha = hash_file(audio)
        if expected_sha256 is not None and audio_sha != expected_sha256:
            raise ValueError("Audio checksum does not match the input manifest")
        options = inspect.signature(self.model.transcribe).bind(str(audio), **self.config["decoding"])
        options.apply_defaults()
        effective = {name: value for name, value in options.arguments.items() if name != "audio"}
        with measure_resources() as inference:
            segments, info = self.model.transcribe(str(audio), **self.config["decoding"])
            rows = [{
                "id": segment.id, "start_seconds": segment.start, "end_seconds": segment.end,
                "text": segment.text.strip(), "avg_logprob": segment.avg_logprob,
                "no_speech_probability": segment.no_speech_prob, "compression_ratio": segment.compression_ratio,
                "temperature": segment.temperature,
                "words": [dict(start_seconds=w.start, end_seconds=w.end, word=w.word,
                               probability=w.probability) for w in segment.words] if segment.words else [],
            } for segment in segments]
        text = " ".join(row["text"] for row in rows).strip()
        flags = repetition_flags(text)
        return {
            "audio_file": str(audio), "audio_sha256": audio_sha, "audio_duration_seconds": info.duration,
            "metadata": metadata or {}, "model": self.model_provenance, "software_versions": self.software,
            "configuration_sha256": self.configuration_sha256, "hardware": self.hardware, "decoding": effective, "prompt": self.config["prompt"],
            "model_initialization": self.initialization, "inference": inference,
            "language": info.language, "language_probability": info.language_probability,
            "text": text, "segments": rows, "human_reviewed": False,
            "quality": {"empty_transcript": not text, "repetition_flags": flags,
                        "needs_review": not text or bool(flags)},
        }
