"""Compute reproducible reference-relative metrics and publication-ready summaries."""

import argparse
import csv
import json
from pathlib import Path

from evaluation.metrics import index_terms
from evaluation.results import compare, load_predictions, paired_intervals, pool
from media_ingestion.pipeline import atomic_json, hash_file
from vocabulary.retrieval import read_lexicon


def named_paths(values):
    return dict((label, Path(path)) for label, path in (value.split("=", 1) for value in values))


def plot(groups, output):
    if not groups:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    axes_settings = [
        ("wer", "Reference-relative WER (%)", 100),
        ("term_f1", "Reference-relative domain-term F1 (%)", 100),
        ("median_inference_seconds", "Median inference time (s)", 1),
        ("median_peak_rss_bytes", "Median sampled peak RSS (MiB)", 1 / 2**20),
    ]
    references = sorted({row["reference"] for row in groups})
    figure, axes = plt.subplots(len(references), 4, figsize=(15, 4 * len(references)), squeeze=False,
                                layout="constrained")
    for reference, row_axes in zip(references, axes):
        rows = [row for row in groups if row["reference"] == reference]
        for axis, (metric, title, factor) in zip(row_axes, axes_settings):
            finite = [row for row in rows if row[metric] is not None]
            bars = axis.barh([f"{row['condition']} ({row['clips']})" for row in finite],
                             [row[metric] * factor for row in finite], color="#267a80")
            axis.bar_label(bars, fmt="%.2f", padding=3, fontsize=8)
            axis.margins(x=0.2)
            axis.invert_yaxis()
            axis.set_title(f"{reference}\n{title}", fontsize=10)
            axis.grid(axis="x", alpha=0.2)
    figure.suptitle("ASR agreement with automated references and inference cost", fontsize=14)
    figure.savefig(output / "asr_comparison.png", dpi=160)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8, 5), layout="constrained")
    for reference in references:
        relevant = [row for row in groups if row["reference"] == reference and row["wer"] is not None]
        axis.scatter([row["median_inference_seconds"] for row in relevant],
                     [100 * row["wer"] for row in relevant], label=reference, alpha=0.75)
        for row in relevant:
            axis.annotate(row["condition"], (row["median_inference_seconds"], 100 * row["wer"]),
                          xytext=(4, 4), textcoords="offset points", fontsize=7)
    axis.set(xlabel="Median inference time per clip (seconds)", ylabel="Reference-relative WER (%)",
             title="Agreement versus computational cost")
    axis.legend()
    axis.grid(alpha=0.2)
    figure.savefig(output / "agreement_cost.png", dpi=160)
    plt.close(figure)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("outputs/evaluation/clip_manifest.json"))
    parser.add_argument("--lexicon", type=Path, default=Path("configs/religious_lexicon.tsv"))
    parser.add_argument("--reference", action="append", required=True, help="label=checkpoint-directory")
    parser.add_argument("--prediction", action="append", required=True, help="label=checkpoint-directory")
    parser.add_argument("--compare", action="append", default=[], help="first-label=second-label")
    parser.add_argument("--subset", choices=("development", "held_out"), default="held_out")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--without-plots", action="store_true")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected = {row["clip_id"]: row for row in manifest["clips"] if row["status"] == "ready"
                and row["subset"] == args.subset}
    if not expected:
        parser.error("No ready clips in the requested subset")
    checksums = {key: row["clip_sha256"] for key, row in expected.items()}
    refs = {label: load_predictions(path, checksums) for label, path in named_paths(args.reference).items()}
    preds = {label: load_predictions(path, checksums) for label, path in named_paths(args.prediction).items()}
    lexicon = index_terms([row["term"] for row in read_lexicon(args.lexicon)])
    rows, summaries, church, details = [], [], [], {}
    for reference, ref_results in refs.items():
        for condition, pred_results in preds.items():
            samples = compare(expected, pred_results, ref_results, lexicon)
            details[(reference, condition)] = samples
            rows.extend({"reference": reference, "condition": condition, **row} for row in samples)
            identifier = {"reference": reference, "condition": condition}
            summaries.append({**identifier, **pool(samples), "missing_pairs": len(expected) - len(samples)})
            for number in sorted({row["church_number"] for row in samples}):
                church.append({**identifier, "church_number": number,
                               **pool([row for row in samples if row["church_number"] == number])})
    disagreement = [{"reference": left, "comparison": right,
                     **pool(compare(expected, second, first, lexicon))}
                    for left, first in refs.items() for right, second in refs.items() if left != right]
    pairs = []
    for pair in args.compare:
        left, right = pair.split("=", 1)
        for reference in refs:
            a, b = details[(reference, left)], details[(reference, right)]
            complete = len(a) == len(b) == len(expected)
            pa, pb = pool(a), pool(b)
            sufficient = (complete and pa["reference_terms"] >= 20 and pb["reference_terms"] >= 20
                          and all(p[metric] is not None for p in (pa, pb) for metric in ("wer", "term_f1")))
            criteria = None
            if sufficient:
                criteria = (pa["term_f1"] >= pb["term_f1"] - 0.05 and pa["wer"] <= pb["wer"] + 0.03
                            and pa["median_inference_seconds"] <= 0.8 * pb["median_inference_seconds"])
            pairs.append({"reference": reference, "first": left, "second": right,
                          "matched_clips": len({row["clip_id"] for row in a} & {row["clip_id"] for row in b}),
                          "intervals_first_minus_second": paired_intervals(a, b),
                          "meets_comparability_thresholds": criteria})
    args.output.mkdir(parents=True, exist_ok=True)
    for name, result in (("per_clip", rows), ("summary", summaries), ("per_church", church)):
        if not result:
            continue
        with (args.output / (name + ".csv")).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(result[0]))
            writer.writeheader()
            writer.writerows(result)
    atomic_json(args.output / "comparison.json", {
        "metric_scope": "agreement with automated pseudo-references",
        "subset": args.subset, "plan_sha256": manifest["plan_sha256"], "lexicon_sha256": hash_file(args.lexicon),
        "ready_clips": len(expected), "reference_predictions": {key: len(value) for key, value in refs.items()},
        "hypothesis_predictions": {key: len(value) for key, value in preds.items()},
        "summaries": summaries, "reference_disagreement": disagreement, "paired_comparisons": pairs,
    })
    if not args.without_plots:
        plot([row for row in summaries if row["clips"]], args.output)
    print(json.dumps({"subset": args.subset, "clips": len(expected), "scored_pairs": len(rows)}))


if __name__ == "__main__":
    main()
