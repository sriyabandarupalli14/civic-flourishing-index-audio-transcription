"""Replay November metadata caches and combine Stage 1 observations offline."""

import argparse
import json
from pathlib import Path

from collectors.replay import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="Original research directory (read-only)")
    parser.add_argument("--first20", type=Path, default=Path("outputs/sermon_collection/combined_stage1_first20.csv"))
    parser.add_argument("--output", type=Path, default=Path("outputs/sermon_collection/stage1b"))
    args = parser.parse_args()
    print(json.dumps(collect(args.reference, args.first20, args.output), indent=2))


if __name__ == "__main__":
    main()
