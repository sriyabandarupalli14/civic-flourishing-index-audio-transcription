"""Dispatch and source configuration for the final ten Stage 1 adapters."""

import asyncio

from . import boxcast, nextjs, sermoncloud, site_media_api, squarespace, static_audio, subsplash
from .metadata import MetadataClient

HTTP_COLLECTORS = {
    "redeeming_grace_cullman": static_audio.collect_redeeming_grace,
    "church_of_the_highlands": nextjs.collect_highlands,
    "grace_community_montgomery": boxcast.collect_grace_montgomery,
    "amazing_grace_alabaster": subsplash.collect_amazing_grace,
    "victory_baptist_moulton": site_media_api.collect_victory_baptist,
    "gadsden_city_church": squarespace.collect_gadsden_city,
    "covenant_life_ministries": sermoncloud.collect_covenant_life,
}
RETRY_KEYS = {
    "amazing_grace_alabaster", "victory_baptist_moulton",
    "gadsden_city_church", "covenant_life_ministries",
}
NEW_KEYS = frozenset(HTTP_COLLECTORS) | {
    "christian_valley", "south_cullman", "first_presbyterian_troy",
}


def collect_remaining(church, output, *, page_limit=None, track_limit=None, failure_budget=None):
    key = church.key
    if key == "christian_valley":
        from . import youtube

        result = asyncio.run(youtube.collect_youtube(
            church.archive_url,
            fallback_urls=("https://www.cvbc-coatopa.org/sermons-1",),
        ))
    elif key == "south_cullman":
        from . import south_cullman

        checkpoint = output / "checkpoints" / "south_cullman_pages.csv"
        result = asyncio.run(south_cullman.collect_cards(
            church.archive_url, checkpoint, max_pages=page_limit or 70,
        ))
        if page_limit is not None:
            result.limitations.append(f"Verification bounded to {page_limit} catalog pages")
    elif key == "first_presbyterian_troy":
        from . import soundcloud

        result = soundcloud.collect_soundcloud(church.archive_url, limit=track_limit, failure_budget=failure_budget)
        if track_limit is not None:
            result.limitations.append(f"Verification bounded to {track_limit} tracks")
    else:
        parameters = {"page_limit": page_limit} if key == "church_of_the_highlands" else {}
        if key == "grace_community_montgomery":
            parameters["recover_pagination"] = True
        with MetadataClient(retry=key in RETRY_KEYS,
                            listing_post=key == "amazing_grace_alabaster") as session:
            result = HTTP_COLLECTORS[key](church.archive_url, church.place_id, church.name,
                                          session=session, **parameters)
    for record in result.sermons.values():
        record.place_id, record.church_name = church.place_id, church.name
    return result
