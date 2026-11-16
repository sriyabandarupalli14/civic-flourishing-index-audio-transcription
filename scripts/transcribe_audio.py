"""Transcribe one local audio file with a configured Whisper model."""

import argparse
import json
from pathlib import Path

from media_ingestion.pipeline import atomic_json
from transcription.config import read_config
from transcription.inference import WhisperInference


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/whisper.toml"))
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--clip-manifest", type=Path, help="Optional materialized clip inventory")
    parser.add_argument("--clip-id")
    parser.add_argument("--output", type=Path, default=Path("outputs/transcription/result.json"))
    args = parser.parse_args(argv)
    if bool(args.clip_manifest) != bool(args.clip_id):
        parser.error("supply both --clip-manifest and --clip-id")
    metadata = {}
    if args.clip_manifest:
        manifest = json.loads(args.clip_manifest.read_text(encoding="utf-8"))
        matches = [row for row in manifest["clips"] if row["clip_id"] == args.clip_id]
        if len(matches) != 1 or matches[0]["status"] != "ready":
            parser.error("clip ID must match one ready clip")
        metadata = matches[0]
    if not args.audio.is_file() or args.output.exists() or args.output.resolve() == args.audio.resolve():
        parser.error("audio must exist and the result path must be new")
    model = WhisperInference(read_config(args.config), model_dir=args.model_dir)
    result = model.transcribe(args.audio, metadata=metadata, expected_sha256=metadata.get("clip_sha256"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, result)
    print(json.dumps({"segments": len(result["segments"]), "inference": result["inference"],
                      "audio_sha256": result["audio_sha256"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
