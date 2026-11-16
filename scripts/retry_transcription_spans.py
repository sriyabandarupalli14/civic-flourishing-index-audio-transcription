"""Generate separate, bounded review candidates from flagged full-recording spans."""

import argparse
import json
from pathlib import Path

from media_ingestion.pipeline import atomic_json
from transcription.checkpoints import read_checkpoint
from transcription.config import read_config
from transcription.inference import WhisperInference
from transcription.retry_spans import alternate_decodes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--config", type=Path, default=Path("configs/whisper.toml"))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    identity = json.loads(args.checkpoint.read_text(encoding="utf-8"))["identity"]
    result = read_checkpoint(args.checkpoint, identity)
    if result is None or args.output.exists() or not 1 <= args.limit <= 3:
        parser.error("Provide a valid checkpoint, a new output path and a limit of one to three")
    config = read_config(args.config)
    config["decoding"].update(condition_on_previous_text=False, vad_filter=True)
    model = WhisperInference(config, model_dir=args.model_dir)
    rows = alternate_decodes(args.audio, result, model, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, {"parent_checkpoint": args.checkpoint.name, "identity": identity,
                              "alternatives": rows})
    print(f"Saved {len(rows)} alternate review candidates")


if __name__ == "__main__":
    main()
