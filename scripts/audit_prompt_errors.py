"""Audit literal terminology mismatches and possible prompt echoes in saved predictions."""

import argparse
import json
from collections import Counter
from pathlib import Path

from evaluation.metrics import align, index_terms, terminology_alignment, tokens
from evaluation.results import load_predictions
from media_ingestion.pipeline import atomic_json
from scripts.score_asr import named_paths
from vocabulary.retrieval import contains, read_lexicon


def prompt_echo(prediction):
    terms = prediction.get("prompt", {}).get("token_usage", {}).get("selected_terms", [])
    return len(terms) >= 3 and contains(tokens(prediction["text"]), tokens(" ".join(terms[:3])))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("outputs/evaluation/clip_manifest.json"))
    parser.add_argument("--lexicon", type=Path, default=Path("configs/religious_lexicon.tsv"))
    parser.add_argument("--reference", action="append", required=True)
    parser.add_argument("--prediction", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected = {clip["clip_id"]: clip["clip_sha256"] for clip in manifest["clips"]
                if clip["status"] == "ready" and clip["subset"] == "held_out"}
    predictions = {name: load_predictions(directory, expected)
                   for name, directory in named_paths(args.prediction).items()}
    lexicon = index_terms(row["term"] for row in read_lexicon(args.lexicon))
    output = []
    for reference, directory in named_paths(args.reference).items():
        refs = load_predictions(directory, expected)
        for name, candidates in predictions.items():
            misses, prompted_misses = Counter(), Counter()
            echoes, matched = 0, 0
            for clip_id in refs.keys() & candidates.keys():
                predicted, target = candidates[clip_id], refs[clip_id]
                ref, hyp = tokens(target["text"]), tokens(predicted["text"])
                _, matches = align(ref, hyp)
                _, found, recovered = terminology_alignment(ref, hyp, matches, lexicon)
                hint_terms = {tuple(tokens(term)) for term in
                              predicted.get("prompt", {}).get("token_usage", {}).get("selected_terms", [])}
                for _, term in found - recovered:
                    misses[" ".join(term)] += 1
                    if term in hint_terms:
                        prompted_misses[" ".join(term)] += 1
                echoes += prompt_echo(predicted)
                matched += 1
            output.append({"reference": reference, "condition": name, "paired_clips": matched,
                           "unmatched_hypothesis_terms": sum(misses.values()),
                           "unmatched_terms_also_in_prompt": sum(prompted_misses.values()),
                           "top_unmatched_terms": sorted(misses.items(), key=lambda pair: (-pair[1], pair[0]))[:10],
                           "literal_prompt_opening_in_hypothesis_clips": echoes})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, {"plan_sha256": manifest["plan_sha256"], "conditions": output})
    print(f"Audited {len(output)} reference-condition pairs")


if __name__ == "__main__":
    main()
