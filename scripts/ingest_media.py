"""Plan, probe, or ingest explicitly selected Stage 2 recordings."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

import requests

from media_ingestion.pipeline import KINDS, Limits, MediaPipeline, load_manifest
from media_ingestion.reporting import plan, report_outputs
from media_ingestion.transport import MediaError

DEFAULT_INPUT = Path("outputs/sermon_collection/stage2/recording_manifest.csv")
DEFAULT_OUTPUT = Path("outputs/sermon_collection/stage3")


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
