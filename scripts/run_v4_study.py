"""Freeze V4 from development evidence, then run held-out comparisons sequentially."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from evaluation.v4_analysis import freeze_inputs, policy_scores
from media_ingestion.pipeline import atomic_json, hash_file
from vocabulary.retrieval import read_lexicon


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    args = parser.parse_args()
    data = args.data
    manifest_path = data / "clip_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    policies = json.loads(Path("configs/v4_policies.json").read_text())
    path = data / "frozen_policy.json"
    if not path.exists():
        name, scores, exposure = policy_scores(data, policies, read_lexicon(Path("configs/religious_lexicon.tsv")))
        atomic_json(path, {"plan_sha256": manifest["plan_sha256"], "manifest_sha256": hash_file(manifest_path),
                           "name": name, "policy": policies[name],
                           "selection": "among policies with any prompted development retry if available, highest mean "
                                        "term F1, then lowest mean WER, then smallest token budget",
                           "development_prompted_retries": exposure, "protocol_files_sha256": freeze_inputs(),
                           "development_scores": scores})
        print("Frozen policy:", name, flush=True)
    if json.loads(path.read_text()).get("manifest_sha256") != hash_file(manifest_path):
        raise ValueError("The materialized V4 manifest differs from the saved freeze")
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "OMP_NUM_THREADS": "4"}
    for condition in ("V0", "V3", "V4-A", "V4-B"):
        log = data / "runs" / f"held-{condition}.log"
        with log.open("a", encoding="utf-8") as handle:
            subprocess.run([sys.executable, "-m", "scripts.run_v4_condition", condition, "--data", str(data),
                            "--subset", "held_out"], env=env, check=True, stdout=handle, stderr=handle)
        print(condition, "finished", flush=True)
    subprocess.run([sys.executable, "-m", "scripts.report_v4", "--data", str(data)], env=env, check=True)


if __name__ == "__main__":
    main()
