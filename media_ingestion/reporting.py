"""Write offline eligibility and verified audio inventories."""

import csv
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from .eligibility import assess
from .pipeline import reference_fingerprint, valid_checkpoint
from .transport import kind_for

PLAN_COLUMNS = ("recording_id", "church_id", "church_number", "reference_types",
                "selection_status", "review_flags", "warning_flags", "hold_reasons")
OUTPUT_COLUMNS = ("recording_id", "church_id", "church_number", "audio_file",
                  "duration_seconds", "sample_rate", "channels", "sha256")


def save_csv(path, fields, rows):
    with NamedTemporaryFile("w", newline="", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def plan(recordings, output):
    eligible, review = [], []
    fields = dict.fromkeys(PLAN_COLUMNS)
    for item in recordings.values():
        kinds = {kind_for(url) for url in item.references}
        disposition = assess(item)
        reason = disposition.primary_hold
        row = {**item.source_record, "recording_id": item.identifier, "church_id": item.church_id,
               "church_number": item.church_number, "reference_types": ";".join(sorted(kinds)),
               "selection_status": reason or "eligible_for_media_check",
               "review_flags": ";".join(item.review_flags), "warning_flags": ";".join(disposition.warnings),
               "hold_reasons": ";".join(disposition.holds)}
        fields.update(dict.fromkeys(row))
        (review if reason else eligible).append(row)
    save_csv(output / "media_plan.csv", fields, eligible)
    save_csv(output / "media_review.csv", fields, review)
    return {"eligible_for_media_check": len(eligible), "held_for_review": len(review)}


def report_outputs(recordings, output):
    complete, samples = {}, []
    for path in sorted((output / "audio").glob("*.checkpoint.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            item = recordings[state["recording_id"]]
            if item.held_reason() or state["manifest_reference_sha256"] != reference_fingerprint(item):
                continue
            audio = path.with_name(path.name.removesuffix(".checkpoint.json") + ".wav")
            if audio.name != f"{item.identifier}-{state['task_key']}.wav":
                continue
            if not valid_checkpoint(path, audio, state["task_key"]):
                continue
            measured = state["audio"]
            scope = state["scope"]
            row = dict(recording_id=item.identifier, church_id=item.church_id,
                       church_number=item.church_number, audio_file=f"audio/{audio.name}",
                       duration_seconds=measured["duration_seconds"], sample_rate=measured["sample_rate"],
                       channels=measured["channels"], sha256=state["sha256"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if scope == "sample":
            samples.append(row)
        elif scope == "full":
            completed = path.stat().st_mtime_ns
            if item.identifier not in complete or completed > complete[item.identifier][0]:
                complete[item.identifier] = (completed, row)
    save_csv(output / "transcription_candidates.csv", OUTPUT_COLUMNS, [value[1] for _, value in sorted(complete.items())])
    save_csv(output / "decoded_samples.csv", OUTPUT_COLUMNS, samples)
    return {"full_audio_candidates": len(complete), "decoded_samples": len(samples)}
