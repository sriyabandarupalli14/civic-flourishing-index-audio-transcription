"""Verify V4 checkpoints and rebuild the held-out metrics, diagnostics and report."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from evaluation.metrics import index_terms
from evaluation.v4_analysis import checkpoint_set, clips_for, coverage, diagnostics
from media_ingestion.pipeline import atomic_json, hash_file
from vocabulary.retrieval import read_lexicon

LABELS = ("V0", "V3", "V4-A", "V4-B", "medium")
REFERENCES = ("large-v3", "wav2vec2")


def percent(value):
    return "—" if value is None else f"{value * 100:.2f}%"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    args = parser.parse_args()
    root = args.data
    report = root / "report"
    report.mkdir(exist_ok=True)
    manifest = json.loads((root / "clip_manifest.json").read_text())
    old = json.loads(Path("outputs/evaluation/clip_manifest.json").read_text())["clips"]
    old_ids = {row["recording_id"] for row in old}
    old_sha = {row.get("clip_sha256") for row in old}
    for row in manifest["clips"]:
        if row["status"] == "ready" and (row["recording_id"] in old_ids or row["clip_sha256"] in old_sha
                                        or hash_file(root / row["clip_file"]) != row["clip_sha256"]):
            raise ValueError("Audio is duplicated from the earlier study or failed checksum verification")
    terms = index_terms(item["term"] for item in read_lexicon(Path("configs/religious_lexicon.tsv")))
    samples = {subset: clips_for(root, subset) for subset in ("development", "held_out")}
    held = samples["held_out"]
    refs = {}
    for subset, clips in samples.items():
        refs[subset] = {}
        for name in REFERENCES:
            predictions = checkpoint_set(root / "runs" / name, clips)
            if set(predictions) != set(clips):
                raise ValueError(f"Incomplete {name} reference coverage for {subset}")
            refs[subset][name] = coverage(predictions, terms)
    outputs = {label: checkpoint_set(root / "runs/held_out" / label, held) for label in LABELS}
    if any(set(rows) != set(held) for rows in outputs.values()):
        raise ValueError("Held-out predictions must be complete and paired")
    stats = {label: diagnostics(runs) for label, runs in outputs.items()}
    # The V4 control and treatment must share the selected segments and candidate gate.
    for key in held:
        control, treatment = (outputs[label][key]["adaptation"]["events"] for label in ("V4-A", "V4-B"))
        def gates(events):
            return [(row["segment"], row["plausible_terms"], row["span_sha256"]) for row in events]
        if gates(control) != gates(treatment):
            raise ValueError("V4-A and V4-B retry different spans")
    command = [sys.executable, "-m", "scripts.score_asr", "--manifest", str(root / "clip_manifest.json"),
               "--output", str(report)]
    for name in REFERENCES:
        command += ["--reference", f"{name}={root / 'runs' / name}"]
    for label in LABELS:
        command += ["--prediction", f"{label}={root / 'runs/held_out' / label}"]
    for a, b in (("V4-A", "V0"), ("V4-B", "V0"), ("V4-B", "V4-A"), ("V4-B", "medium")):
        command += ["--compare", f"{a}={b}"]
    subprocess.run(command, check=True)
    comparison = json.loads((report / "comparison.json").read_text())
    freeze = json.loads((root / "frozen_policy.json").read_text())
    paired = {(row["reference"], row["condition"]): row for row in comparison["summaries"]}
    faster = 1 - stats["V4-B"]["median_seconds"] / stats["medium"]["median_seconds"]
    improved = all(paired[ref, "V4-B"]["term_f1"] is not None
                   and paired[ref, "V4-B"]["term_f1"] > paired[ref, control]["term_f1"]
                   and paired[ref, "V4-B"]["wer"] <= paired[ref, control]["wer"]
                   for ref in REFERENCES for control in ("V0", "V4-A"))
    errors = []
    for key in held:
        before, after = [outputs[label][key] for label in ("V4-A", "V4-B")]
        for event in after["adaptation"]["events"]:
            if event["outcome"] != "empty_or_unchanged":
                errors.append({"clip_id": key, **event,
                               "control_outcome": next(r["outcome"] for r in before["adaptation"]["events"]
                                                       if r["segment"] == event["segment"])})
    atomic_json(report / "diagnostics.json", {"coverage": refs, "conditions": stats,
        "speed_advantage_over_medium": faster, "improved_over_both": improved, "freeze": freeze})
    atomic_json(report / "retry_examples.json", errors)
    lines = ["# Experiment 2 extension: selective V4 adaptation", "",
             f"The independent sample contains {len(samples['development'])} development and {len(held)} held-out "
             f"clips across {manifest['coverage']['ready_churches']} churches. The frozen policy was "
             f"`{freeze['name']}`. Metrics measure agreement with independent automated references.", "",
             "## Reference terminology coverage", "",
             "| Subset | Reference | Occurrences | Distinct terms | Clips with terms |",
             "| --- | --- | ---: | ---: | ---: |"]
    for subset, group in refs.items():
        for name, row in group.items():
            lines.append(f"| {subset} | {name} | {row['occurrences']} | {row['distinct_terms']} | {row['clips_with_terms']} |")
    lines += ["", "## Held-out agreement", "",
              "| Reference | Condition | WER | CER | Term precision | Term recall | Term F1 | Unmatched terms |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for ref in REFERENCES:
        for label in LABELS:
            row = paired[ref, label]
            figures = " | ".join(percent(row[k]) for k in ("wer", "cer", "term_precision", "term_recall", "term_f1"))
            lines.append(f"| {ref} | {label} | {figures} | {row['false_term_insertions']} |")
    lines += ["", "## Reference disagreement", ""]
    for row in comparison["reference_disagreement"]:
        lines.append(f"- {row['comparison']} relative to {row['reference']} produced {percent(row['wer'])} WER, "
                     f"{percent(row['cer'])} CER and {percent(row['term_f1'])} term F1.")
    lines += ["", "## Runtime and retry behavior", "",
              "| Condition | Median seconds | Median peak RSS MiB | Clips with a loop | Retried segments | Accepted retries |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for label, row in stats.items():
        lines.append(f"| {label} | {row['median_seconds']:.2f} | {row['median_peak_rss_mib']:.1f} "
                     f"| {row['repeat_clips']} | {row['retries']}/{row['segments']} ({percent(row['retry_rate'])}) "
                     f"| {row['accepted']}/{row['retries']} ({percent(row['acceptance_rate'])}) |")
    lines += ["", f"V4-B used {percent(faster)} less median time than medium.en. "
              f"The joint improvement check against V0 and V4-A {'passed' if improved else 'did not pass'}.", "",
              f"V4-B supplied hints on {stats['V4-B']['prompted_retries']} of {stats['V4-B']['retries']} held-out retries.",
              "See the retry examples for span-level hypotheses and rejection reasons.", "",
              "The sample enriches terminology through metadata. Shared churches, imperfect references, and near-ceiling "
              "baseline terminology recall limit conclusions about generalization. Sampled process RSS and local CPU "
              "timings characterize this workstation and configuration.", ""]
    (report / "V4_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(report / "V4_REPORT.md")


if __name__ == "__main__":
    main()
