"""Build a reproducible evaluation plan and cut clips from verified local media."""

import argparse
import json
import tomllib
from pathlib import Path

from evaluation.dataset import materialize, plan_dataset
from evaluation.sources import acquire_prefixes
from media_ingestion.pipeline import atomic_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--manifest", type=Path, default=Path("outputs/sermon_collection/stage2/recording_manifest.csv"))
    plan.add_argument("--config", type=Path, default=Path("configs/evaluation.toml"))
    plan.add_argument("--development-recording", action="append", default=[])
    plan.add_argument("--failed-recording", action="append", default=[])
    plan.add_argument("--failed-church", action="append", default=[])
    plan.add_argument("--output", type=Path, default=Path("outputs/evaluation/plan.json"))
    clips = commands.add_parser("materialize")
    clips.add_argument("--plan", type=Path, default=Path("outputs/evaluation/plan.json"))
    clips.add_argument("--cache-index", type=Path, help="Local JSON list of checksummed audio sources")
    clips.add_argument("--output", type=Path, default=Path("outputs/evaluation"))
    acquire = commands.add_parser("acquire", help="Fetch bounded prefixes for the preselected slots")
    acquire.add_argument("--plan", type=Path, default=Path("outputs/evaluation/plan.json"))
    acquire.add_argument("--manifest", type=Path, default=Path("outputs/sermon_collection/stage2/recording_manifest.csv"))
    acquire.add_argument("--cache-index", type=Path, help="Local JSON list of existing verified audio sources")
    acquire.add_argument("--output", type=Path, default=Path("outputs/evaluation"))
    args = parser.parse_args(argv)
    if args.command == "plan":
        with args.config.open("rb") as handle:
            result = plan_dataset(args.manifest, tomllib.load(handle)["dataset"], args.development_recording,
                                  failed_recordings=args.failed_recording, failed_churches=args.failed_church)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.output, result)
        print(f"Planned {len(result['clips'])} clips. Plan SHA-256 {result['plan_sha256']}")
    else:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        sources = json.loads(args.cache_index.read_text(encoding="utf-8")) if args.cache_index else []
        if args.command == "acquire":
            args.output.mkdir(parents=True, exist_ok=True)
            sources, events = acquire_prefixes(plan, args.manifest, args.output / "prefixes", sources)
            atomic_json(args.output / "cache_index.json", sources)
            atomic_json(args.output / "acquisition.json", {"plan_sha256": plan["plan_sha256"], "results": events})
            print(f"Audio sources available for materialization: {len(sources)}")
        else:
            result = materialize(plan, sources, args.output)
            atomic_json(args.output / "clip_manifest.json", result)
            print(json.dumps(result["coverage"], indent=2))


if __name__ == "__main__":
    main()
