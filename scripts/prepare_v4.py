"""Prepare an independent V4 dataset using bounded Stage 3 acquisition."""

import argparse
import json
from collections import Counter
from pathlib import Path

from evaluation.dataset import materialize
from evaluation.sources import acquire_prefixes
from evaluation.v4_dataset import plan_v4
from media_ingestion.pipeline import atomic_json
from vocabulary.retrieval import read_lexicon


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--acquire", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "clip_manifest.json").exists():
        parser.error("Choose a fresh directory to preserve the frozen sample")
    previous = Path("outputs/evaluation/clip_manifest.json")
    manifest = Path("outputs/sermon_collection/stage2/recording_manifest.csv")
    lexicon = read_lexicon(Path("configs/religious_lexicon.tsv"))
    sources, failed, unavailable = [], set(), set()
    for attempt in range(3 if args.acquire else 1):
        plan = plan_v4(manifest, previous, lexicon, failed_ids=failed, failed_churches=unavailable)
        phase = output / f"round-{attempt + 1}"
        phase.mkdir(exist_ok=True)
        atomic_json(phase / "plan.json", plan)
        if not args.acquire:
            print([(r["source_record"]["church_number"], r["selection_evidence"]) for r in plan["clips"]])
            return
        sources, outcomes = acquire_prefixes(plan, manifest, output / "prefixes", sources)
        atomic_json(phase / "acquisition.json", outcomes)
        atomic_json(phase / "sources.json", sources)
        print(f"Round {attempt + 1} acquired {len(sources)} cumulative sources", flush=True)
        result = materialize(plan, sources, output)
        old_hashes = {row.get("clip_sha256") for row in json.loads(previous.read_text())["clips"]}
        for row in result["clips"]:
            if row.get("clip_sha256") in old_hashes and row["status"] == "ready":
                raise ValueError("The selected audio duplicates the previous study")
        totals = Counter(row["church_id"] for row in result["clips"])
        rejected = Counter(row["church_id"] for row in result["clips"] if row["status"] != "ready")
        if not rejected:
            break
        failed.update(row["recording_id"] for row in result["clips"] if row["status"] != "ready")
        unavailable.update(key for key in rejected if rejected[key] == totals[key])
    atomic_json(output / "clip_manifest.json", result)
    print(json.dumps(result["coverage"], indent=2))


if __name__ == "__main__":
    main()
