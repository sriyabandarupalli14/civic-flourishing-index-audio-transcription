"""Combine selected local Stage 1 outputs without changing or deduplicating their rows."""

import csv
import json

from .registry import CHURCHES


def consolidate(directory):
    rows = []
    fields = ["church_key", "church_number", "registry_place_id", "registry_church_name",
              "collection_origin"]
    by_church = {}
    for church in CHURCHES:
        if church.status != "active":
            continue
        with (directory / f"{church.key}_summary.json").open(encoding="utf-8") as handle:
            summary = json.load(handle)
        origin = summary.get("collection_origin", "prior_verified_live")
        with (directory / f"{church.key}.csv").open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "source_id" not in reader.fieldnames:
                raise ValueError(f"Missing source_id header for {church.key}")
            fields.extend(name for name in reader.fieldnames if name not in fields)
            source_rows = list(reader)
        if len(source_rows) != summary["records"]:
            raise ValueError(f"CSV and summary count disagree for {church.key}")
        for row in source_rows:
            rows.append(dict(row, church_key=church.key, church_number=church.number,
                             registry_place_id=church.place_id, registry_church_name=church.name,
                             collection_origin=origin))
        by_church[church.key] = {"records": len(source_rows), "collection_origin": origin}
    combined = directory / "combined_stage1_first20.csv"
    temporary = combined.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(combined)
    finally:
        temporary.unlink(missing_ok=True)
    summary = {"records": len(rows), "churches": len(by_church), "by_church": by_church}
    (directory / "combined_stage1_first20_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8",
    )
    return summary
