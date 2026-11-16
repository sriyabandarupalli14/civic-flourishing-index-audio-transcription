"""Church-scoped grouping with explicit handling for ambiguous bridges."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
import json

from .normalize import url_parts

MATCHABLE = {"audio_file", "video_file", "player"}
MEDIA = MATCHABLE | {"stream"}


def trusted_reference(url, kind):
    # A stream endpoint can be reused between services. Use a provider's
    # broadcast ID or source metadata for those rows, never the stream alone.
    return kind in MATCHABLE and url_parts(url) is not None


def incompatible(left, right):
    """Guard URL-only merges against reuse of a link for different events."""
    left, right = list(left), list(right)
    for field in ("title", "speaker"):
        a = {getattr(row, field).casefold() for row in left if getattr(row, field)}
        b = {getattr(row, field).casefold() for row in right if getattr(row, field)}
        if a and b and a.isdisjoint(b):
            return True
    dates = defaultdict(set)
    for row in left + right:
        if row.date:
            dates[row.date_basis.casefold()].add(row.date)
    return any(len(values) > 1 for values in dates.values())


def within_conflict(rows):
    for field in ("title", "speaker"):
        if len({getattr(row, field).casefold() for row in rows if getattr(row, field)}) > 1:
            return True
    return incompatible(rows, ())


@dataclass
class Recording:
    observations: list
    basis: set
    identity: tuple | None = None
    flags: set = field(default_factory=set)

    @property
    def id(self):
        key = [self.observations[0].church_id, self.identity]
        if not self.identity:
            key.append(sorted(row.line for row in self.observations))
        digest = sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()
        return f"rec_{digest[:20]}"


def group_observations(observations):
    anchored = {}
    singles, unanchored = [], {}
    url_groups = defaultdict(set)
    unknown_urls = defaultdict(set)

    for row in observations:
        if not row.church_id or set(row.flags) & {"conflicting_church_ids", "multiple_recording_identifiers"}:
            singles.append(Recording([row], {"observation_only"}))
            continue
        urls = {(row.church_id, url) for url, kind in row.refs.items() if trusted_reference(url, kind)}
        if row.identities:
            identity = next(iter(row.identities))
            key = (row.church_id, identity)
            group = anchored.setdefault(key, Recording([], {"church_platform_id"}, identity))
            group.observations.append(row)
            for url in urls:
                url_groups[url].add(key)
        else:
            unanchored[row.line] = (row, urls)
            for url in urls:
                unknown_urls[url].add(row.line)

    anchor_evidence = {key: list(group.observations) for key, group in anchored.items()}

    for anchors in url_groups.values():
        if len(anchors) > 1:
            for key in anchors:
                anchored[key].flags.add("exact_url_multiple_platform_ids")

    # Connected components of unanchored rows allow exact URL variants, but they
    # must never form a transitive bridge between distinct platform identities.
    visited, visited_urls = set(), set()
    for start in unanchored:
        if start in visited:
            continue
        pending, component, neighbours = [start], [], set()
        visited.add(start)
        while pending:
            current = pending.pop()
            row, urls = unanchored[current]
            component.append(row)
            for url in urls - visited_urls:
                visited_urls.add(url)
                neighbours.update(url_groups[url])
                for other in unknown_urls[url] - visited:
                    visited.add(other)
                    pending.append(other)

        if len(neighbours) > 1:
            reason = "ambiguous_exact_url_bridge"
        elif len(component) > 1 and within_conflict(component):
            reason = "conflicting_metadata_on_exact_url"
        elif neighbours:
            key = next(iter(neighbours))
            anchor = anchored[key]
            if incompatible(component, anchor_evidence[key]):
                reason = "metadata_conflicts_with_url_linked_platform_id"
            else:
                anchor.observations.extend(component)
                anchor.basis.add("unambiguous_exact_url")
                continue
        else:
            singles.append(Recording(component, {"exact_url" if len(component) > 1 else "observation_only"}))
            continue

        for key in neighbours:
            anchored[key].flags.add(reason)
        for row in component:
            singles.append(Recording([row], {"observation_only"}, flags={reason}))

    groups = [*anchored.values(), *singles]
    annotate_ambiguities(groups)
    return sorted(groups, key=lambda item: min(row.line for row in item.observations))


def annotate_ambiguities(groups):
    same_url, same_identity, same_title_date = defaultdict(set), defaultdict(set), defaultdict(set)
    query_paths = defaultdict(dict)
    across_church_paths = defaultdict(set)
    for index, group in enumerate(groups):
        for row in group.observations:
            group.flags.update(row.flags)
            if group.identity:
                same_identity[group.identity].add(index)
            for url, kind in row.refs.items():
                if not url_parts(url):
                    continue
                parts = url_parts(url)
                if kind == "non_media_asset":
                    continue
                same_url[url].add(index)
                if kind in {"audio_file", "video_file"}:
                    across_church_paths[(parts.hostname, parts.path)].add(index)
                if kind in {"audio_file", "video_file", "stream"} and parts.query:
                    key = (row.church_id, parts.hostname, parts.path)
                    query_paths[key].setdefault(index, set()).add(url)
            if row.title and row.date:
                same_title_date[(row.church_id, row.title.casefold(), row.date)].add(index)

    def flag_cross_church(indexes, flag):
        churches = {groups[i].observations[0].church_id for i in indexes}
        if len(churches) > 1:
            for i in indexes:
                groups[i].flags.add(flag)

    for indexes in same_url.values():
        flag_cross_church(indexes, "cross_church_exact_reference")
    for indexes in across_church_paths.values():
        flag_cross_church(indexes, "cross_church_media_path_review")
    for indexes in same_identity.values():
        flag_cross_church(indexes, "cross_church_platform_id")
    for mapping in query_paths.values():
        if len(mapping) > 1 and len(set().union(*mapping.values())) > 1:
            for i in mapping:
                groups[i].flags.add("query_variant_reference_review")
    for (church, _, _), indexes in same_title_date.items():
        if church and len(indexes) > 1:
            for i in indexes:
                groups[i].flags.add("same_title_and_date_separate_groups")


def variants(group, name):
    return Counter(getattr(row, name) for row in sorted(group.observations, key=lambda row: row.line)
                   if getattr(row, name))


def manifest_record(group):
    observations = sorted(group.observations, key=lambda row: row.line)
    first = observations[0]
    fields = ("title", "speaker", "date", "duration")
    values = {name: variants(group, name) for name in fields}
    for field in fields:
        normalized = {value.casefold() for value in values[field]}
        if len(normalized) > 1:
            group.flags.add("conflicting_" + field)
    # A publication date and an event date are not the same semantic field.
    date_pairs = Counter((row.date, row.date_basis) for row in observations if row.date)
    if len({basis.casefold() for _, basis in date_pairs if basis}) > 1:
        group.flags.add("multiple_date_meanings")

    links = defaultdict(set)
    for row in observations:
        for url, kind in row.refs.items():
            links[kind].add(url)
    if links["non_media_asset"]:
        group.flags.add("non_media_asset_in_input")
    known = set().union(*(links[kind] for kind in MEDIA))
    # The same reference can appear in both checked categories in the inputs.
    unresolved = links["unresolved_endpoint"] - known
    state = "with_media_reference" if known else "unresolved_reference_only" if unresolved else "no_media_reference"
    if state != "with_media_reference":
        group.flags.add(state)

    chosen = {field: (values[field].most_common(1)[0][0] if values[field] else "") for field in fields}
    pair = next((item for item, _ in date_pairs.most_common() if item[0] == chosen["date"]), ("", ""))
    return {
        "recording_id": group.id, "church_id": first.church_id, "church_number": first.church_number,
        "church_name": first.church_name, "title": chosen["title"], "speaker": chosen["speaker"],
        "date": pair[0], "date_basis": pair[1], "duration_sec": chosen["duration"],
        "platform_identifier": list(group.identity) if group.identity else [],
        "grouping_basis": sorted(group.basis), "media_state": state,
        "audio_urls": sorted(links["audio_file"]), "video_urls": sorted(links["video_file"]),
        "stream_urls": sorted(links["stream"]), "player_urls": sorted(links["player"]),
        "unresolved_urls": sorted(unresolved), "invalid_references": sorted(links["invalid_reference"]),
        "non_media_urls": sorted(links["non_media_asset"]),
        "source_pages": sorted({row.page for row in observations if row.page}),
        "metadata_variants": {**{field: sorted(values[field]) for field in fields},
                              "date_evidence": [list(item) for item in sorted(date_pairs)]},
        "deduplicated_observation_count": len(observations),
        "review_flags": sorted(group.flags), "playback_verified": "false",
    }
