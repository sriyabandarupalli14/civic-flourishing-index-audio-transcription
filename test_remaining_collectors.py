import asyncio
import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from collectors import (boxcast, nextjs, sermoncloud, site_media_api, soundcloud,
                        south_cullman, squarespace, static_audio, subsplash, youtube)
from collectors.metadata import MetadataClient, MetadataResponse
from collectors.remaining import NEW_KEYS
from collectors.registry import CHURCHES, CHURCH_BY_KEY
from collection_outputs import consolidate
from sermon_collectors import collect_church

SOURCE = "https://example.test"
SIGNED = "https://cdn.example.test/recording.mp3?sig=a%2Bb"


class FixtureClient:
    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def get(self, url, **kwargs):
        return self._call("GET", url, kwargs.get("params"))

    def post(self, url, **kwargs):
        return self._call("POST", url, kwargs.get("data"))

    def _call(self, method, url, query):
        self.calls.append((method, url, query))
        value = self.respond(method, url, query)
        return MetadataResponse(url, value if isinstance(value, str) else json.dumps(value))


class Downloader:
    def __init__(self, responses, options):
        self.responses = responses
        self.options = options

    def __enter__(self):
        self.assert_metadata_only()
        return self

    def __exit__(self, *_):
        pass

    def assert_metadata_only(self):
        if not self.options.get("skip_download"):
            raise AssertionError("Expected metadata-only downloader")

    def extract_info(self, url, *, download):
        if download:
            raise AssertionError("A media download was attempted")
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value


class RemainingTests(unittest.TestCase):
    def setUp(self):
        delay = patch("time.sleep")
        delay.start()
        self.addCleanup(delay.stop)

    def test_http_transport_rejects_media_before_consuming_response(self):
        with MetadataClient() as client:
            with patch("requests.Session.request") as request:
                with self.assertRaisesRegex(ValueError, "outside metadata collection"):
                    client.get(SIGNED)
                request.assert_not_called()
                response = request.return_value.__enter__.return_value
                response.headers = {"Content-Type": "audio/mpeg"}
                with self.assertRaisesRegex(ValueError, "Unexpected metadata type"):
                    client.get(SOURCE + "/extensionless")
                response.iter_content.assert_not_called()
                self.assertTrue(request.call_args.kwargs["stream"])

    def test_registry_activates_all_ten(self):
        self.assertEqual(len(NEW_KEYS), 10)
        self.assertTrue(all(CHURCH_BY_KEY[k].status == "active" for k in NEW_KEYS))

    def test_cli_dispatch_and_consolidated_row_preservation(self):
        church = CHURCH_BY_KEY["christian_valley"]
        with patch("sermon_collectors.collect_remaining") as collect:
            collect_church(church)
            collect.assert_called_once_with(church, Path("outputs/sermon_collection"))
        with TemporaryDirectory() as root:
            target = Path(root)
            for item in CHURCHES:
                if item.status != "active":
                    continue
                (target / f"{item.key}.csv").write_text("source_id,title\n1,same\n1,same\n")
                (target / f"{item.key}_summary.json").write_text('{"records": 2}')
            summary = consolidate(target)
            self.assertEqual((summary["churches"], summary["records"]), (18, 36))
            with (target / "combined_stage1_first20.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0], rows[1])
            self.assertNotIn("8", {row["church_number"] for row in rows})

    def test_wix_public_component_ids(self):
        html = '<link id="features_video" href="https://siteassets.parastorage.com/data">'
        payload = json.dumps({"components": [{"videoType": "YouTube", "videoId": "abcdefghijk"}, "https://www.youtube.com/live/BCDEFGHIJKL?x=y"]})
        self.assertEqual(youtube.wix_youtube_ids(
            html, SOURCE, lambda url: MetadataResponse(url, payload)), ["abcdefghijk", "BCDEFGHIJKL"])

    def test_youtube_fallback_embed_and_failed_metadata(self):
        async def render(url):
            return "" if url == SOURCE else "youtube.com/embed/abcdefghijk " * 2
        watch = "https://www.youtube.com/watch?v=abcdefghijk"
        result = asyncio.run(youtube.collect_youtube(
            SOURCE, fallback_urls=(SOURCE + "/new",), render=render,
            downloader=lambda opts: Downloader({watch: ValueError()}, opts),
        ))
        row, = result.sermons.values()
        self.assertEqual(row.video_url, watch)
        self.assertIn("metadata unavailable", row.data_note)

    def test_soundcloud_listing_and_detail_fallback(self):
        track, other = SOURCE + "/track", SOURCE + "/other"
        inputs = {
            SOURCE: {"entries": [{"title": "Hope (Jane Doe)", "url": track},
                                  {"title": "Faith", "url": other}]},
            track: {"webpage_url": track, "upload_date": "20241003", "duration": 19.6},
            other: ValueError(),
        }
        result = soundcloud.collect_soundcloud(SOURCE, downloader=lambda opts: Downloader(inputs, opts))
        first, second = result.sermons.values()
        self.assertEqual((first.speaker, first.date, first.duration_sec), ("Jane Doe", "2024-10-03", 20))
        self.assertEqual(second.media_urls, [other])
        self.assertIn("details unavailable", second.data_note)

    def test_south_cullman_resume_merge_and_date_conflict(self):
        raw = pd.DataFrame([{"audio_url": SIGNED, "sermon_title": "Hope...", "speaker": "Jane",
                             "date_card": "10/4/24", "date_file": "20241003", "tags": "Sun AM | X", "page": 2}])
        final = pd.DataFrame([{"audio_url": SIGNED, "sermon_title": "Full title"}])
        merged = south_cullman.merge_south_checkpoint(final, raw)
        row, = south_cullman.checkpoint_result(raw, SOURCE).sermons.values()
        self.assertEqual(merged.loc[0, "sermon_title_checkpoint"], "Hope...")
        self.assertEqual(row.source_fields["service_type"], "Sun AM")
        self.assertIn("date conflict", row.data_note)
        self.assertEqual(row.audio_url, SIGNED)

    def test_south_cullman_resumes_and_leaves_checkpoint_on_page_error(self):
        with TemporaryDirectory() as root:
            checkpoint = Path(root) / "cards.csv"
            checkpoint.write_text("audio_url,sermon_title,speaker,date_card,tags,date_file,page\n"
                                  + SIGNED + ",Old,Jane,10/4/24,Sun AM,20241004,2\n")
            driver = MagicMock()
            driver.__aenter__.return_value.chromium.launch = AsyncMock(return_value=AsyncMock())
            loader = AsyncMock(return_value=(503, []))
            with patch("collectors.south_cullman.async_playwright", return_value=driver), \
                    patch("collectors.south_cullman.metadata_browser_page", new_callable=AsyncMock), \
                    patch("collectors.south_cullman.load_page", loader):
                with self.assertRaisesRegex(RuntimeError, "Page 3 failed"):
                    asyncio.run(south_cullman.collect_cards_raw(SOURCE, checkpoint))
            self.assertEqual(loader.call_args.args[1], SOURCE + "?c_page=3")
            self.assertIn(SIGNED, checkpoint.read_text())

    def test_redeeming_grace_filename_and_signed_url(self):
        html = '<p><a href="/dont_quit.mp3?sig=a%2Bb">Message by Dr. Steele_dont_quit.mp3</a></p>'
        result = static_audio.collect_redeeming_grace(SOURCE, "id", "Church", session=FixtureClient(lambda *_: html))
        row, = result.sermons.values()
        self.assertEqual((row.title, row.speaker, row.date), ("dont quit", "Dr. Steele", ""))
        self.assertTrue(row.audio_url.endswith("?sig=a%2Bb"))

    def test_nextjs_only_messages_and_keeps_missing_media(self):
        message = {"title": "Talk", "slug": "10-03-2024", "speaker": "Jane",
                   "urls": [{"type": "message", "id": 1, "length": "12:03", "mp3_url": SIGNED},
                            {"type": "message", "id": 2},
                            {"type": "worship", "id": 3, "mp3_url": "https://example.test/song.mp3"}]}
        html = "<script>self.__next_f.push(" + json.dumps([1, json.dumps(message)]) + ")</script>"
        result = nextjs.collect_highlands(SOURCE + "/media", "id", "Church",
                                         session=FixtureClient(lambda *_: html), page_limit=1)
        self.assertEqual(len(result.sermons), 2)
        self.assertEqual([r.media_urls for r in result.sermons.values()], [[SIGNED], []])
        self.assertEqual(next(iter(result.sermons.values())).duration_sec, 723)

    def test_boxcast_repeated_pagination_and_status_filter(self):
        def fixture(method, url, query):
            if url.endswith("/broadcasts"):
                return [{"id": "a", "name": "Talk | Pastor Jane", "starts_at": "2024-10-04T02:00:00Z"},
                        {"id": "b", "name": "Live"}]
            return {"status": "recorded", "playlist": SIGNED} if "/a/" in url else {"status": "live"}
        result = boxcast.collect_grace_montgomery(SOURCE, "id", "Church", session=FixtureClient(fixture))
        row, = result.sermons.values()
        self.assertEqual((row.date, row.speaker, row.video_url), ("2024-10-03", "Jane", SIGNED))
        self.assertTrue(result.coverage[0]["pagination_repeated"])

    def test_boxcast_header_recovers_broadcasts_after_repeated_listing(self):
        with patch.object(FixtureClient, "get") as getter:
            def payload(url, *, params, **kwargs):
                if url.endswith("/broadcasts"):
                    items = [{"id": "one"}]
                    if params.get("l") == 100:
                        items.append({"id": "two"})
                        return MetadataResponse(url, json.dumps(items), {"x-pagination": "{}"})
                    return MetadataResponse(url, json.dumps(items))
                return MetadataResponse(url, json.dumps({"status": "recorded", "playlist": SIGNED}))
            getter.side_effect = payload
            result = boxcast.collect_grace_montgomery(
                SOURCE, "id", "Church", session=FixtureClient(None), recover_pagination=True,
            )
        self.assertEqual(set(result.sermons), {"one", "two"})
        self.assertTrue(result.coverage[0]["header_pagination_completed"])

    def test_subsplash_pagination_format_precedence_and_missing_item(self):
        def fixture(method, url, query):
            if method == "POST":
                item = str(query["page"])
                return {"total": 2, "_embedded": {"media-items": [{"id": item, "app_key": "CRCXMM"}]}}
            media = [{"format": "m3u8", "url": "https://cdn.example.test/a.m3u8"},
                     {"format": "mp4", "url": "https://cdn.example.test/a.mp4"},
                     {"format": "mp3", "url": SIGNED}] if url.endswith("/1") else []
            return {"media": media, "title": "Fallback"}
        result = subsplash.collect_amazing_grace(SOURCE, "id", "Church", session=FixtureClient(fixture))
        one, two = result.sermons.values()
        self.assertEqual(one.media_urls, [SIGNED, "https://cdn.example.test/a.mp4"])
        self.assertEqual(two.media_urls, [])

    def test_custom_api_literal_list_player_directory_and_durations(self):
        def fixture(method, url, query):
            if query["cmd"] == "getlist":
                return repr({"total": 2, "list": [
                    {"IDENTIFIER": "1", "TITLE": "Hope - Jane", "T1": "a b.mp3", "DATE": 1728000000000, "DURATION": 120000},
                    {"IDENTIFIER": "2", "TITLE": "Faith"},
                ]})
            return "play.url:https://cdn.example.test/pdir/171/a%20b.mp3;"
        result = site_media_api.collect_victory_baptist(SOURCE, "id", "Church", session=FixtureClient(fixture))
        first, second = result.sermons.values()
        self.assertEqual((first.audio_url, first.speaker, first.duration_sec),
                         ("https://cdn.example.test/pdir/171/a%20b.mp3", "Jane", 120))
        self.assertEqual(second.media_urls, [])

    def test_squarespace_discovery_and_duplicate_provenance(self):
        index = SOURCE + "/sermons"
        player = f'<div class="sqs-audio-embed" data-asset-url="{SIGNED}" data-author="Jane" data-length-in-milli-seconds="23000"></div>'
        client = FixtureClient(lambda method, url, query: player if url != index else
                               '<header><a href="/about">About</a></header><main><a href="/study">Study</a></main>')
        result = squarespace.collect_gadsden_city(index, "id", "Church", session=client)
        row, = result.sermons.values()
        self.assertEqual((row.audio_url, row.duration_sec), (SIGNED, 23))
        self.assertIn("also listed at", row.data_note)
        self.assertNotIn(SOURCE + "/about", [call[1] for call in client.calls])

    def test_sermoncloud_totals_and_media_missing(self):
        def fixture(method, url, query):
            page = query["page"]
            return {"total": 2, "last_page": 2, "data": [{
                "id": page, "date": {"date": "October 3, 2024"}, "preacher": " Jane  Doe ",
                "media": {"audio": SIGNED} if page == 1 else {},
            }]}
        result = sermoncloud.collect_covenant_life(SOURCE, "id", "Church", session=FixtureClient(fixture))
        first, second = result.sermons.values()
        self.assertEqual((first.date, first.speaker, first.audio_url), ("2024-10-03", "Jane Doe", SIGNED))
        self.assertEqual(second.media_urls, [])
