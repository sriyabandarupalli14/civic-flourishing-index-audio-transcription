"""Clean and reconcile Stage 1 observations without network access."""

import argparse
import json
from pathlib import Path

from processing.pipeline import DEFAULT_INPUT, DEFAULT_OUTPUT, reconcile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    config = parser.parse_args()
    print(json.dumps(reconcile(config.input, config.output), indent=2))


if __name__ == "__main__":
    main()
