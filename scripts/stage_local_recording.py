"""Checksum-verify and normalize one full local recording for Stage 3 transcription."""

import argparse
import json
from pathlib import Path

from media_ingestion.pipeline import hash_file, load_manifest
from transcription.production import stage_verified_local


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("outputs/sermon_collection/stage2/recording_manifest.csv"))
    parser.add_argument("--recording", required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--metadata", type=Path, required=True, help="Local provenance JSON containing audio_url")
    parser.add_argument("--output", type=Path, default=Path("outputs/transcription/local_stage3/audio"))
    args = parser.parse_args(argv)
    recording = load_manifest(args.manifest)[args.recording]
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    stage_verified_local(recording, args.audio, metadata["audio_url"], args.sha256, args.output,
                         {"metadata_file": str(args.metadata), "metadata_sha256": hash_file(args.metadata),
                          "metadata": metadata})
    print("Verified full local media staged")


if __name__ == "__main__":
    main()
