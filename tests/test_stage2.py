import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from processing.pipeline import reconcile
from processing.grouping import group_observations, manifest_record
from processing.normalize import exact_payload, normalize, normalize_date, normalize_duration

AUDIO = "https://media.example.org/one.mp3?sig=a%2Bb"
VIDEO = "https://media.example.org/one.mp4"
OTHER = "https://media.example.org/two.mp3"


def row(**changes):
    return {"registry_place_id": "church-a", "church_number": "1", "title": "Hope",
            "media_urls": "[]", **changes}


def groups(*rows):
    return [manifest_record(group) for group in group_observations(
        [normalize(source, index) for index, source in enumerate(rows, 1)]
    )]


class Stage2Tests(unittest.TestCase):
    def test_dates_durations_missing_values_and_signed_reference(self):
        item = normalize(row(title="  Hope   Today ", speaker="N/A", sermon_date="Jan 2, 2026",
                             duration_sec="1:02.5", audio_url=AUDIO), 1)
        self.assertEqual((item.title, item.speaker, item.date, item.duration),
                         ("Hope Today", "", "2026-01-02", "62.5"))
        self.assertEqual(item.refs, {AUDIO: "audio_file"})
        self.assertEqual(normalize_date("Fri, 02 Jan 2026 00:30:00 +0000"), "2026-01-02")
        self.assertEqual(normalize_duration("1:75"), "")

    def test_exact_payload_keeps_distinct_native_ids_and_missing_id_rows(self):
        one = row(audio_url=AUDIO, source_id="1")
        two = row(audio_url=AUDIO, source_id="2")
        self.assertEqual(exact_payload(one, normalize(one, 1)), exact_payload(two, normalize(two, 2)))
        for r, native in ((one, "first"), (two, "second")):
            r.update(platform="subsplash", platform_scope="item", platform_recording_id=native)
        self.assertNotEqual(exact_payload(one, normalize(one, 1)), exact_payload(two, normalize(two, 2)))
        no_media = row(source_id="one")
        other = row(source_id="two")
        self.assertNotEqual(exact_payload(no_media, normalize(no_media, 1)),
                            exact_payload(other, normalize(other, 2)))

    def test_platform_aliases_group_without_losing_alternative_urls(self):
        a = "https://www.youtube.com/watch?v=abcdefghijk"
        b = "https://youtu.be/abcdefghijk?si=value"
        group, = groups(row(video_url=a, audio_url=AUDIO), row(video_url=b, media_url=VIDEO))
        self.assertEqual(group["player_urls"], sorted([a, b]))
        self.assertEqual(group["audio_urls"], [AUDIO])
        self.assertEqual(group["video_urls"], [VIDEO])
        self.assertEqual(group["deduplicated_observation_count"], 2)

    def test_one_exact_url_attaches_to_a_single_platform_identity(self):
        identified = row(audio_url=AUDIO, platform="subsplash", platform_scope="item", platform_recording_id="one")
        group, = groups(identified, row(audio_url=AUDIO, video_url=VIDEO))
        self.assertIn("unambiguous_exact_url", group["grouping_basis"])
        self.assertEqual(group["video_urls"], [VIDEO])

    def test_exact_url_transitive_bridge_cannot_merge_different_platform_ids(self):
        found = groups(
            row(audio_url=AUDIO, platform="subsplash", platform_scope="item", platform_recording_id="one"),
            row(audio_url=OTHER, platform="subsplash", platform_scope="item", platform_recording_id="two"),
            row(media_urls=json.dumps([AUDIO, VIDEO])), row(media_urls=json.dumps([VIDEO, OTHER])),
        )
        self.assertEqual(len(found), 4)
        self.assertTrue(all("ambiguous_exact_url_bridge" in r["review_flags"] for r in found))

    def test_shared_url_with_disagreeing_metadata_stays_separate(self):
        found = groups(row(audio_url=AUDIO, date="2026-01-02"), row(audio_url=AUDIO, date="2026-01-04"))
        self.assertEqual(len(found), 2)
        self.assertTrue(all("conflicting_metadata_on_exact_url" in r["review_flags"] for r in found))

    def test_title_only_matches_and_query_variants_are_reviewed_without_merging(self):
        alternate = AUDIO.replace("a%2Bb", "different")
        found = groups(row(audio_url=AUDIO, date="2026-01-02"), row(audio_url=alternate, date="2026-01-02"))
        self.assertEqual(len(found), 2)
        for item in found:
            self.assertIn("query_variant_reference_review", item["review_flags"])
            self.assertIn("same_title_and_date_separate_groups", item["review_flags"])

    def test_cross_church_ids_and_inactive_archive_remain_separate(self):
        watch = "https://www.youtube.com/watch?v=abcdefghijk"
        found = groups(row(video_url=watch), row(registry_place_id="church-b", church_number="8",
                                              collection_origin="november_archive_only_inactive_church", video_url=watch))
        self.assertEqual(len(found), 2)
        self.assertTrue(all("cross_church_platform_id" in r["review_flags"] for r in found))
        self.assertIn("inactive_church_archive", found[1]["review_flags"])

    def test_series_engine_message_id_groups_companion_asset_without_treating_it_as_media(self):
        page = "https://hopecommunitypca.com/sermons/?enmse=1&enmse_mid=8192&enmse_av=1"
        library = "https://player.vimeo.com/api/player.js"
        item, = groups(row(sermon_url=page, media_url=library), row(sermon_url=page, audio_url=AUDIO))
        self.assertEqual(item["platform_identifier"], ["series_engine", "hopecommunitypca.com", "8192"])
        self.assertEqual(item["non_media_urls"], [library])
        self.assertEqual(item["audio_urls"], [AUDIO])
        self.assertEqual(item["unresolved_urls"], [])

    def test_shared_stream_requires_a_broadcast_identity(self):
        stream = "https://media.example.org/live.m3u8"
        found = groups(row(video_url=stream), row(video_url=stream))
        self.assertEqual(len(found), 2)
        self.assertTrue(all(r["media_state"] == "with_media_reference" for r in found))

    def test_lineage_and_output_partitions_reconcile(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "stage1"
            original.mkdir()
            path = original / "input.csv"
            output = root / "stage2"
            rows = [
                row(audio_url=AUDIO, source_id="a"), row(audio_url=AUDIO, source_id="b"),
                row(title="Missing"), row(title="Unresolved", unresolved_urls='["https://example.org/?download_mp3=1"]'),
            ]
            headers = sorted({key for item in rows for key in item})
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)
            before = path.read_bytes()
            with patch("requests.sessions.Session.request", side_effect=AssertionError("network attempted")):
                report = reconcile(path, output)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual((report["input_observations"], report["exact_payload_duplicates_removed"],
                              report["groups_with_media_reference"], report["groups_without_media_reference"]),
                             (4, 1, 1, 2))
            with (output / "observation_lineage.csv").open() as handle:
                lineage = list(csv.DictReader(handle))
            self.assertEqual(len(lineage), 4)
            self.assertEqual(lineage[0]["recording_id"], lineage[1]["recording_id"])
            self.assertEqual(lineage[1]["exact_duplicate"], "true")
            self.assertEqual(lineage[1]["source_id"], "b")
            with self.assertRaisesRegex(ValueError, "separate output"):
                reconcile(path, original / "would-write")
