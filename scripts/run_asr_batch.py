"""Run and checkpoint one local ASR configuration on the evaluation clips."""

import argparse
import json
from pathlib import Path

from evaluation.references import Wav2Vec2Reference
from media_ingestion.pipeline import atomic_json, hash_file
from transcription.batch import run_batch
from transcription.config import read_config
from transcription.inference import WhisperInference
from vocabulary.retrieval import balanced_static, rankings_fingerprint, read_lexicon


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("outputs/evaluation/clip_manifest.json"))
    parser.add_argument("--config", type=Path, default=Path("configs/whisper.toml"))
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--engine", choices=("whisper", "wav2vec2"), default="whisper")
    parser.add_argument("--model", choices=("base.en", "small.en", "medium.en", "large-v3"))
    parser.add_argument("--subset", choices=("development", "held_out"))
    parser.add_argument("--prompt", choices=("none", "static", "expanded", "dynamic", "context", "hotwords"))
    parser.add_argument("--lexicon", type=Path, default=Path("configs/religious_lexicon.tsv"))
    parser.add_argument("--rankings", type=Path)
    parser.add_argument("--retrieval-method", choices=("exact", "lexical", "semantic", "hybrid"), default="hybrid")
    parser.add_argument("--top-k", type=int, default=32)
    parser.add_argument("--hotword-budget", type=int, default=96)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    entries = [{**row, "audio_file": str((args.manifest.parent / row["clip_file"]).resolve()),
                "audio_sha256": row["clip_sha256"], "plan_sha256": manifest["plan_sha256"]}
               for row in manifest["clips"] if row["status"] == "ready"
               and (not args.subset or row["subset"] == args.subset)]
    if not entries or (args.limit is not None and args.limit < 1):
        parser.error("No ready clips selected or invalid limit")
    if args.engine == "wav2vec2":
        if args.model or args.prompt:
            parser.error("Whisper model and prompt overrides do not apply to wav2vec2")

        def factory():
            return Wav2Vec2Reference(args.model_dir)
    else:
        config = read_config(args.config)
        if args.model:
            config["model"]["name"] = args.model
        selector = None
        if args.prompt:
            config["prompt"]["mode"] = args.prompt
            if args.prompt == "none":
                config["decoding"].update(initial_prompt=None, hotwords=None)
            else:
                source = config["prompt"].get("vocabulary")
                if not isinstance(source, dict):
                    parser.error("The config must load a static vocabulary before applying a prompt variant")
                if args.top_k < 1 or args.hotword_budget < 1:
                    parser.error("Prompt budgets must be positive")
                config["prompt"].update(hotword_budget=args.hotword_budget, top_k=args.top_k)
                config["decoding"]["initial_prompt"] = None if args.prompt == "hotwords" else source["initial_prompt"]
                if args.prompt == "dynamic":
                    if not args.rankings:
                        parser.error("Dynamic prompting requires precomputed metadata rankings")
                    data = json.loads(args.rankings.read_text(encoding="utf-8"))
                    if data["plan_sha256"] != manifest["plan_sha256"] or data["lexicon_sha256"] != hash_file(args.lexicon):
                        parser.error("Rankings belong to a different evaluation sample or lexicon")
                    ranking_sha = rankings_fingerprint(data)
                    if data["rankings_sha256"] != ranking_sha:
                        parser.error("Metadata ranking artifact failed integrity verification")
                    ranks = data["rankings"]
                    config["prompt"].update(rankings_sha256=ranking_sha, retrieval=args.retrieval_method)

                    def selector(metadata):
                        return ranks[metadata["clip_id"]][args.retrieval_method][:args.top_k]
                else:
                    terms = [] if args.prompt == "context" else source["hotwords"]
                    if args.prompt == "expanded":
                        terms = balanced_static(read_lexicon(args.lexicon))
                        config["prompt"]["expanded_lexicon_sha256"] = hash_file(args.lexicon)

                    def selector(metadata):
                        return terms[:args.top_k]

        def factory():
            return WhisperInference(config, model_dir=args.model_dir, prompt_selector=selector)

    report = []
    for event in run_batch(entries[:args.limit], factory, args.output, shard_count=args.shard_count,
                           shard_index=args.shard_index):
        report.append(event)
        print(json.dumps(event), flush=True)
    atomic_json(args.output / f"run-{args.shard_index}-of-{args.shard_count}.json", report)
    return int(any(event["status"] == "failed" for event in report))


if __name__ == "__main__":
    raise SystemExit(main())
