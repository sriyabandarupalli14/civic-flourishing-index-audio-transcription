import tempfile
import unittest
import wave
from pathlib import Path

from media_ingestion.pipeline import Recording, hash_file
from transcription.production import stage3_entries, stage_verified_local
from transcription.quality import assess_quality


class ProductionTranscriptionTests(unittest.TestCase):
    def test_handoff_rejects_corrupt_or_held_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            url = "https://example.org/source.wav"
            recording = Recording("rec-1", "church", "1", (url,))
            with wave.open(str(source), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x00\x00" * 32000)
            state = stage_verified_local(recording, source, url, hash_file(source), root / "audio", {})
            entries, excluded = stage3_entries({recording.identifier: recording}, root / "audio")
            self.assertFalse(excluded)
            self.assertEqual(entries[0]["audio_sha256"], state["sha256"])
            Path(entries[0]["audio_file"]).write_bytes(b"corrupt")
            entries, excluded = stage3_entries({recording.identifier: recording}, root / "audio")
            self.assertFalse(entries)
            self.assertEqual(excluded[0]["reason"], "media_checkpoint_mismatch")

    def test_review_flags_leave_speech_unchanged(self):
        segment = {"id": 1, "start_seconds": 3, "end_seconds": 2, "text": "Test.",
                   "avg_logprob": -2, "no_speech_probability": 0.9, "compression_ratio": 2.5}
        quality = assess_quality("Test.", [segment], 4)
        self.assertTrue(quality["needs_review"])
        self.assertEqual(len(quality["segment_flags"][0]["reasons"]), 4)
        self.assertEqual(segment["text"], "Test.")

    def test_retry_caps_overlapping_spans_and_invalid_timestamps(self):
        from transcription.retry_spans import candidate_windows

        quality = {"segment_flags": [
            {"start_seconds": 2, "end_seconds": 1, "reasons": ["invalid_timestamps"]},
            {"start_seconds": 5, "end_seconds": 90, "reasons": ["low_average_log_probability"]},
            {"start_seconds": 12, "end_seconds": 15, "reasons": ["low_average_log_probability"]},
            {"start_seconds": 59, "end_seconds": 65, "reasons": ["low_average_log_probability"]},
        ]}
        self.assertEqual(candidate_windows(quality, 60, 2), [(4, 49), (58, 60)])
