"""Replay November metadata caches and combine Stage 1 observations offline."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile

from collectors.archive_cache import ArchiveCache, SPRINT
from collectors.registry import CHURCHES
from collectors.stage1b_routes import ROUTES, Replay, run_replay
from xml.etree.ElementTree import ParseError

MEDIA_FIELDS = ("media_url", "audio_url", "video_url", "player_url")


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def media_references(row):
    links = [row.get(key, "") for key in MEDIA_FIELDS]
    for key in ("media_urls", "all_media_references_json"):
        if value := row.get(key):
            links.extend(json.loads(value) if isinstance(value, str) else value)
    return {url for url in links if url}


def standardized(church, row):
    return {
        **row,
        "church_key": church.key, "church_number": church.number,
        "registry_place_id": church.place_id, "registry_church_name": church.name,
        "collection_origin": "november_preserved_observation",
        "source_id": row["observation_id"],
        "sermon_url": row.get("source_page_url") or row.get("source_archive_url") or church.archive_url,
        "title": row.get("sermon_title", ""), "date": row.get("sermon_date", ""),
        "date_basis": row.get("date_meaning", ""),
        "media_urls": json.dumps(sorted(media_references(row)), ensure_ascii=False),
    }


def compare_replay(preserved, replay):
    """Compare exact URLs and metadata without changing or merging rows."""
    saved = defaultdict(list)
    for row in preserved:
        for url in media_references(row):
            saved[url].append(row)
    references = {url for row in replay for url in media_references(row)}
    counts = Counter()
    fields = ("sermon_title", "sermon_date", "speaker", "duration_sec")
    for row in replay:
        peers = saved.get(row.get("media_url"), [])
        for field in fields:
            value = str(row.get(field, "") or "")
            if value and peers:
                suffix = "matches" if any(value == str(peer.get(field, "") or "") for peer in peers) else "differs_or_absent"
                counts[field + "_" + suffix] += 1
    return {
        "preserved_exact_refs": len(saved), "replay_exact_refs": len(references),
        "matching_exact_refs": len(references.intersection(saved)),
        **{f"{key}_{suffix}": counts[f"{key}_{suffix}"] for key in fields
           for suffix in ("matches", "differs_or_absent")},
    }


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    if not fields:
        fields = ["church_number", "place_id", "source_page_url", "media_url"]
    with NamedTemporaryFile(mode="w", newline="", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temporary = Path(temp.name)
        try:
            writer = csv.DictWriter(temp, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            temp.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def collect(reference, first20, output, *, replayer=run_replay):
    reference, output = reference.resolve(), output.resolve()
    if output.is_relative_to(reference):
        raise ValueError("Output directory must be outside the original research project")
    before = read_rows(first20)
    first_by_id = defaultdict(list)
    first_registry = {c.place_id: c for c in CHURCHES[:20] if c.status == "active"}
    for row in before:
        place_id = row["registry_place_id"]
        church = first_registry.get(place_id)
        if church is None or row["church_number"] != str(church.number) or row["church_key"] != church.key:
            raise ValueError("First-20 input contains an excluded, unknown, or mismatched church")
        first_by_id[place_id].append(row)
    if set(first_by_id) != set(first_registry):
        raise ValueError("First-20 input is missing an active church export")

    sprint = reference / SPRINT
    ledger = {row["place_id"]: row for row in read_rows(sprint / "coverage_after.csv")}
    legacy = defaultdict(list)
    archived_excluded = {c.place_id for c in CHURCHES[:20] if c.status == "excluded"}
    later_ids = {c.place_id for c in CHURCHES[20:]}
    for row in read_rows(sprint / "combined_review_v1.csv"):
        if row["place_id"] in later_ids | archived_excluded:
            legacy[row["place_id"]].append(row)
    excluded = Counter(r["place_id"] for r in read_rows(sprint / "excluded_unrelated_observations.csv"))
    cache = ArchiveCache(reference)
    coverage, combined, replays = [], list(before), []
    excluded_carryover = 0
    for church in CHURCHES:
        key = church.place_id
        saved = first_by_id[key] if church.number <= 20 and key not in archived_excluded else legacy[key]
        finding = {
            "church_number": church.number, "place_id": key, "church_name": church.name,
            "platform": church.platform or "", "collection_routes": ROUTES.get(church.number, church.platform or ""),
            "master_archive_url": church.original_archive_url,
            "records": len(saved), "without_media_reference": sum(not media_references(row) for row in saved),
            "excluded_unrelated_observations": excluded[key] if church.number > 20 else 0,
        }
        if church.number <= 20:
            if key in archived_excluded:
                historical = ledger.get(key)
                if historical and int(historical["observations"]) != len(saved):
                    raise ValueError(f"November ledger disagrees with archived rows for church #{church.number}")
                if any(row.get("scope_disposition", "").startswith("excluded") for row in saved):
                    raise ValueError(f"Unrelated observation in review union for church #{church.number}")
                combined.extend(dict(standardized(church, row),
                                     collection_origin="november_archive_only_inactive_church") for row in saved)
                excluded_carryover += len(saved)
                finding.update(
                    status="excluded_from_active_collection", selected_from="November review union" if saved else "",
                    limitations="No collector activated or replayed in this pass. " +
                    ("Existing November observations included for all-44 coverage." if saved
                     else "No preserved November observations."),
                    identity_review=(historical or {}).get("identity_review", ""),
                )
            else:
                finding.update(status="carried_forward", selected_from="existing Stage 1A output",
                               limitations="Existing Stage 1A output; no new replay in this pass")
        else:
            historical = ledger[key]
            if len(saved) != int(historical["observations"]):
                raise ValueError(f"November ledger disagrees with saved rows for church #{church.number}")
            if any(row.get("scope_disposition", "").startswith("excluded") for row in saved):
                raise ValueError(f"Excluded observation in November review union for church #{church.number}")
            try:
                replay = replayer(cache, church)
            except (ValueError, KeyError, TypeError, ParseError) as error:
                replay = Replay()
                finding["replay_error"] = type(error).__name__
                replay.modes.append("Cache shape or parser mismatch; saved observations retained")
            replays.extend(dict(row, church_number=church.number, place_id=key) for row in replay.rows)
            combined.extend(standardized(church, row) for row in saved)
            files = sorted({row["evidence_file"] for row in saved if row.get("evidence_file")})
            finding.update(
                status=historical["current_status"], identity_review=historical["identity_review"],
                limitations=historical["evidence_assessment"],
                completeness_boundary=historical["completeness_boundary"],
                selected_from="November review union", saved_evidence_files=json.dumps(files),
                replay_modes="; ".join(replay.modes), saved_responses_checked=replay.checked,
                missing_requested_captures=replay.missing, replay_rows=len(replay.rows),
                **compare_replay(saved, replay.rows),
            )
        coverage.append(finding)

    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "combined_stage1_first44.csv", combined)
    write_csv(output / "first44_collection_coverage.csv", coverage)
    write_csv(output / "stage1b_cached_replay.csv", replays)
    summary = {
        "registered_churches": len(CHURCHES),
        "churches_with_records": sum(bool(row["records"]) for row in coverage),
        "first20_records": len(before), "archived_inactive_church_records": excluded_carryover,
        "stage1b_records": len(combined) - len(before) - excluded_carryover,
        "total_records": len(combined), "replayed_rows": len(replays),
        "stage1b_without_media_reference": sum(row["without_media_reference"] for row in coverage[20:]),
        "stage1b_unrelated_held_out": sum(row["excluded_unrelated_observations"] for row in coverage[20:]),
    }
    (output / "first44_collection_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="Original research directory (read-only)")
    parser.add_argument("--first20", type=Path, default=Path("outputs/sermon_collection/combined_stage1_first20.csv"))
    parser.add_argument("--output", type=Path, default=Path("outputs/sermon_collection/stage1b"))
    args = parser.parse_args()
    print(json.dumps(collect(args.reference, args.first20, args.output), indent=2))


if __name__ == "__main__":
    main()
