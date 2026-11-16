"""Combine selected local Stage 1 outputs without changing or deduplicating their rows."""

import argparse
import json
from pathlib import Path

from collectors.outputs import consolidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/sermon_collection"))
    args = parser.parse_args()
    print(json.dumps(consolidate(args.output), indent=2))


if __name__ == "__main__":
    main()
