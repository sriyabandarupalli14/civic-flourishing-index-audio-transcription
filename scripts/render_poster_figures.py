"""Render aggregate poster panels from saved comparisons without loading models or audio."""

import argparse
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

REFS = ("large-v3", "wav2vec2")
COLORS = ("#176D75", "#AD4E31")
BLUE = "#214E78"


def comparison(path, clips, conditions):
    data = json.loads(path.read_text())
    if (data["subset"] != "held_out" or data["ready_clips"] != clips
            or set(data["hypothesis_predictions"]) != set(conditions)):
        raise ValueError(f"Unexpected evaluation cohort or conditions in {path}")
    rows = {(r["reference"], r["condition"]): r for r in data["summaries"]}
    if set(rows) != {(ref, key) for ref in REFS for key in conditions} or any(
            row["clips"] != clips for row in rows.values()):
        raise ValueError(f"Incomplete paired results in {path}")
    return rows


def canvas(title, subtitle):
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.5))
    fig.subplots_adjust(left=.075, right=.97, top=.72, bottom=.24, wspace=.30)
    fig.text(.075, .94, title, fontsize=21, weight="bold", va="top", color="#182535")
    fig.text(.075, .855, subtitle, fontsize=11, va="top", color="#405064")
    for ax in axes:
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color("#BAC5CC")
        ax.grid(axis="y", color="#DEE6EC", linewidth=.8)
        ax.set_axisbelow(True)
        ax.tick_params(length=0, pad=6)
    return fig, axes


def scores(ax, rows, keys, labels, metric, title):
    x = np.arange(len(keys))
    peak = 0
    for ref, color, offset in zip(REFS, COLORS, (-.19, .19)):
        values = [100 * rows[ref, key][metric] for key in keys]
        bars = ax.bar(x + offset, values, .34, color=color, label=ref)
        ax.bar_label(bars, labels=[f"{v:.1f}" for v in values], padding=3, fontsize=9)
        peak = max(peak, *values)
    ax.set(xticks=x, xticklabels=labels, ylim=(0, peak * 1.19))
    ax.set_title(title, loc="left", weight="bold", pad=11)
    ax.set_ylabel("Percent")


def finish(fig, output, name, note, legend=True):
    if legend:
        handles, labels = fig.axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, frameon=False, loc="lower left",
                   bbox_to_anchor=(.067, .085), ncol=2)
    fig.text(.075, .041, note, fontsize=8.8, color="#405064")
    for extension in ("pdf", "png"):
        fig.savefig(output / f"{name}.{extension}", dpi=300 if extension == "png" else None,
                    facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, default=Path("outputs/evaluation/runs"))
    parser.add_argument("--follow-up", type=Path, default=Path("outputs/evaluation_v4/report"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    mpl.rcParams.update({"font.family": "DejaVu Sans", "pdf.fonttype": 42, "font.size": 10})
    models = ("base.en", "small.en", "medium.en")
    original_conditions = (*models, *(f"{model}-{strategy}" for model in models
                                     for strategy in ("static", "expanded", "dynamic")))
    original = comparison(args.original / "benchmark/comparison.json", 39, models)
    adaptation = comparison(args.original / "adaptation/comparison.json", 39, original_conditions)
    variants = ("V0", "V3", "V4-A", "V4-B")
    follow_up = comparison(args.follow_up / "comparison.json", 40, variants)
    gate = json.loads((args.follow_up / "diagnostics.json").read_text())
    segments = gate["pre_limit_gate_funnel"]["segments"]
    triggers = gate["pre_limit_gate_funnel"]["diagnostic_trigger"]
    retries = gate["conditions"]["V4-B"]["retries"]
    args.output.mkdir(parents=True, exist_ok=True)

    fig, (left, right) = canvas("Whisper model size and inference cost",
                               "Experiment 1  ·  39 held-out one-minute clips  ·  Unprompted models")
    scores(left, original, models, models, "wer", "Word error rate ↓")
    seconds = [original[REFS[0], model]["median_inference_seconds"] for model in models]
    bars = right.bar(models, seconds, .52, color=BLUE)
    right.bar_label(bars, labels=[f"{v:.2f}" for v in seconds], padding=4)
    right.set(ylim=(0, max(seconds) * 1.19), ylabel="Seconds per clip")
    right.set_title("Median inference time ↓", loc="left", weight="bold", pad=11)
    finish(fig, args.output, "model-benchmark",
           "WER measures agreement with automated references. Timing is local CPU INT8 inference and excludes model load.")

    fig, axes = canvas("Vocabulary prompting reduced small.en term agreement",
                       "Experiment 2  ·  Original cohort  ·  39 held-out one-minute clips")
    keys = ("small.en", "small.en-static", "small.en-expanded", "small.en-dynamic")
    labels = ("V0\nUnprompted", "V1\nStatic", "V2\nExpanded", "V3\nDynamic")
    scores(axes[0], adaptation, keys, labels, "wer", "Word error rate ↓")
    scores(axes[1], adaptation, keys, labels, "term_f1", "Terminology F1 ↑")
    finish(fig, args.output, "vocabulary-original",
           "Both metrics measure agreement with automated references. Terminology F1 uses aligned occurrences.")

    fig, axes = canvas("V4 abstained on every held-out clip",
                       "Experiment 2  ·  Independent follow-up cohort  ·  40 held-out one-minute clips")
    scores(axes[0], follow_up, variants, variants, "wer", "Word error rate ↓")
    scores(axes[1], follow_up, variants, variants, "term_f1", "Terminology F1 ↑")
    finish(fig, args.output, "vocabulary-follow-up",
           f"V4 flagged {triggers} of {segments} segments and ran {retries} retries. Metrics measure agreement "
           "with automated references.")


if __name__ == "__main__":
    main()
