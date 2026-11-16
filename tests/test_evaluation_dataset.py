import csv
import json
import tempfile
import unittest
import wave
from pathlib import Path

from evaluation.dataset import materialize, plan_dataset
from media_ingestion.pipeline import hash_file, hash_text


class EvaluationDatasetTests(unittest.TestCase):
    def test_seeded_split_and_cache_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "recordings.csv"
            fields = ("recording_id", "church_id", "church_number", "title", "review_flags",
                      "audio_urls", "video_urls", "stream_urls", "player_urls")
            with path.open("w") as handle:
                out = csv.DictWriter(handle, fields)
                out.writeheader()
                for church in ("1", "2"):
                    for sermon in range(3):
                        key = f"{church}-{sermon}"
                        out.writerow(dict(recording_id=key, church_id=church, church_number=church,
                                          title=f"Sermon {sermon}", review_flags="[]",
                                          audio_urls=json.dumps([f"https://example.org/{key}.mp3"])))
            settings = dict(seed=42, churches=2, clips_per_church=2, development_per_church=1,
                            start_seconds=1, duration_seconds=1)
            plan = plan_dataset(path, settings, ["1-0"])
            self.assertEqual(plan, plan_dataset(path, settings, ["1-0"]))
            rows = plan["clips"]
            self.assertEqual(len({row["recording_id"] for row in rows}), 4)
            self.assertEqual(sum(row["subset"] == "development" for row in rows), 2)
            self.assertEqual(rows[0]["recording_id"], "1-0")
            self.assertEqual(rows[0]["subset"], "development")
            wav = root / "test.wav"
            with wave.open(str(wav), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\x00\x00" * 32000)
            cache = [dict(recording_id="1-0", audio_file=str(wav), sha256=hash_file(wav),
                          reference_sha256=hash_text("https://example.org/1-0.mp3"))]
            result = materialize(plan, cache, root)
            self.assertEqual(result["coverage"]["statuses"], {"ready": 1, "missing_local_audio": 3})
            self.assertEqual(result["clips"][0]["sample_count"], 16000)
            with wave.open(str(root / result["clips"][0]["clip_file"]), "rb") as handle:
                self.assertEqual(handle.getnframes(), 16000)
            cache[0]["reference_sha256"] = hash_text("https://example.org/other.mp3")
            self.assertEqual(materialize(plan, cache, root)["clips"][0]["status"], "source_reference_mismatch")
            plan["target_clips"] = 999
            with self.assertRaisesRegex(ValueError, "Plan checksum"):
                materialize(plan, cache, root)
