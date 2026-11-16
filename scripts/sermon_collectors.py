"""Collect sermon metadata using the Stage 1 church registry."""

import argparse
import json
import sys
from pathlib import Path

from collectors.dispatch import collect_church
from collectors.registry import CHURCH_BY_KEY
from collectors.schema import write_results


def main():
    active_churches = {
        key: church
        for key, church in CHURCH_BY_KEY.items() if church.status == "active"
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("church", choices=["all", *active_churches], nargs="?", default="all")
    parser.add_argument("--output", type=Path, default=Path("outputs/sermon_collection"))
    args = parser.parse_args()
    failures = []
    for name, church in active_churches.items():
        if args.church in {"all", name}:
            try:
                result = collect_church(church, output=args.output)
            except Exception as error:
                if args.church != "all":
                    raise
                failures.append(name)
                print(f"{name}: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
                continue
            print(name, json.dumps(write_results(args.output, name, result), indent=2), flush=True)
    if failures:
        raise SystemExit("Failed collectors: " + ", ".join(failures))


if __name__ == "__main__":
    main()
