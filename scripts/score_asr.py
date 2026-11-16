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

    references = sorted({row["reference"] for row in groups})
    palette = {"base.en": "#287678", "small.en": "#425c9b", "medium.en": "#9a541c"}

    def draw(axis, rows, metric, title, factor, width):
        finite = [row for row in rows if row[metric] is not None]
        labels = [f"{row['condition']} ({row['clips']})" for row in finite]
        bars = axis.barh(labels, [row[metric] * factor for row in finite],
                         color=[palette.get(row["condition"].split("-", 1)[0], "#287678") for row in finite])
        axis.bar_label(bars, fmt="%.2f", padding=3, fontsize=8)
        axis.set_xlim(0, width)
        axis.invert_yaxis()
        axis.set_title(title, fontsize=10)
        axis.grid(axis="x", alpha=0.18)

    quality = [("wer", "WER (%)"), ("cer", "CER (%)"), ("term_f1", "Domain-term F1 (%)")]
    row_count = max(sum(row["reference"] == reference for row in groups) for reference in references)
    height = max(3.0, 1.5 + row_count * 0.24)
    figure, axes = plt.subplots(len(references), 3, figsize=(14, height * len(references)),
                                squeeze=False, layout="constrained")
    for reference, row_axes in zip(references, axes):
        rows = [row for row in groups if row["reference"] == reference]
        for axis, (metric, title) in zip(row_axes, quality):
            width = max((100 * row[metric] for row in groups if row[metric] is not None), default=1) * 1.23
            draw(axis, rows, metric, f"{reference} | {title}", 100, max(width, 1))
    figure.suptitle("Reference-relative ASR agreement across held or development clips", fontsize=14)
    figure.savefig(output / "asr_comparison.png", dpi=160)
    plt.close(figure)

    figures = ("median_inference_seconds", "median_peak_rss_bytes", "median_model_load_seconds")
    titles = ("Median inference time (s)", "Median sampled peak RSS (MiB)", "Model loading time (s)")
    factors = (1, 1 / 2**20, 1)
    # Resource observations are shared across both reference comparisons.
    resource_rows = {}
    for row in groups:
        label = row["condition"]
        if label not in resource_rows or row["clips"] > resource_rows[label]["clips"]:
            resource_rows[label] = row
    figure, axes = plt.subplots(1, 3, figsize=(14, height), layout="constrained")
    for axis, metric, title, factor in zip(axes, figures, titles, factors):
        width = max((factor * row[metric] for row in resource_rows.values() if row[metric] is not None), default=1)
        draw(axis, list(resource_rows.values()), metric, title, factor, max(width * 1.23, 0.1))
    figure.suptitle("Local CPU resource measurements", fontsize=14)
    figure.savefig(output / "runtime_memory.png", dpi=160)
    plt.close(figure)

    figure, axes = plt.subplots(1, len(references), figsize=(6 * len(references), 4.5),
                                squeeze=False, sharex=True, sharey=True, layout="constrained")
    markers = {"static": "s", "expanded": "^", "dynamic": "D", "baseline": "o"}
    for axis, reference in zip(axes[0], references):
        relevant = [row for row in groups if row["reference"] == reference and row["wer"] is not None]
        for row in relevant:
            parts = row["condition"].split("-", 1)
            variant = parts[1] if len(parts) == 2 else "baseline"
            axis.scatter(row["median_inference_seconds"], 100 * row["wer"], label=row["condition"],
                         marker=markers.get(variant, "o"), color=palette.get(parts[0], "#287678"), alpha=0.8)
        axis.set(xlabel="Median inference time per clip (seconds)", title=reference)
        axis.grid(alpha=0.18)
    axes[0][0].set_ylabel("Reference-relative WER (%)")
    handles, labels = axes[0][-1].get_legend_handles_labels()
    if handles:
        axes[0][-1].legend(handles, labels, fontsize=7, ncol=2 if len(labels) > 7 else 1)
    figure.suptitle("Reference agreement versus local inference cost")
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
                          "paired_delta_wer": pa["wer"] - pb["wer"] if complete and pa["wer"] is not None
                          and pb["wer"] is not None else None,
                          "paired_delta_term_f1": pa["term_f1"] - pb["term_f1"] if complete
                          and pa["term_f1"] is not None and pb["term_f1"] is not None else None,
                          "runtime_ratio": pa["median_inference_seconds"] / pb["median_inference_seconds"]
                          if complete and pb["median_inference_seconds"] else None,
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
