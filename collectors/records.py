"""Extended collection rows for source provenance and legacy-record parity."""

import re
from dataclasses import dataclass, field

from .schema import Collection, Sermon


@dataclass
class SourceRecord(Sermon):
    place_id: str = ""
    church_name: str = ""
    duration_sec: int | str = ""
    data_note: str = ""
    audio_url: str = ""
    video_url: str = ""
    source_fields: dict = field(default_factory=dict)


class SourceCollection(Collection):
    record_type = SourceRecord


def format_collection(rows, archive, *, coverage=(), limitations=()):
    result = SourceCollection(coverage=list(coverage), limitations=list(limitations))
    for index, raw in enumerate(rows, 1):
        row = dict(raw)
        audio, video = row.pop("audio_url", "") or "", row.pop("video_url", "") or ""
        note = row.pop("data_note", "") or ""
        source = re.search(r"(?:^|; )source (?:post|page|collection): ([^;]+)", note)
        source_id = str(row.pop("source_id", "") or index)
        result.sermons[source_id] = SourceRecord(
            source_id, row.pop("source_page", "") or (source[1] if source else archive),
            title=row.pop("sermon_title", "") or "", speaker=row.pop("speaker", "") or "",
            date=row.pop("sermon_date", "") or "", date_basis=row.pop("date_basis", "") or "",
            media_urls=list(dict.fromkeys(url for url in (audio, video) if url)),
            place_id=row.pop("place_id", ""), church_name=row.pop("church_name", ""),
            duration_sec=row.pop("duration_sec", ""), data_note=note, audio_url=audio,
            video_url=video, source_fields=row,
        )
    return result
