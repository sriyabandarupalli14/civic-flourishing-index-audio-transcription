"""Bounded metadata HTTP and browser transport for the platform adapters."""

import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .shared import is_media


@dataclass(frozen=True)
class MetadataResponse:
    url: str
    text: str
    headers: dict = field(default_factory=dict)

    def json(self):
        return json.loads(self.text)


class MetadataClient(requests.Session):
    """Fetch page and API payloads without reading recording bodies."""

    def __init__(self, *, retry=False, listing_post=False):
        super().__init__()
        self.headers.update({"User-Agent": "Mozilla/5.0",
                             "Accept": "text/html, application/json, text/plain, */*;q=0.1"})
        if retry:
            methods = {"GET", "HEAD", "POST"} if listing_post else {"GET", "HEAD"}
            policy = Retry(total=3, backoff_factor=1, allowed_methods=methods,
                           status_forcelist=[429, 500, 502, 503, 504])
            self.mount("https://", HTTPAdapter(max_retries=policy))

    def request(self, method, url, **kwargs):
        if is_media(url):
            raise ValueError("Recording URLs are outside metadata collection")
        kwargs["stream"] = True
        with super().request(method, url, **kwargs) as response:
            response.raise_for_status()
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if mime not in {"text/html", "text/plain", "application/json",
                            "application/xhtml+xml", "application/javascript", "text/javascript",
                            "text/xml", "application/xml"} and not mime.endswith("+json"):
                raise ValueError(f"Unexpected metadata type: {mime or 'missing'}")
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 12 * 1024 * 1024:
                    raise ValueError("Metadata response exceeded 12 MiB")
                chunks.append(chunk)
            return MetadataResponse(response.url, b"".join(chunks).decode("utf-8-sig", errors="replace"),
                                    {key.lower(): value for key, value in response.headers.items()})


async def metadata_browser_page(browser, **kwargs):
    """Allow browser catalog rendering while blocking recording, iframe and binary traffic."""
    context = await browser.new_context(service_workers="block", **kwargs)

    async def guard(route):
        request = route.request
        if (request.method == "GET" and not is_media(request.url)
                and "range" not in request.headers
                and (request.resource_type in {"script", "stylesheet"}
                     or (request.resource_type == "document" and request.frame.parent_frame is None))):
            await route.continue_()
        else:
            await route.abort()

    await context.route("**/*", guard)
    return await context.new_page()
