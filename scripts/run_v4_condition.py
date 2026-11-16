"""Run a single V4 comparison condition with measured end-to-end inference."""

import argparse
import json
from pathlib import Path

from media_ingestion.pipeline import atomic_json
from transcription.adaptive import AdaptiveInference
from transcription.batch import run_batch
from transcription.checkpoints import digest
from transcription.config import read_config
from transcription.inference import WhisperInference, measure_resources
from vocabulary.retrieval import MiniLM, Retrieval, read_lexicon

CONDITIONS = ("V0", "V3", "V4-A", "V4-B", "medium")


class MeasuredBaseline:
    """Include live metadata retrieval in V3 and measure both baselines identically."""

    def __init__(self, engine):
        self.engine = engine
        self.configuration_sha256 = engine.configuration_sha256

    def transcribe(self, audio, *, metadata=None, expected_sha256=None):
        with measure_resources() as resources:
            result = self.engine.transcribe(audio, metadata=metadata, expected_sha256=expected_sha256)
        return {**result, "inference": resources}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("condition", choices=CONDITIONS)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--subset", required=True, choices=("development", "held_out"))
    parser.add_argument("--policy", default=None)
    parser.add_argument("--label")
    args = parser.parse_args()
    manifest = json.loads((args.data / "clip_manifest.json").read_text())
    entries = [{**row, "audio_file": str((args.data / row["clip_file"]).resolve()), "audio_sha256": row["clip_sha256"],
                "plan_sha256": manifest["plan_sha256"]}
               for row in manifest["clips"] if row["status"] == "ready" and row["subset"] == args.subset]
    lexicon = read_lexicon(Path("configs/religious_lexicon.tsv"))
    policy = None
    if args.condition.startswith("V4"):
        policies = json.loads(Path("configs/v4_policies.json").read_text())
        if args.subset == "held_out":
            freeze = json.loads((args.data / "frozen_policy.json").read_text())
            if freeze["plan_sha256"] != manifest["plan_sha256"] or freeze["policy"] != policies[freeze["name"]]:
                raise ValueError("Frozen policy belongs to a different dataset or configuration")
            if args.policy and args.policy != freeze["name"]:
                raise ValueError("Held-out inference requires the frozen policy")
            policy = freeze["policy"]
        else:
            policy = policies[args.policy or "conservative"]

    def factory():
        config = read_config(Path("configs/whisper.toml"))
        config["model"]["name"] = "medium.en" if args.condition == "medium" else "small.en"
        model_dir, = Path("models", config["model"]["name"]).glob("*/")
        if args.condition == "V3":
            snapshot, = Path("models/minilm").glob("*/")
            encoder = MiniLM(snapshot)
            retriever = Retrieval(lexicon, encoder)
            fallback = config["prompt"]["vocabulary"]["hotwords"]
            config["prompt"].update(mode="dynamic", hotword_budget=96, top_k=32,
                                    retrieval="hybrid", embedding=encoder.provenance, lexicon_sha256=digest(lexicon))

            def select(metadata):
                return retriever.rank(metadata["source_record"], "hybrid", top_k=32, fallback=fallback)

            return MeasuredBaseline(WhisperInference(config, model_dir=model_dir, prompt_selector=select))
        config["prompt"] = {"mode": "none"}
        config["decoding"].update(initial_prompt=None, hotwords=None)
        engine = WhisperInference(config, model_dir=model_dir)
        return (AdaptiveInference(engine, lexicon, policy, args.condition == "V4-B")
                if policy else MeasuredBaseline(engine))

    output = args.data / "runs" / args.subset / (args.label or args.condition)
    events = []
    for event in run_batch(entries, factory, output):
        print(json.dumps(event), flush=True)
        events.append(event)
    atomic_json(output / "run-0-of-1.json", events)
    return int(any(event["status"] == "failed" for event in events))


if __name__ == "__main__":
    raise SystemExit(main())
