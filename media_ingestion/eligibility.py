"""Stage 3 dispositions for the Stage 2 recording groups."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .transport import kind_for

KINDS = ("audio", "video", "youtube", "soundcloud", "hls")
INACTIVE = {"8", "18"}
IDENTITY_FLAGS = {
    "source_identity_review", "church_location_unresolved", "shared_adventist_archive",
    "campus_attribution_unverified", "candidate_channel_association",
    "cross_church_media_path_review", "conflicting_church_ids", "missing_church_id",
    "cross_church_exact_reference", "cross_church_platform_id",
}
CONTENT_FLAGS = {"source_content_review", "attachment_requires_sermon_review"}
AMBIGUITY_FLAGS = {
    "ambiguous_exact_url_bridge", "conflicting_metadata_on_exact_url",
    "exact_url_multiple_platform_ids", "metadata_conflicts_with_url_linked_platform_id",
    "multiple_recording_identifiers", "conflicting_title", "conflicting_speaker",
    "conflicting_date", "conflicting_duration",
}
WARNING_FLAGS = {
    "non_media_asset_in_input", "same_title_and_date_separate_groups",
    "query_variant_reference_review", "multiple_date_meanings", "unparsed_date",
    "unparsed_duration", "invalid_reference", "invalid_reference_array",
}
KNOWN_FLAGS = IDENTITY_FLAGS | CONTENT_FLAGS | AMBIGUITY_FLAGS | WARNING_FLAGS | {"inactive_church_archive"}


@dataclass(frozen=True)
class Eligibility:
    holds: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def primary_hold(self):
        return self.holds[0] if self.holds else ""


def monroeville_title_suffix_only(recording):
    if (recording.church_number != "24" or len(recording.identity) != 3
            or recording.identity[:2] != ("sermonnet", "monpres.sermon.net")
            or not recording.identity[2].isdigit()):
        return False
    titles = recording.title_variants
    base_titles = {re.sub(r"\s*-\s*audio\s*$", "", title, flags=re.I).strip().casefold()
                   for title in titles}
    return len(set(titles)) > 1 and len(base_titles) == 1 and "" not in base_titles and any(
        re.search(r"\s*-\s*audio\s*$", title, re.I) for title in titles
    )


def brierfield_historical_pages(recording):
    # The saved #39 individual sermon pages were flagged for historical
    # availability. Limit this exception to that audited archive route.
    if recording.church_number != "39" or not recording.pages:
        return False
    try:
        return all(urlsplit(page).hostname == "www.churchatbrierfield.org"
                   and urlsplit(page).path.startswith("/sermon/")
                   for page in recording.pages)
    except ValueError:
        return False


def assess(recording):
    flags = set(recording.review_flags)
    warnings = flags & WARNING_FLAGS
    if "conflicting_title" in flags and monroeville_title_suffix_only(recording):
        warnings.add("conflicting_title")
    if "source_content_review" in flags and brierfield_historical_pages(recording):
        warnings.add("source_content_review")
    blockers = flags - warnings

    holds = []
    if recording.church_number in INACTIVE or "inactive_church_archive" in blockers:
        holds.append("inactive_church")
    if not recording.church_id or not recording.church_number or blockers & IDENTITY_FLAGS:
        holds.append("church_identity_review")
    if not any(kind_for(url) in KINDS for url in recording.references):
        holds.append("no_supported_reference")
    if blockers & AMBIGUITY_FLAGS:
        holds.append("recording_ambiguity_review")
    if blockers & CONTENT_FLAGS:
        holds.append("content_relevance_review")
    if blockers - KNOWN_FLAGS:
        holds.append("unclassified_review_required")
    return Eligibility(tuple(holds), tuple(sorted(warnings)))
