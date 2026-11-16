"""Local independent wav2vec2-CTC pseudo-reference inference."""

import wave
from importlib.metadata import version

from media_ingestion.pipeline import hash_file
from transcription.checkpoints import digest
from transcription.inference import measure_resources


class Wav2Vec2Reference:
    def __init__(self, snapshot, threads=4):
        import torch
        from transformers import AutoModelForCTC, AutoProcessor

        self.torch = torch
        torch.set_num_threads(threads)
        self.provenance = {"model": "facebook/wav2vec2-base-960h", "revision": snapshot.name,
                           "files_sha256": {p.name: hash_file(p) for p in sorted(snapshot.iterdir()) if p.is_file()},
                           "versions": {name: version(name) for name in ("torch", "transformers", "numpy")},
                           "device": "cpu", "threads": threads, "stride_seconds": 2, "chunk_seconds": 20}
        self.configuration_sha256 = digest(self.provenance)
        with measure_resources() as self.initialization:
            self.processor = AutoProcessor.from_pretrained(snapshot, local_files_only=True)
            self.model = AutoModelForCTC.from_pretrained(snapshot, local_files_only=True, use_safetensors=True).eval()

    def transcribe(self, audio, *, metadata=None, expected_sha256=None):
        import numpy as np

        sha = hash_file(audio)
        if sha != expected_sha256:
            raise ValueError("Audio checksum does not match the clip inventory")
        with measure_resources() as inference, wave.open(str(audio), "rb") as wav:
            rate = wav.getframerate()
            if (rate, wav.getsampwidth(), wav.getnchannels()) != (16000, 2, 1):
                raise ValueError("Independent reference requires mono 16 kHz PCM16 audio")
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
            ratio = self.model.config.inputs_to_logits_ratio
            stride = 2 * rate
            ids = []
            with self.torch.inference_mode():
                for start in range(0, len(samples), 16 * rate):
                    end = min(start + 16 * rate, len(samples))
                    left, right = max(0, start - stride), min(len(samples), end + stride)
                    inputs = self.processor(samples[left:right], sampling_rate=rate, return_tensors="pt")
                    logits = self.model(**inputs).logits[0]
                    lo = (start - left) // ratio
                    hi = (end - left) // ratio if end < len(samples) else len(logits)
                    ids.extend(logits[lo:hi].argmax(dim=-1).tolist())
            text = self.processor.decode(ids).strip()
        return {"audio_sha256": sha, "metadata": metadata or {}, "text": text,
                "audio_duration_seconds": len(samples) / rate, "reference": self.provenance,
                "configuration_sha256": self.configuration_sha256, "model_initialization": self.initialization,
                "inference": inference, "human_reviewed": False}
