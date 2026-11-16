"""CLI and compatibility entry points for the Stage 1 collectors."""

import argparse
import json
import sys
from pathlib import Path

from collectors import adventist, sermon_browser, sermonnet, wordpress_churches
from collectors.registry import CHURCH_BY_KEY
from collectors.remaining import NEW_KEYS, collect_remaining
from collectors.schema import Collection, Sermon, write_results
from collectors.sermon_browser import fetch_sermon_browser_metadata as fetch_metadata

GRACE = CHURCH_BY_KEY["grace_fellowship"]
PRATTVILLE = CHURCH_BY_KEY["prattville_community"]
GRACE_ARCHIVE = GRACE.archive_url
PRATTVILLE_ARCHIVE = PRATTVILLE.archive_url


WORDPRESS_COLLECTORS = {
    "gateway_church_international": wordpress_churches.collect_gateway,
    "christ_church_branch_cove": wordpress_churches.collect_branch_cove,
    "vernon_church_of_christ": wordpress_churches.collect_vernon,
    "pickens_baptist_association": wordpress_churches.collect_pickens,
}

def collect_sermonnet(archive_url=GRACE_ARCHIVE, fetch=sermonnet.fetch_metadata):
    """Collect Grace Fellowship with its configured posting-date timezone."""
    return sermonnet.collect_sermonnet(archive_url, timezone=GRACE.timezone, fetch=fetch)


def collect_prattville(archive_url=PRATTVILLE_ARCHIVE, fetch=fetch_metadata):
    """Collect Prattville through the WordPress Sermon Browser adapter."""
    return sermon_browser.collect_sermon_browser(archive_url, fetch=fetch)


def collect_church(church, *, output=Path("outputs/sermon_collection")):
    if church.status != "active":
        raise ValueError(f"{church.key} is {church.status}")
    if church.platform == "sermonnet":
        return sermonnet.collect_sermonnet(church.archive_url, timezone=church.timezone)
    if church.platform == "sermon_browser":
        return sermon_browser.collect_sermon_browser(church.archive_url)
    if church.platform == "adventist":
        return adventist.collect_adventist(
            church.archive_url,
            page_parameter=church.page_parameter,
            max_pages=church.max_pages,
            dynamic_player=church.dynamic_player,
        )
    if church.key in WORDPRESS_COLLECTORS:
        result = WORDPRESS_COLLECTORS[church.key](church.archive_url)
        for sermon in result.sermons.values():
            sermon.place_id = church.place_id
            sermon.church_name = church.name
        return result
    if church.key in NEW_KEYS:
        return collect_remaining(church, output)
    raise ValueError(f"No collector for {church.platform}")


def main():
    active_churches = {
        key: church
        for key, church in CHURCH_BY_KEY.items() if church.status == "active"
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("church", choices=["all", *active_churches], nargs="?", default="all")
    parser.add_argument("--output", type=Path, default=Path("outputs/sermon_collection"))
    args = parser.parse_args()
    failures = []
    for name, church in active_churches.items():
        if args.church in {"all", name}:
            try:
                result = collect_church(church, output=args.output)
            except Exception as error:
                if args.church != "all":
                    raise
                failures.append(name)
                print(f"{name}: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
                continue
            print(name, json.dumps(write_results(args.output, name, result), indent=2), flush=True)
    if failures:
        raise SystemExit("Failed collectors: " + ", ".join(failures))


if __name__ == "__main__":
    main()
