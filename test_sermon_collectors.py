import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from collectors.registry import CHURCHES, CHURCH_BY_KEY
from collectors.schema import Collection, Sermon, write_results
from collectors.shared import public_url
from sermon_collectors import collect_church, collect_prattville, collect_sermonnet, fetch_metadata


class CollectorTests(unittest.TestCase):
    def test_sermonnet_paginates_and_deduplicates_public_postings(self):
        archive = "https://example.org/archive"
        playlists = [
            {"id": pid, "countPostings": count, "mediaCenterId": 4, "title": title}
            for pid, count, title in [(1, 3, "Main"), (2, 1, "Events"), (3, 1, "Private")]
        ]
        playlists[-1]["is_private"] = True
        row = {
            "id": 11, "sharedLink": "https://example.org/episode/11", "title": "Hope",
            "posted": 1704153600, "speaker": {"name": "Speaker"},
            "mediaData": {"src": {"audio": {"download": "https://cdn.example.org/11.mp3?sig=a"}}},
        }
        live = dict(row, id=12, isLive=True)
        no_media = dict(row, id=13, sharedLink="https://example.org/episode/13", mediaData={})
        pages = {(1, 1): [row, live], (1, 2): [no_media], (2, 1): [row]}
        calls = []

        def fetch(url):
            if url == archive:
                return url, "var StudioAppData = " + json.dumps({"playlists": playlists})
            params = parse_qs(urlsplit(url).query)
            key = tuple(int(params[name][0]) for name in ("playlistId", "offset"))
            calls.append(key)
            return url, json.dumps({"data": [{"collection": pages[key]}]})

        result = collect_sermonnet(archive, fetch)
        self.assertEqual(calls, [(1, 1), (1, 2), (2, 1)])
        self.assertEqual(set(result.sermons), {"11", "13"})
        self.assertEqual(result.sermons["11"].playlists, ["Main", "Events"])
        self.assertEqual(result.sermons["11"].date, "2024-01-01")
        self.assertEqual(result.sermons["11"].speaker, "Speaker")
        self.assertEqual(result.sermons["11"].media_urls, ["https://cdn.example.org/11.mp3?sig=a"])
        self.assertEqual(result.sermons["13"].media_urls, [])
        self.assertFalse(result.limitations)

    def test_prattville_keeps_missing_media_and_never_fetches_download_endpoint(self):
        archive = "https://example.org/media/sermons/"
        post = "https://example.org/sermons/hope/"
        second = "https://example.org/media/sermons/page/2/"
        download = post + "?download_mp3=mp3"
        pages = {
            archive: f"""<li id="sermon_1"><span class="title">Hope</span>
                <span class="date">Jan 1, 2024</span><div class="preacher">By: Speaker</div>
                <input class="mp3" value="undefined"><a class="download_btn" href="{download}"></a>
                </li><a rel="next" href="{second}">Next</a>""",
            second: """<li id="sermon_2"><span class="title">Faith</span>
                <a class="download_btn" href="/recording.mp3"></a></li>""",
            post: '<article><audio src="/media/hope.mp3"></audio></article>',
        }
        calls = []

        def fetch(url):
            calls.append(url)
            return url, pages[url]

        result = collect_prattville(archive, fetch)
        self.assertEqual(calls, [archive, second, post])
        self.assertEqual(result.sermons["1"].sermon_url, post)
        self.assertEqual(result.sermons["1"].speaker, "Speaker")
        self.assertEqual(result.sermons["1"].date, "Jan 1, 2024")
        self.assertEqual(result.sermons["1"].media_urls, ["https://example.org/media/hope.mp3"])
        self.assertEqual(result.sermons["1"].unresolved_urls, [download])
        self.assertEqual(result.sermons["2"].media_urls, ["https://example.org/recording.mp3"])

    def test_metadata_fetch_rejects_recordings_before_request(self):
        with patch("collectors.shared.requests.get") as get:
            for url in ("https://example.org/recording.mp3",
                        "https://example.org/sermons/hope/?download_mp3=mp3"):
                with self.assertRaises(ValueError):
                    fetch_metadata(url)
            get.assert_not_called()


    def test_registry_scope_and_dispatch(self):
        self.assertEqual([church.number for church in CHURCHES], list(range(1, 21)))
        self.assertEqual(len({church.place_id for church in CHURCHES}), 20)
        self.assertEqual(len(CHURCH_BY_KEY), 20)
        self.assertEqual([c.number for c in CHURCHES if c.status == "active"], [n for n in range(1, 21) if n not in (8, 18)])
        self.assertEqual([c.number for c in CHURCHES if c.status == "excluded"], [8, 18])
        self.assertEqual(sum(c.status == "pending_migration" for c in CHURCHES), 0)
        grace = CHURCH_BY_KEY["grace_fellowship"]
        self.assertNotEqual(grace.original_archive_url, grace.archive_url)
        with patch("collectors.sermonnet.collect_sermonnet") as sermonnet:
            collect_church(grace)
            sermonnet.assert_called_once_with(grace.archive_url, timezone="America/Chicago")
        prattville = CHURCH_BY_KEY["prattville_community"]
        with patch("collectors.sermon_browser.collect_sermon_browser") as browser:
            collect_church(prattville)
            browser.assert_called_once_with(prattville.archive_url)
        for key, parameter, limit, dynamic in (
            ("mt_pisgah", "query-3-page", 15, True),
            ("first_union_springs_sda", None, None, False),
        ):
            church = CHURCH_BY_KEY[key]
            with patch("collectors.adventist.collect_adventist") as adventist:
                collect_church(church)
                adventist.assert_called_once_with(
                    church.archive_url, page_parameter=parameter, max_pages=limit,
                    dynamic_player=dynamic,
                )
        with self.assertRaises(ValueError):
            collect_church(CHURCHES[7])

    def test_shared_urls_preserve_signed_queries(self):
        self.assertEqual(
            public_url("https://example.org/archive/", "../song.mp3?sig=abc%2Bdef#media"),
            "https://example.org/song.mp3?sig=abc%2Bdef",
        )
        for value in ("undefined", "javascript:alert(1)", "https://user:pass@example.org/a"):
            self.assertEqual(public_url("https://example.org/", value), "")

    def test_shared_export_preserves_legacy_fields_and_unresolved_references(self):
        rows = {
            "1": Sermon("1", "https://example.org/one", title="Hope",
                        media_urls=["https://example.org/audio.mp3"], playlists=["Main"]),
            "2": Sermon("2", "https://example.org/two",
                        unresolved_urls=["https://example.org/two?download_mp3=mp3"]),
        }
        with TemporaryDirectory() as root:
            path = Path(root)
            summary = write_results(path, "example", Collection(sermons=rows))
            with (path / "example.csv").open() as handle:
                csv_rows = csv.DictReader(handle)
                self.assertEqual(csv_rows.fieldnames, [
                    "source_id", "sermon_url", "title", "date", "date_basis", "speaker",
                    "media_urls", "unresolved_urls", "playlists",
                ])
                first, second = list(csv_rows)
            self.assertEqual(json.loads(first["playlists"]), ["Main"])
            self.assertEqual(json.loads(second["unresolved_urls"]), rows["2"].unresolved_urls)
            self.assertEqual(summary["records_with_media"], 1)
            self.assertEqual(summary["records_with_unresolved_links"], 1)
            self.assertEqual(json.loads((path / "example_summary.json").read_text()), summary)


if __name__ == "__main__":
    unittest.main()
