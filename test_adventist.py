import unittest
from unittest.mock import Mock

from collectors.adventist import collect_adventist, route_player_request


class AdventistTests(unittest.TestCase):
    archive = "https://example.org/online-sermons/"
    detail = archive + "hope/"
    signed = "https://cdn.example.org/hope.mp4?X-Amz-Signature=a%2Bb&X-Amz-Expires=60"

    def test_linked_catalog_and_labeled_detail(self):
        second = self.archive + "?query-3-page=2"
        pages = {
            self.archive: f'<h2><a href="{self.detail}">Fallback title</a></h2>'
                          f'<a href="{second}&sort=recent">Next</a>',
            second: f'<h3><a href="{self.detail}">Duplicate</a></h3>'
                    f'<a href="{second}">Repeated page</a>',
            self.detail: ('<h1>Hope</h1><p>Date:</p><span>October 3, 2024</span>'
                          '<p>Presenters</p><span>Example Speaker</span>'
                          '<h2>Related Content</h2><p>Presenter</p><span>Other Speaker</span>'
                          '<audio src="/first.mp3"></audio><a href="/second.mp3">Other audio</a>'
                          '<video><source src="' + self.signed.replace("&", "&amp;") + '"></video>'),
        }
        requests = []

        def fetch(url):
            requests.append(url)
            return url, pages[url]

        result = collect_adventist(self.archive, fetch=fetch)
        self.assertEqual(requests, [self.archive, second, self.detail])
        item = result.sermons[self.detail]
        self.assertEqual((item.title, item.date, item.speaker), ("Hope", "2024-10-03", "Example Speaker"))
        self.assertEqual(item.media_urls, ["https://example.org/first.mp3", self.signed])

    def test_numbered_catalog_uses_muted_player_fallback(self):
        page = Mock(url=self.detail)
        page.evaluate.side_effect = ["", None, self.signed]
        pages = {
            self.archive: f'<a href="{self.detail}">Hope</a>',
            self.archive + "?query-3-page=2": f'<a href="{self.detail}">Old entry</a>',
            self.archive + "?query-3-page=3": '<a href="https://other.org/online-sermons/no/">Ignore</a>',
            self.detail: '<h1>Hope</h1><time datetime="2024-10-03T12:00:00-05:00"></time><video/>',
        }

        result = collect_adventist(
            self.archive, page_parameter="query-3-page", max_pages=15, dynamic_player=True,
            fetch=lambda url: (url, pages[url]), player_factory=lambda stack: page,
        )
        self.assertEqual(result.coverage[0]["archive_pages"], 3)
        self.assertEqual(result.sermons[self.detail].date, "2024-10-03")
        self.assertEqual(result.sermons[self.detail].media_urls, [self.signed])
        self.assertIn("video.muted = true", page.evaluate.call_args_list[1].args[0])
        page.wait_for_timeout.assert_called_once_with(4000)

    def test_primary_video_reuses_html_and_never_uses_related_players(self):
        pages = {
            self.archive: f'<a href="{self.detail}">Hope</a>',
            self.archive + "?query-3-page=2": "",
            self.archive + "?query-3-page=3": "",
            self.detail: (f'<video src="{self.signed}"></video>'
                          '<h2>Related Content</h2><video src="/other.mp4"></video>'),
        }
        factory = Mock()
        result = collect_adventist(
            self.archive, page_parameter="query-3-page", max_pages=15, dynamic_player=True,
            fetch=lambda url: (url, pages[url]), player_factory=factory,
        )
        factory.assert_not_called()
        self.assertEqual(result.sermons[self.detail].media_urls, [self.signed])

    def test_player_transport_blocks_media_and_binary_fetches(self):
        for resource, url, headers in (
            ("media", "https://cdn.example.org/extensionless", {}),
            ("script", self.signed, {}),
            ("document", "https://cdn.example.org/extensionless", {"range": "bytes=0-"}),
            ("fetch", "https://cdn.example.org/extensionless", {}),
        ):
            route = Mock()
            route.request.method = "GET"
            route.request.resource_type, route.request.url, route.request.headers = resource, url, headers
            route_player_request(route)
            route.abort.assert_called_once_with()
            route.continue_.assert_not_called()
