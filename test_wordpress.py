import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from collectors.schema import write_results
from collectors.wordpress_churches import (
    collect_branch_cove, collect_gateway, collect_pickens, collect_vernon,
)
from collectors.registry import CHURCH_BY_KEY
from collectors.wordpress import WordPressCollection, WordPressSermon
from sermon_collectors import WORDPRESS_COLLECTORS, collect_church


class WordPressTests(unittest.TestCase):
    def test_registered_collectors(self):
        self.assertEqual(WORDPRESS_COLLECTORS, {
            "gateway_church_international": collect_gateway,
            "christ_church_branch_cove": collect_branch_cove,
            "vernon_church_of_christ": collect_vernon,
            "pickens_baptist_association": collect_pickens,
        })
        self.assertTrue(all(CHURCH_BY_KEY[key].status == "active" for key in WORDPRESS_COLLECTORS))

    def test_dispatch_keeps_registry_identity_in_wordpress_results(self):
        church = CHURCH_BY_KEY["vernon_church_of_christ"]
        row = WordPressSermon("1", "https://example.org/sermons/test/")
        expected = WordPressCollection(sermons={"1": row})
        with patch.dict(WORDPRESS_COLLECTORS, {church.key: lambda url: expected}):
            actual = collect_church(church)
        self.assertEqual(actual.sermons["1"].place_id, church.place_id)
        self.assertEqual(actual.sermons["1"].church_name, church.name)

    def test_gateway_first_playlist_and_metadata_export(self):
        archive = "https://example.org/services/"
        tracks = {"tracks": [
            {"title": "October 3rd, 2024 Pastor A &amp; B",
             "src": "/audio.mp3?sig=a%2Bb",
             "meta": {"date": "October 4, 2024", "length_formatted": "1:02:03"}},
            {"title": "2024-10-04 Song (Speaker)", "meta": {"date": "Unknown", "length_formatted": "4:15"}},
        ]}
        html = f'<script class="wp-playlist-script">{json.dumps(tracks)}</script>'
        html += '<script class="wp-playlist-script">invalid ignored block</script>'
        result = collect_gateway(archive, fetch=lambda url: (url, html))
        first, second = result.sermons.values()
        self.assertEqual((first.title, first.date, first.speaker, first.duration_sec),
                         ("October 3rd, 2024 Pastor A & B", "2024-10-03", "A & B", 3723))
        self.assertEqual(first.media_urls, ["https://example.org/audio.mp3?sig=a%2Bb"])
        self.assertIn("posted date differs from title date", first.data_note)
        self.assertEqual((second.speaker, second.duration_sec, second.media_urls), ("Speaker", 255, []))
        self.assertIn("posted date format unparsed", second.data_note)
        with TemporaryDirectory() as root:
            write_results(Path(root), "gateway", result)
            with (Path(root) / "gateway.csv").open() as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["duration_sec"], "3723")
            self.assertEqual(row["data_note"], first.data_note)

    def test_branch_cove_follows_dated_sermon_posts_and_preserves_each_audio(self):
        archive = "https://example.org/posts/"
        page_two = archive + "page/2/"
        detail = "https://example.org/2024/05/01/message/"
        missing = "https://example.org/2024/05/02/another/"
        pages = {
            archive: f'<a href="{detail}">Sermon May</a><a href="{page_two}">Next</a>'
                     '<a href="/2024/05/01/news/">News</a>',
            page_two: f'<a href="{detail}">Sermon duplicate</a><a href="{missing}">Sermon without media</a>',
            detail: """<article><h1>Sermon 2024-05-01</h1><div class="entry-content">
                <p>Elder Jane Doe</p><audio><source src="/extensionless"></audio>
                <a href="/second.m4a">Audio</a><a href="/second.m4a">Same audio</a>
                </div><a href="/unrelated.mp3">Outside content</a></article>""",
            missing: "<article><h1>Sermon 2024-05-02</h1></article>",
        }
        result = collect_branch_cove(archive, fetch=lambda url: (url, pages[url]), workers=1)
        rows = list(result.sermons.values())
        self.assertEqual([r.media_urls for r in rows], [
            ["https://example.org/extensionless"], ["https://example.org/second.m4a"], [],
        ])
        self.assertEqual([r.date for r in rows], ["2024-05-01", "2024-05-01", "2024-05-02"])
        self.assertEqual(rows[0].speaker, "Jane Doe")
        self.assertIn("no direct audio link", rows[-1].data_note)

    def test_vernon_pagination_date_precedence_and_duplicate_media_across_posts(self):
        archive = "https://example.org/sermons/"
        p2 = archive + "page/2/"
        first, second = archive + "one/", archive + "two/"
        html = '<audio src="/same.mp3"></audio><audio src="/ignored"></audio>'
        pages = {
            archive: '<h3><a href="/sermons/one/">5-3-24 Message</a></h3>'
                     '<p>May 4, 2024 | by Speaker One</p><a href="?paged=2">Next</a>'
                     '<a href="/sermons/page/2/">Same page</a>',
            p2: '<h3><a href="/sermons/two/">Message</a></h3>',
            first: html, second: "June 1, 2024 | by Speaker Two" + html,
        }
        seen = []

        def fetch(url):
            seen.append(url)
            return url, pages[url]

        result = collect_vernon(archive, fetch=fetch, workers=1)
        self.assertEqual(seen, [archive, p2, first, second])
        one, two = result.sermons.values()
        self.assertEqual((one.date, one.speaker), ("2024-05-03", "Speaker One"))
        self.assertIn("displayed entry date: 2024-05-04", one.data_note)
        self.assertEqual((two.date, two.speaker), ("2024-06-01", "Speaker Two"))
        self.assertEqual(one.media_urls, two.media_urls)

    def test_pickens_scope_and_date_fallbacks(self):
        archive = "https://example.org/sunday-school-online/"
        p2 = archive + "page/2/"
        first, second = "https://example.org/sermons/one/", "https://example.org/sermons/two/"
        pages = {
            archive: '<div class="wpfc-sermon"><a class="wpfc-sermon-title-text" href="/sermons/one/">'
                     'Old title</a><span class="wpfc-sermon-meta-date">May 4, 2024</span></div>'
                     '<a href="/sunday-school-online/page/2/">Next</a>',
            p2: '<div class="wpfc-sermon"><a class="wpfc-sermon-title-text" href="/sermons/two/">Lesson</a></div>',
            first: """<a href="/outside.mp3">Outside</a><div class="wpfc-sermon-single">
                      <h1 class="wpfc-sermon-single-title">New title</h1>
                      <a href="/speakers/example/">Speaker</a><a href="/lesson.wav">Listen</a></div>""",
            second: '<article><span class="wpfc-sermon-single-meta-date">May 5, 2024</span></article>',
        }
        result = collect_pickens(archive, fetch=lambda url: (url, pages[url]), workers=1)
        one, two = result.sermons.values()
        self.assertEqual((one.title, one.date, one.speaker), ("New title", "2024-05-04", "Speaker"))
        self.assertEqual(one.media_urls, ["https://example.org/lesson.wav"])
        self.assertEqual((two.date, two.media_urls), ("2024-05-05", []))
        self.assertIn("Sunday School Online lesson", two.data_note)
