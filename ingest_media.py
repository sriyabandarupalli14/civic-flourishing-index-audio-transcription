"""Plan, probe, or ingest explicitly selected Stage 2 recordings."""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import requests

from media_ingestion.eligibility import assess
from media_ingestion.pipeline import (
    KINDS, Limits, MediaPipeline, load_manifest, reference_fingerprint, valid_checkpoint,
)
from media_ingestion.transport import MediaError, kind_for

DEFAULT_INPUT = Path("outputs/sermon_collection/stage2/recording_manifest.csv")
DEFAULT_OUTPUT = Path("outputs/sermon_collection/stage3")
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "probe", "ingest", "report"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--recording", action="append", default=[])
    parser.add_argument("--kind", choices=KINDS)
    parser.add_argument("--sample-seconds", type=float)
    parser.add_argument("--max-mib", type=int, default=128)
    parser.add_argument("--max-seconds", type=int, default=10800)
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--max-references", type=int, default=3)
    args = parser.parse_args(argv)
    if args.mode in {"probe", "ingest"} and not args.recording:
        parser.error("provide explicit --recording IDs for network access")
    if args.sample_seconds is not None and args.mode != "ingest":
        parser.error("--sample-seconds applies only to ingest")
    try:
        limits = Limits(args.max_mib * 1024 * 1024, args.max_seconds, args.sample_seconds,
                        args.attempts, args.max_references)
        recordings = load_manifest(args.manifest)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    missing = set(args.recording) - recordings.keys()
    if missing:
        parser.error("one or more recording IDs do not occur in the manifest")
    if args.output.resolve().is_relative_to(args.manifest.resolve().parent):
        parser.error("write outputs outside the Stage 2 manifest directory")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode in {"plan", "report"}:
        action = plan if args.mode == "plan" else report_outputs
        print(json.dumps(action(recordings, args.output), indent=2))
        return 0

    logs = args.output / "logs"
    logs.mkdir(exist_ok=True)
    counts = Counter()
    with requests.Session() as session, (logs / f"{uuid4().hex}.jsonl").open("x", encoding="utf-8") as handle:
        session.max_redirects = 5
        pipe = MediaPipeline(session, args.output / "audio")

        def log(entry):
            handle.write(json.dumps(entry) + "\n")
            handle.flush()

        for key in dict.fromkeys(args.recording):
            try:
                result = pipe.run(recordings[key], mode=args.mode, kind=args.kind, limits=limits, log=log)
            except (MediaError, OSError) as error:
                result = {"recording_id": key, "status": "failed",
                          "reason": error.code if isinstance(error, MediaError) else "local_io_error"}
                log(result)
            counts[result["status"]] += 1
            print(json.dumps(result), flush=True)
    print(json.dumps({"results": counts}), file=sys.stderr)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
