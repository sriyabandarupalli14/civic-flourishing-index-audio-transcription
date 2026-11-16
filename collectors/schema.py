"""Common collection records and the existing CSV/JSON export format."""

import csv
import json
from dataclasses import asdict, dataclass, field
from typing import ClassVar

@dataclass
class Sermon:
    source_id: str
    sermon_url: str
    title: str = ""
    date: str = ""
    date_basis: str = ""
    speaker: str = ""
    media_urls: list[str] = field(default_factory=list)
    unresolved_urls: list[str] = field(default_factory=list)
    playlists: list[str] = field(default_factory=list)


@dataclass
class Collection:
    record_type: ClassVar[type[Sermon]] = Sermon
    sermons: dict[str, Sermon] = field(default_factory=dict)
    coverage: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def write_results(directory, name, result):
    directory.mkdir(parents=True, exist_ok=True)
    fields = list(result.record_type.__dataclass_fields__)
    path = directory / f"{name}.csv"
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for sermon in result.sermons.values():
            row = asdict(sermon)
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})
    summary = {
        "records": len(result.sermons),
        "records_with_media": sum(bool(row.media_urls) for row in result.sermons.values()),
        "unique_media_urls": len({url for row in result.sermons.values() for url in row.media_urls}),
        "records_with_unresolved_links": sum(bool(row.unresolved_urls) for row in result.sermons.values()),
        "with_date": sum(bool(row.date) for row in result.sermons.values()),
        "with_speaker": sum(bool(row.speaker) for row in result.sermons.values()),
        "coverage": result.coverage,
        "limitations": result.limitations,
    }
    (directory / f"{name}_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
