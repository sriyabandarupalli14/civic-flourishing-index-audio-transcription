"""Transcribe verified full recordings from an isolated or standard Stage 3 audio directory."""

import argparse
import json
from collections import Counter
from pathlib import Path

from media_ingestion.pipeline import atomic_json, hash_file, load_manifest
from transcription.batch import run_batch
from transcription.checkpoints import read_checkpoint
from transcription.config import read_config
from transcription.inference import WhisperInference
from transcription.production import stage3_entries
from vocabulary.retrieval import MiniLM, Retrieval, balanced_static, read_lexicon


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("outputs/sermon_collection/stage2/recording_manifest.csv"))
    parser.add_argument("--audio-dir", type=Path, default=Path("outputs/sermon_collection/stage3/audio"))
    parser.add_argument("--recording", action="append", required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/whisper.toml"))
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--vocabulary-strategy", choices=("expanded", "dynamic"))
    parser.add_argument("--lexicon", type=Path, default=Path("configs/religious_lexicon.tsv"))
    parser.add_argument("--embedding-model", type=Path)
    parser.add_argument("--retrieval-method", choices=("exact", "lexical", "semantic", "hybrid"), default="lexical")
    parser.add_argument("--top-k", type=int, default=32)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("outputs/transcription/recordings"))
    args = parser.parse_args(argv)
    entries, excluded = stage3_entries(load_manifest(args.manifest), args.audio_dir, args.recording)
    config = read_config(args.config)
    selector = None
    if args.vocabulary_strategy:
        baseline = config["prompt"].get("vocabulary")
        if not isinstance(baseline, dict) or args.top_k < 1:
            parser.error("Expanded and dynamic mode require a vocabulary and positive top-k")
        lexicon = read_lexicon(args.lexicon)
        config["prompt"].update(mode=args.vocabulary_strategy, hotword_budget=96, top_k=args.top_k,
                                expanded_lexicon_sha256=hash_file(args.lexicon))
        if args.vocabulary_strategy == "expanded":
            terms = balanced_static(lexicon)[:args.top_k]

            def selector(row):
                return terms
        else:
            if args.retrieval_method in {"semantic", "hybrid"} and args.embedding_model is None:
                parser.error("Semantic and hybrid retrieval require --embedding-model")
            encoder = MiniLM(args.embedding_model) if args.embedding_model else None
            retriever = Retrieval(lexicon, encoder)
            config["prompt"].update(retrieval=args.retrieval_method,
                                    embedding=encoder.provenance if encoder else None)

            def selector(row):
                return retriever.rank(row["source_record"], args.retrieval_method, args.top_k, baseline["hotwords"])

    def factory():
        return WhisperInference(config, model_dir=args.model_dir, prompt_selector=selector)

    events, recordings, segments = [], [], []
    for event in run_batch(entries, factory, args.output, shard_count=args.shard_count, shard_index=args.shard_index):
        events.append(event)
        print(json.dumps(event), flush=True)
        if "checkpoint" in event:
            path = args.output / event["checkpoint"]
            identity = json.loads(path.read_text(encoding="utf-8"))["identity"]
            result = read_checkpoint(path, identity)
            if result is None:
                raise ValueError("New transcription checkpoint failed verification")
            review = "needs_review" if result["quality"]["needs_review"] else "review_not_requested"
            info = {"recording_id": event["record_id"], "audio_sha256": identity["audio_sha256"],
                    "configuration_sha256": result["configuration_sha256"], "review_status": review}
            recordings.append({**info, "checkpoint": path.name, "text": result["text"], "quality": result["quality"]})
            segments.extend({**info, **segment} for segment in result["segments"])
    missing = any(entry["reason"] == "no_verified_full_audio" for entry in excluded)
    counts = dict(Counter(event["status"] for event in events))
    shard = f"{args.shard_index}-of-{args.shard_count}"
    summary = {"counts": counts, "events": events, "excluded_media": excluded}
    atomic_json(args.output / f"recordings-{shard}.json", recordings)
    atomic_json(args.output / f"segments-{shard}.json", segments)
    atomic_json(args.output / f"run-{shard}.json", summary)
    print(json.dumps(summary, indent=2))
    return int(missing or not entries or any(event["status"] == "failed" for event in events))



if __name__ == "__main__":
    raise SystemExit(main())
