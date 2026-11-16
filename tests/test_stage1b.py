import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bs4 import BeautifulSoup

from collectors.replay import collect, compare_replay, read_rows, write_csv
from collectors.archive_cache import ArchiveCache, MissingCapture, PASS, SPRINT
from collectors.archive_extractors import drive_media, flat_youtube, jwplayer_references, rss_enclosures, wordpress_attachments
from collectors.registry import CHURCHES
from collectors.stage1b_routes import ROUTES, Replay, _static_rows


class Stage1BTests(unittest.TestCase):
    def test_registry_has_a_replay_plan_for_every_new_church(self):
        self.assertEqual(set(ROUTES), set(range(21, 45)))
        self.assertTrue(all(c.status == "archived" for c in CHURCHES[20:]))
        self.assertEqual(len({c.place_id for c in CHURCHES}), 44)
        self.assertNotEqual(CHURCHES[10].place_id, CHURCHES[39].place_id)

    def test_cache_keeps_request_parameters_and_never_fetches_on_a_miss(self):
        with TemporaryDirectory() as name:
            pages = Path(name) / PASS / "pages"
            pages.mkdir(parents=True)
            (pages / "request.json").write_text(json.dumps({
                "status": 200, "requested_url": "https://example.org/archive/?page=2&view=all",
            }))
            (pages / "request.html").write_text("<h1>Captured</h1>")
            cache = ArchiveCache(name)
            self.assertIn("Captured", cache.fetch("https://example.org/archive?view=all&page=2")[1])
            with patch("requests.sessions.Session.request") as network:
                with self.assertRaises(MissingCapture):
                    cache.fetch("https://example.org/archive?page=3")
                network.assert_not_called()

    def test_enclosure_feed_retains_date_basis_playlist_and_signed_query(self):
        xml = """<rss><channel><item><title>Talk</title><pubDate>Fri, 02 Jan 2026</pubDate>
            <enclosure url="https://example.org/1234567/talk.mp3?x=a%2Bb&amp;y=2"/>
            </item></channel></rss>"""
        row, = rss_enclosures(xml, "https://example.org/feed", "playlist")
        self.assertEqual(row["media_url"], "https://example.org/1234567/talk.mp3?x=a%2Bb&y=2")
        self.assertEqual((row["date_meaning"], row["playlist_id"]), ("RSS publication date", "playlist"))

    def test_youtube_channel_parser_preserves_id_and_recording_metadata(self):
        rows = list(flat_youtube({"entries": [
            {"id": "abcdefghijk", "title": "Talk", "upload_date": "20260102", "duration": 30},
            {"id": "invalid", "url": "https://example.org/index"},
        ]}))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["media_url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(rows[0]["sermon_date"], "2026-01-02")
        self.assertEqual(rows[0]["duration_sec"], 30)

    def test_signed_hls_from_viewstate_keeps_the_exact_query(self):
        url = "https://content.jwplatform.com/manifests/Test.m3u8?sig=abc%2Bdef"
        hidden = base64.b64encode(url.encode()).decode()
        soup = BeautifulSoup(f'<h1>The Flow</h1><input type="hidden" value="{hidden}">', "html.parser")
        row, = jwplayer_references(soup, "https://example.org/video.aspx")
        self.assertEqual(row["media_url"], url)
        self.assertEqual(row["media_kind"], "stream_manifest")

    def test_drive_folders_and_wordpress_upload_dates_stay_distinct(self):
        media, = drive_media([["folder", "", "Old", "application/vnd.google-apps.folder"],
                             ["one", "", "Talk", "audio/mpeg"]], "https://drive.google.com/drive/folders/list")
        self.assertEqual(media["media_url"], "https://drive.google.com/file/d/one/view")
        self.assertEqual(media["source_mime_type"], "audio/mpeg")
        item, = wordpress_attachments([{
            "id": 5, "title": {"rendered": "A &amp; B"}, "date": "2026-01-02", "post": 2,
            "source_url": "https://example.org/media.mp4",
        }], "https://example.org/")
        self.assertEqual(item["sermon_title"], "A & B")
        self.assertIn("upload date", item["date_meaning"])
        self.assertIn("requires review", item["content_review"])

    def test_static_replay_retains_cards_without_media_and_skips_related_adventist_metadata(self):
        focus = BeautifulSoup('<div class="wp-block-media-text__content"><h2>Talk</h2></div>', "html.parser")
        row, = _static_rows(focus, "https://example.org/sermons/", 28)
        self.assertEqual((row["sermon_title"], row["media_url"]), ("Talk", ""))
        adventist = BeautifulSoup(
            '<h1>Talk</h1><p>Date</p><p>January 2, 2026</p><p>Presenters</p><p>Speaker</p>'
            '<p>Related Content</p><p>Presenters</p><p>Other</p>'
            '<video src="/one.mp4?sig=a"></video><a href="/two.mp3">Listen</a>', "html.parser")
        rows = list(_static_rows(adventist, "https://example.org/sermons/talk/", 40))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["speaker"] == "Speaker" for r in rows))
        self.assertEqual(rows[0]["media_url"], "https://example.org/one.mp4?sig=a")

    def test_comparison_reports_exact_url_and_field_differences(self):
        original = {"media_url": "https://example.org/a.mp3?sig=1", "sermon_title": "Title"}
        replay = [{**original, "sermon_title": "Other"}, {**original, "media_url": "https://example.org/a.mp3?sig=2"}]
        comparison = compare_replay([original], replay)
        self.assertEqual(comparison["matching_exact_refs"], 1)
        self.assertEqual(comparison["sermon_title_differs_or_absent"], 1)

    def test_merge_keeps_duplicate_rows_and_holds_unrelated_candidates_out(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            reference, output = root / "original", root / "output"
            sprint = reference / SPRINT
            sprint.mkdir(parents=True)
            first20 = root / "first20.csv"
            write_csv(first20, [
                {"registry_place_id": church.place_id, "church_number": church.number, "church_key": church.key}
                for church in CHURCHES[:20] if church.status == "active"
            ])
            target = CHURCHES[21].place_id
            write_csv(sprint / "coverage_after.csv", [
                {"place_id": church.place_id, "observations": 2 if church.place_id == target else 0,
                 "current_status": "partial", "identity_review": "", "evidence_assessment": "",
                 "completeness_boundary": "fixture only"}
                for church in CHURCHES[20:]
            ])
            raw = {"place_id": target, "observation_id": "record", "sermon_title": "Same",
                   "media_url": "https://example.org/a.mp3?sig=1", "source_date_raw": "2026"}
            inactive = CHURCHES[7].place_id
            write_csv(sprint / "combined_review_v1.csv", [raw, raw, {**raw, "place_id": inactive}])
            write_csv(sprint / "excluded_unrelated_observations.csv", [{"place_id": CHURCHES[30].place_id}])
            summary = collect(reference, first20, output, replayer=lambda *_: Replay())
            self.assertEqual((summary["stage1b_records"], summary["stage1b_unrelated_held_out"]), (2, 1))
            self.assertEqual(summary["archived_inactive_church_records"], 1)
            self.assertEqual(len(read_rows(output / "first44_collection_coverage.csv")), 44)
            rows = read_rows(output / "combined_stage1_first44.csv")
            self.assertEqual(rows[-1], rows[-2])
            self.assertEqual(rows[-3]["collection_origin"], "november_archive_only_inactive_church")
            self.assertEqual(rows[-1]["source_date_raw"], "2026")
            self.assertEqual(rows[-1]["media_url"], raw["media_url"])
            with self.assertRaisesRegex(ValueError, "outside"):
                collect(reference, first20, reference / "output")
