"""Render a research report exclusively from verified study summaries and coverage."""

import argparse
import json
from pathlib import Path


def percentage(value):
    return "—" if value is None else f"{100 * value:.1f}%"


def number(value, scale=1):
    return "—" if value is None else f"{value / scale:.2f}"


def add_section(lines, path, label):
    lines.extend([f"## {label}", ""])
    if not path.is_file():
        lines.extend(["No scored result is available for this section.", ""])
        return None
    comparison = json.loads(path.read_text(encoding="utf-8"))
    lines.extend([
        f"Subset: {comparison['subset']}. Ready clips: {comparison['ready_clips']}. Metrics are relative to each named automated reference.",
        "",
        "| Reference | Condition | Paired / ready | WER | CER | Term P / R / F1 | S / D / I | False term insertions | Median s | RTF | Median peak RSS MiB | Load s |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in comparison["summaries"]:
        terms = " / ".join(percentage(row[key]) for key in ("term_precision", "term_recall", "term_f1"))
        edits = " / ".join(str(row[key]) for key in ("substitutions", "deletions", "insertions"))
        lines.append(
            f"| {row['reference']} | {row['condition']} | {row['clips']} / {comparison['ready_clips']}"
            f" | {percentage(row['wer'])} | {percentage(row['cer'])} | {terms} | {edits}"
            f" | {row['false_term_insertions']} | {number(row['median_inference_seconds'])}"
            f" | {number(row['median_real_time_factor'])} | {number(row['median_peak_rss_bytes'], 2**20)}"
            f" | {number(row['median_model_load_seconds'])} |"
        )
    lines.extend(["", f"See [{label} per-church results]({path.parent.name}/per_church.csv) and "
                  f"[verified comparison details]({path.parent.name}/comparison.json) for denominators, "
                  "coverage and paired church-cluster intervals.", ""])
    return comparison


def recommend(benchmark, adaptation):
    if not benchmark:
        return "base.en, CPU INT8 with unprompted decoding, is a provisional smoke-tested configuration."
    refs = set(benchmark["reference_predictions"])
    by_candidate = {}
    for row in benchmark["summaries"]:
        if row["missing_pairs"] == 0 and row["wer"] is not None:
            by_candidate.setdefault(row["condition"], {})[row["reference"]] = row
    complete = {model: rows for model, rows in by_candidate.items() if refs == rows.keys()}
    if not complete:
        return "Evidence is incomplete. Keep the smoke-tested base.en unprompted configuration provisional."
    best = min(complete, key=lambda model: sum(row["wer"] for row in complete[model].values()) / len(refs))
    if not adaptation or len(refs) != 2:
        return (f"**{best} unprompted** has the lowest mean reference-relative WER among complete baselines. "
                "The adaptation evidence is incomplete, so the production setting remains provisional.")
    supporting = {}
    for row in adaptation["paired_comparisons"]:
        if row["second"] == best and row["meets_comparability_thresholds"] is True:
            supporting.setdefault(row["first"], set()).add(row["reference"])
    eligible = {label for label, evidence in supporting.items() if evidence == refs}
    by_label = {(row["condition"], row["reference"]): row for row in adaptation["summaries"]}
    candidates = []
    for label in sorted(eligible):
        baseline = label.split("-", 1)[0]
        if baseline in eligible and label != baseline:
            gains = [by_label[label, ref]["term_f1"] - by_label[baseline, ref]["term_f1"] for ref in refs]
            if min(gains) < 0 or not any(gain > 0 for gain in gains):
                continue
        runtime = by_label[label, sorted(refs)[0]]["median_inference_seconds"]
        candidates.append((runtime, label))
    if candidates:
        selected = min(candidates)[1]
        return (f"The provisional configuration is **{selected}**. It meets the prespecified domain F1, WER and "
                f"runtime thresholds against {best} on both references. Where an unprompted configuration already "
                "qualifies, a prompted configuration is preferred only when it improves term F1 on at least one "
                "reference without lowering it on the other. Comparability alone does not establish prompt benefit.")
    return (f"**{best} unprompted** has the lowest mean reference-relative WER among complete baselines. "
            "No smaller tested setting met the prespecified criteria against it on both references.")



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("outputs/evaluation/clip_manifest.json"))
    parser.add_argument("--runs", type=Path, default=Path("outputs/evaluation/runs"))
    parser.add_argument("--rankings", type=Path, default=Path("outputs/evaluation/vocabulary_rankings.json"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    coverage = manifest["coverage"]
    lines = [
        "# Civic Flourishing Index local ASR study", "",
        "H1 tests whether larger Whisper models improve agreement at greater computational cost. "
        "H2 tests whether domain prompts let a smaller model approach a larger baseline at lower cost.", "",
        "## Frozen sample and methods", "",
        f"{coverage['ready_clips']} of {coverage['target_clips']} planned clips were ready across "
        f"{coverage['ready_churches']} churches. Development: {coverage['ready_by_subset'].get('development', 0)}. "
        f"Held-out: {coverage['ready_by_subset'].get('held_out', 0)}. The plan SHA-256 is "
        f"`{manifest['plan_sha256']}`. Audio windows are 30–90 seconds and disjoint by provisional recording.", "",
        "Candidate sizes use faster-whisper and CTranslate2. The baseline uses beam size five and temperature zero, "
        "with prompt, previous-text conditioning and VAD disabled. Reference generation is local. Large-v3 uses "
        "Whisper with unprompted decoding, and wav2vec2-base-960h uses overlapping CTC chunks and greedy decoding.", "",
        "V0 is unprompted. Static uses the original list, expanded interleaves lexicon categories, and dynamic "
        "uses precomputed metadata-only rankings. The hotword cap is 96 tokens. Inference time excludes model load "
        "and precomputed retrieval, which have separate timing artifacts. Memory is sampled CPU process RSS.", "",
    ]
    benchmark = add_section(lines, args.runs / "benchmark/comparison.json", "Benchmark")
    add_section(lines, args.runs / "development/comparison.json", "Development")
    freeze = args.runs / "frozen_prompt.json"
    if freeze.is_file():
        selected = json.loads(freeze.read_text(encoding="utf-8"))
        lines.extend([f"Frozen dynamic route: **{selected['retrieval_method']}**, K={selected['top_k']}, "
                      f"{selected['hotword_budget']} token cap. Selection used development clips.", ""])
        if args.rankings.is_file():
            artifact = json.loads(args.rankings.read_text(encoding="utf-8"))
            if artifact.get("rankings_sha256") == selected.get("rankings_sha256"):
                lines.extend(["### Retrieval examples", ""])
                method = selected["retrieval_method"]
                examples = [clip for clip in manifest["clips"] if clip["status"] == "ready"
                            and clip["subset"] == "development" and clip["source_record"].get("title")][:2]
                for clip in examples:
                    terms = artifact["rankings"][clip["clip_id"]][method][:5]
                    lines.append(f"- {clip['source_record']['title']} → {', '.join(terms)}")
                lines.append("")
    adaptation = add_section(lines, args.runs / "adaptation/comparison.json", "Adaptation")
    lines.extend(["## Reference disagreement and decision", ""])
    if benchmark:
        for row in benchmark["reference_disagreement"]:
            lines.append(f"- {row['comparison']} relative to {row['reference']}, {row['clips']} paired clips, "
                         f"WER {percentage(row['wer'])}, CER {percentage(row['cer'])}, "
                         f"domain F1 {percentage(row['term_f1'])}.")
        lines.append("")
    lines.extend([recommend(benchmark, adaptation), ""])
    if adaptation:
        lines.extend(["## Prompt effects relative to each model's baseline", "",
                      "| Reference | Prompted condition | Paired WER change (pp) | Term F1 change (pp) |",
                      "| --- | --- | ---: | ---: |"])
        for row in adaptation["paired_comparisons"]:
            if (not row["first"].startswith(row["second"] + "-")
                    or row.get("paired_delta_wer") is None or row.get("paired_delta_term_f1") is None):
                continue
            lines.append(f"| {row['reference']} | {row['first']} | {100 * row['paired_delta_wer']:+.2f}"
                         f" | {100 * row['paired_delta_term_f1']:+.2f} |")
        lines.append("")
    lines.extend(["## Limitations", "",
                  "Automated references can share errors and can differ on silence, singing, names, contractions, "
                  "numbers and word boundaries. These results measure agreement only. Domain counts match literal "
                  "lexicon terms and can include common words used outside religious contexts. The purposive sample "
                  "favors accessible direct audio and one early minute per sermon. The subsets share churches, "
                  "and recording-level separation cannot verify that every speaker is unique. The sample and "
                  "church-cluster intervals do not establish corpus-wide or human-validated performance.", ""])
    args.runs.mkdir(parents=True, exist_ok=True)
    report = args.runs / "ML_RESEARCH_REPORT.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
