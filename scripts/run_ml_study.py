"""Run the frozen local study sequentially, preserving resumable checkpoints."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from subprocess import TimeoutExpired, run

from evaluation.metrics import index_terms
from evaluation.results import compare, load_predictions, pool
from media_ingestion.pipeline import atomic_json, hash_file
from vocabulary.retrieval import rankings_fingerprint, read_lexicon

MODELS = ("base.en", "small.en", "medium.en")
REFERENCES = ("wav2vec2", "large-v3")
DEV_STRATEGIES = (
    ("static", 32), ("expanded", 32), ("context", 32), ("hotwords", 32),
    ("exact", 32), ("lexical", 16), ("lexical", 32), ("semantic", 32), ("hybrid", 16), ("hybrid", 32),
)


def choose_development(manifest, root, lexicon_path):
    expected = {row["clip_id"]: row for row in manifest["clips"]
                if row["subset"] == "development" and row["status"] == "ready"}
    checksum = {key: row["clip_sha256"] for key, row in expected.items()}
    refs = {name: load_predictions(root / name, checksum) for name in REFERENCES if (root / name).is_dir()}
    index = index_terms([row["term"] for row in read_lexicon(lexicon_path)])
    tuning = []
    for method, top_k in DEV_STRATEGIES:
        label = f"dev-{method}-{top_k}"
        candidates = load_predictions(root / label, checksum)
        scores = {ref: pool(compare(expected, candidates, results, index)) for ref, results in refs.items()}
        tuning.append({"method": method, "top_k": top_k, "scores": scores})
    active = tuple(ref for ref, results in refs.items() if len(results) == len(expected))

    def complete(row):
        return active and all((result := row["scores"][ref])["clips"] == len(expected)
                              and result["term_f1"] is not None and result["wer"] is not None for ref in active)

    def performance(row):
        f1 = sum(row["scores"][ref]["term_f1"] for ref in active) / len(active)
        wer = sum(row["scores"][ref]["wer"] for ref in active) / len(active)
        return -f1, wer, row["top_k"], row["method"]

    eligible = [row for row in tuning if row["method"] in {"exact", "lexical", "semantic", "hybrid"} and complete(row)]
    selection = min(eligible, key=performance) if eligible else {"method": "hybrid", "top_k": 32}
    return {"retrieval_method": selection["method"], "top_k": selection["top_k"], "hotword_budget": 96,
            "references_used": active, "development_clip_count": len(expected),
            "plan_sha256": manifest["plan_sha256"], "lexicon_sha256": hash_file(lexicon_path),
            "selection_method": "highest mean term F1, then lower mean WER, smaller K and method name"
            if eligible else "predeclared hybrid fallback",
            "development_scores": tuning}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("outputs/evaluation/clip_manifest.json"))
    parser.add_argument("--rankings", type=Path, default=Path("outputs/evaluation/vocabulary_rankings.json"))
    parser.add_argument("--lexicon", type=Path, default=Path("configs/religious_lexicon.tsv"))
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--base-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/evaluation/runs"))
    parser.add_argument("--hours", type=float, default=6)
    parser.add_argument("--resume-after-baselines", action="store_true")
    args = parser.parse_args(argv)
    if not 0 < args.hours <= 6:
        parser.error("Budget must be between zero and six hours")
    deadline = time.monotonic() + 3600 * args.hours
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", OMP_NUM_THREADS="4")
    ledger = []
    ledger_path = args.output / "study_ledger.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    def record(label, status):
        ledger.append({"stage": label, "status": status})
        atomic_json(ledger_path, ledger)
        print(f"{label}: {status}", flush=True)

    def execute(module, cli, label):
        if (remaining := deadline - time.monotonic()) <= 0:
            record(label, "budget_exhausted")
            return
        with (args.output / f"{label}.log").open("a", encoding="utf-8") as handle:
            try:
                code = run([sys.executable, "-m", module, *map(str, cli)], stdout=handle, stderr=handle,
                           env=env, timeout=remaining, check=False).returncode
                record(label, "completed" if code == 0 else f"exit_{code}")
            except TimeoutExpired:
                record(label, "timed_out")

    def asr(model, label, *extra):
        snapshots = sorted(p for p in (args.models / model).glob("*") if p.is_dir())
        snapshot = args.base_snapshot if model == "base.en" else (snapshots[0] if len(snapshots) == 1 else None)
        if snapshot is None:
            record(label, "model_snapshot_missing_or_ambiguous")
            return
        engine = ("--engine", "wav2vec2") if model == "wav2vec2" else ("--model", model)
        execute("scripts.run_asr_batch",
                ("--manifest", args.manifest, "--model-dir", snapshot, "--output", args.output / label,
                 "--lexicon", args.lexicon, *engine, *extra), label)

    if not args.resume_after_baselines:
        asr("wav2vec2", "wav2vec2")
        for model in (*MODELS, "large-v3"):
            asr(model, model, "--prompt", "none")

    def score(label, conditions, subset="held_out", pairs=()):
        references = [item for ref in REFERENCES if (args.output / ref).is_dir()
                      for item in ("--reference", f"{ref}={args.output / ref}")]
        if not references:
            record(label, "no_reference_checkpoints")
            return
        hypotheses = [item for condition in conditions for item in
                      ("--prediction", f"{condition}={args.output / condition}")]
        comparisons = [item for first, second in pairs for item in ("--compare", f"{first}={second}")]
        execute("scripts.score_asr", ("--manifest", args.manifest, "--lexicon", args.lexicon,
                "--subset", subset, "--output", args.output / label, *references, *hypotheses, *comparisons), label)

    score("benchmark", MODELS, pairs=(("small.en", "base.en"), ("medium.en", "small.en")))
    for method, top_k in DEV_STRATEGIES:
        dynamic = method in {"exact", "lexical", "semantic", "hybrid"}
        controls = ("--rankings", args.rankings, "--retrieval-method", method) if dynamic else ()
        asr("base.en", f"dev-{method}-{top_k}", "--subset", "development", "--top-k", top_k,
            "--prompt", "dynamic" if dynamic else method, *controls)
    if time.monotonic() >= deadline:
        record("freeze", "budget_exhausted")
        return
    freeze_path = args.output / "frozen_prompt.json"
    computed = choose_development(manifest, args.output, args.lexicon)
    computed["rankings_sha256"] = rankings_fingerprint(json.loads(args.rankings.read_text(encoding="utf-8")))
    if freeze_path.exists():
        selected = json.loads(freeze_path.read_text(encoding="utf-8"))
        if any(selected[key] != computed[key] for key in ("plan_sha256", "lexicon_sha256", "rankings_sha256")):
            raise ValueError("Frozen tuning settings belong to a different dataset or lexicon")
    else:
        selected = computed
        atomic_json(freeze_path, selected)
    score("development", ("base.en", *(f"dev-{method}-{k}" for method, k in DEV_STRATEGIES)), "development")

    conditions = [*MODELS]
    for model in MODELS:
        for strategy in ("static", "expanded", "dynamic"):
            label = f"{model}-{strategy}"
            controls = ("--rankings", args.rankings, "--retrieval-method", selected["retrieval_method"],
                        "--top-k", selected["top_k"]) if strategy == "dynamic" else ()
            asr(model, label, "--subset", "held_out", "--prompt", strategy, *controls)
            conditions.append(label)
    score("adaptation", conditions,
          pairs=tuple((f"{model}-{strategy}", larger) for model, larger in
                      (("base.en", "small.en"), ("base.en", "medium.en"), ("small.en", "medium.en"))
                      for strategy in ("static", "expanded", "dynamic")))
    execute("scripts.render_ml_report", ("--manifest", args.manifest, "--runs", args.output), "report")


if __name__ == "__main__":
    main()
