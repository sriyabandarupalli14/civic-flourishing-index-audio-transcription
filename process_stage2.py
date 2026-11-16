"""Clean and reconcile Stage 1 observations without network access."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from hashlib import file_digest
from pathlib import Path
from tempfile import NamedTemporaryFile

from processing.grouping import MEDIA, group_observations, manifest_record
from processing.normalize import exact_payload, normalize

DEFAULT_INPUT = Path("outputs/sermon_collection/stage1b/combined_stage1_first44.csv")
DEFAULT_OUTPUT = Path("outputs/sermon_collection/stage2")
REQUIRED = {"registry_place_id", "church_number", "title", "media_urls"}

MANIFEST_FIELDS = (
    "recording_id", "church_id", "church_number", "church_name", "title", "speaker",
    "date", "date_basis", "duration_sec", "platform_identifier", "grouping_basis",
    "media_state", "audio_urls", "video_urls", "stream_urls", "player_urls",
    "unresolved_urls", "invalid_references", "non_media_urls", "source_pages", "metadata_variants",
    "deduplicated_observation_count", "review_flags", "playback_verified",
    "input_observation_count", "exact_duplicate_count", "input_rows",
)

LINEAGE_FIELDS = (
    "recording_id", "stage1_input_row", "representative_input_row", "exact_duplicate",
    "collection_origin", "source_id", "audit_observation_id", "observation_id",
    "evidence_file", "evidence_csv_row", "source_csv", "original_source_csv",
    "additional_source_csv", "source_page_url", "historical_snapshot_url", "cache_evidence",
)


def write_csv(path, fields, rows):
    with NamedTemporaryFile("w", newline="", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (list, dict)) else value for key, value in row.items()
                })
            handle.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def collect_observations(path):
    unique, raw_rows, owners = {}, {}, {}
    observations = []
    counts = Counter()
    raw_signatures = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not REQUIRED.issubset(reader.fieldnames or []):
            raise ValueError("Input does not have the expected Stage 1 header")
        # A CSV record number is stable even when a cell contains embedded newlines.
        for index, row in enumerate(reader, 1):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV record {index}")
            raw_rows[index] = row
            observation = normalize(row, index)
            counts["observations_with_media_reference"] += any(k in MEDIA for k in observation.refs.values())
            counts["unparsed_dates"] += "unparsed_date" in observation.flags
            counts["unparsed_durations"] += "unparsed_duration" in observation.flags
            raw_key = tuple(row.values())
            counts["verbatim_row_duplicates"] += raw_key in raw_signatures
            raw_signatures.add(raw_key)
            payload = exact_payload(row, observation)
            owner = unique.setdefault(payload, index)
            owners[index] = owner
            if index == owner:
                observations.append(observation)
    return observations, raw_rows, owners, counts


def reconcile(path, output):
    path, output = path.resolve(), output.resolve()
    if output.is_relative_to(path.parent):
        raise ValueError("Use a separate output directory from the Stage 1 export")
    observations, raw_rows, owners, stats = collect_observations(path)
    groups = group_observations(observations)
    input_members = defaultdict(list)
    for original, representative in owners.items():
        input_members[representative].append(original)

    records, lineage = [], []
    for group in groups:
        row = manifest_record(group)
        source_rows = sorted(i for observation in group.observations for i in input_members[observation.line])
        row["input_rows"] = source_rows
        row["input_observation_count"] = len(source_rows)
        row["exact_duplicate_count"] = len(source_rows) - len(group.observations)
        records.append(row)
        for index in source_rows:
            raw = raw_rows[index]
            link = {key: raw.get(key, "") for key in LINEAGE_FIELDS[4:]}
            link.update(recording_id=row["recording_id"], stage1_input_row=index,
                        representative_input_row=owners[index],
                        exact_duplicate="true" if owners[index] != index else "false")
            lineage.append(link)

    with_media = [r for r in records if r["media_state"] == "with_media_reference"]
    without_media = [r for r in records if r["media_state"] != "with_media_reference"]
    review = [r for r in records if r["review_flags"]]
    unresolved_only = sum(r["media_state"] == "unresolved_reference_only" for r in without_media)
    flags = Counter(flag for r in review for flag in r["review_flags"])
    basis = Counter(name for record in records for name in record["grouping_basis"])
    if len({entry["stage1_input_row"] for entry in lineage}) != len(raw_rows) or len(lineage) != len(raw_rows):
        raise ValueError("Every original input record must be represented exactly once in lineage")
    if len({r["recording_id"] for r in records}) != len(records):
        raise ValueError("Recording IDs must be unique")

    with path.open("rb") as handle:
        checksum = file_digest(handle, "sha256").hexdigest()
    report = {
        "input_file": path.name, "input_sha256": checksum, "input_observations": len(raw_rows),
        "verbatim_row_duplicates": stats["verbatim_row_duplicates"],
        "exact_payload_duplicates_removed": len(raw_rows) - len(observations),
        "observations_after_exact_deduplication": len(observations),
        "observations_combined_by_grouping": len(observations) - len(records),
        "provisional_recording_groups": len(records),
        "groups_with_media_reference": len(with_media),
        "groups_without_media_reference": len(without_media),
        "groups_with_unresolved_endpoint_only": unresolved_only,
        "groups_without_classified_media_or_endpoint": len(without_media) - unresolved_only,
        "review_groups": len(review), "review_flag_counts": dict(sorted(flags.items())),
        "grouping_basis_counts": dict(sorted(basis.items())),
        "input_observations_with_media_reference": stats["observations_with_media_reference"],
        "input_observations_without_media_reference": len(raw_rows) - stats["observations_with_media_reference"],
        "unparsed_input_dates": stats["unparsed_dates"],
        "unparsed_input_durations": stats["unparsed_durations"],
        "lineage_rows": len(lineage), "network_validation_performed": False,
    }

    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "recording_manifest.csv", MANIFEST_FIELDS, with_media)
    write_csv(output / "recordings_without_media.csv", MANIFEST_FIELDS, without_media)
    write_csv(output / "recording_review.csv", MANIFEST_FIELDS, review)
    write_csv(output / "observation_lineage.csv", LINEAGE_FIELDS, sorted(lineage, key=lambda r: r["stage1_input_row"]))
    (output / "stage2_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (output / "stage2_report.md").write_text(render_report(report), encoding="utf-8")
    return report


def render_report(report):
    values = {
        "Input observations": "input_observations",
        "Exact payload duplicates removed": "exact_payload_duplicates_removed",
        "Observations after exact deduplication": "observations_after_exact_deduplication",
        "Observations combined through recording grouping": "observations_combined_by_grouping",
        "Provisional recording groups": "provisional_recording_groups",
        "Groups with a media reference": "groups_with_media_reference",
        "Groups without a media reference": "groups_without_media_reference",
        "Groups with unresolved endpoints only": "groups_with_unresolved_endpoint_only",
        "Groups flagged for review": "review_groups",
        "Input observations linked in lineage": "lineage_rows",
    }
    table = "\n".join(f"| {label} | {report[key]:,} |" for label, key in values.items())
    flags = "\n".join(f"- `{flag}` appears on {count:,} groups."
                      for flag, count in report["review_flag_counts"].items())
    return f"""# Stage 2 cleaning and recording reconciliation

Input: `{report["input_file"]}`

SHA-256: `{report["input_sha256"]}`

| Measure | Count |
| --- | ---: |
{table}

## How the counts were derived

The input contained {report["verbatim_row_duplicates"]:,} verbatim repeated rows. The exact-payload
pass compares church, scoped identities, normalized title/speaker/date/duration, date meaning,
exact typed references and review flags. Rows lacking reliable reference evidence require a verbatim
match. The lineage table records every original
CSV record number, including repeats, together with the evidence locators and assigned recording
ID. The source CSV identified by the checksum remains the authority for original metadata values.

Whitespace and recognized missing values are standardized. Parseable dates use ISO calendar dates
and retain their source meaning. Durations are expressed in seconds. The lineage can be used to
retrieve {report["unparsed_input_dates"]:,} unparsed date values and
{report["unparsed_input_durations"]:,} unparsed duration values from the input.

Recording grouping uses a church ID plus a scoped platform or message identity or an unambiguous exact media
reference. URL query strings are retained. Shared streams, conflicting IDs, and title-only similarity
do not drive automatic merges. Cross-church matches remain in their own groups. All retained typed
URL variants, non-media assets, and conflicting normalized metadata are present in the outputs. Display fields use
the most frequent nonblank value among deduplicated observations, with input order breaking ties.

## Output partitions

- `recording_manifest.csv` contains groups with file, stream, or individual player references.
- `recordings_without_media.csv` contains metadata-only groups and
  {report["groups_with_unresolved_endpoint_only"]:,} groups with unresolved endpoints only.
- `recording_review.csv` is a subset across both partitions. A group can have multiple review flags.
- `observation_lineage.csv` maps each Stage 1 data row to its provisional recording and exact-payload representative. Row 1 means the first data record after the header.

Media references were classified offline. Playback, availability, church identity, and final unique
sermon counts remain unverified.

## Review flags

{flags}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    config = parser.parse_args()
    print(json.dumps(reconcile(config.input, config.output), indent=2))


if __name__ == "__main__":
    main()
