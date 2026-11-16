import json
import tempfile
import unittest
from pathlib import Path

from transcription.config import read_config
from transcription.inference import measure_resources
from transcription.quality import repetition_flags


class WhisperInfrastructureTests(unittest.TestCase):
    def test_config_preserves_static_and_unprompted_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.with_name("words.json").write_text(json.dumps({
                "initial_prompt": "Sermon.", "hotwords": ["Good Shepherd", "Pentecost"],
            }))
            base = '[model]\nname = "medium.en"\n[hardware]\ndevice = "cpu"\n[decoding]\nbeam_size = 5\n'
            path.write_text(base)
            baseline = read_config(path)
            path.write_text(base + '[prompt]\nmode = "static"\nvocabulary = "words.json"\n')
            prompted = read_config(path)
        self.assertIsNone(baseline["decoding"]["hotwords"])
        self.assertEqual(prompted["decoding"]["hotwords"], "Good Shepherd, Pentecost")
        self.assertEqual(prompted["decoding"]["beam_size"], baseline["decoding"]["beam_size"])

    def test_resource_statistics_and_legacy_repetition_detection(self):
        with measure_resources() as measured:
            self.assertGreater(measured["rss_start_bytes"], 0)
        self.assertGreaterEqual(measured["rss_peak_sampled_bytes"], measured["rss_end_bytes"])
        self.assertGreaterEqual(measured["wall_seconds"], 0)
        self.assertIn({"phrase": "one two three four", "consecutive_repeats": 4},
                      repetition_flags("one two three four " * 4))

    def test_batch_verifies_audio_and_repairs_corrupt_checkpoint(self):
        from media_ingestion.pipeline import hash_file
        from transcription.batch import run_batch

        class FakeModel:
            configuration_sha256 = "configuration"
            calls = 0

            def transcribe(self, audio, *, metadata, expected_sha256):
                self.calls += 1
                return {"audio_sha256": expected_sha256, "text": "synthetic output"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            audio.write_bytes(b"audio fixture")
            entry = {"recording_id": "fixture", "audio_file": str(audio), "audio_sha256": hash_file(audio)}
            model = FakeModel()

            def run():
                return next(run_batch([entry], lambda: model, root))

            event = run()
            self.assertEqual(event["status"], "completed")
            self.assertEqual(run()["status"], "reused")
            (root / event["checkpoint"]).write_text("{}")
            self.assertEqual(run()["status"], "completed")
            self.assertEqual(model.calls, 2)
            audio.write_bytes(b"changed audio")
            self.assertEqual(run()["reason"], "audio_checksum_mismatch")
