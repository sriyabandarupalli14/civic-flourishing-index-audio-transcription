"""Rebuild matched, reference-relative summaries from verified checkpoints."""

import json
import random
import statistics
from collections import defaultdict

from transcription.checkpoints import digest, read_checkpoint

from .metrics import counts, rates


def load_predictions(directory, expected):
    rows = {}
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("run-"):
            continue
        envelope = json.loads(path.read_text(encoding="utf-8"))
        identity = envelope["identity"]
        record_id = identity["record_id"]
        if record_id not in expected:
            continue
        result = read_checkpoint(path, identity)
        if (result is None or identity["audio_sha256"] != expected[record_id]
                or path.stem != digest(identity) or record_id in rows):
            raise ValueError("Invalid, mismatched or duplicated prediction checkpoint")
        rows[record_id] = result
    return rows


def compare(expected, predictions, references, lexicon):
    rows = []
    for clip_id, info in expected.items():
        prediction, reference = predictions.get(clip_id), references.get(clip_id)
        if prediction is None or reference is None:
            continue
        runtime = prediction["inference"]
        seconds = runtime["wall_seconds"]
        measures = counts(reference["text"], prediction["text"], lexicon)
        rows.append({"clip_id": clip_id, "subset": info["subset"],
                     "church_number": info["source_record"]["church_number"],
                     **measures, "inference_seconds": seconds,
                     "real_time_factor": seconds / info["duration_seconds"],
                     "peak_rss_bytes": runtime["rss_peak_sampled_bytes"],
                     "model_load_seconds": prediction["model_initialization"]["wall_seconds"],
                     "audio_sha256": info["clip_sha256"],
                     "hypothesis_config_sha256": prediction["configuration_sha256"],
                     "reference_config_sha256": reference["configuration_sha256"]})
    return rows


def pool(rows):
    keys = ("reference_words", "reference_chars", "substitutions", "deletions", "insertions",
            "word_errors", "character_errors", "reference_terms", "predicted_terms", "matched_terms",
            "false_term_insertions")
    totals = {key: sum(row[key] for row in rows) for key in keys}
    medians = {f"median_{key}": statistics.median(row[key] for row in rows) if rows else None
               for key in ("inference_seconds", "real_time_factor", "peak_rss_bytes", "model_load_seconds")}
    return {"clips": len(rows), "churches": len({row["church_number"] for row in rows}),
            **totals, **rates(totals), **medians}


def paired_intervals(first, second, seed=20261116, n=1000):
    by_clip = {row["clip_id"]: row for row in second}
    clusters = defaultdict(list)
    for row in first:
        if row["clip_id"] in by_clip:
            clusters[row["church_number"]].append((row, by_clip[row["clip_id"]]))
    if len(clusters) < 2:
        return None
    churches = sorted(clusters)
    rng = random.Random(seed)
    results = defaultdict(list)
    for _ in range(n):
        pairs = [pair for church in rng.choices(churches, k=len(churches)) for pair in clusters[church]]
        a, b = [pool([pair[index] for pair in pairs]) for index in (0, 1)]
        for metric in ("wer", "term_f1"):
            if a[metric] is not None and b[metric] is not None:
                results[metric].append(a[metric] - b[metric])
    return {metric: {"lower": sorted(values)[int(0.025 * (len(values) - 1))],
                     "upper": sorted(values)[int(0.975 * (len(values) - 1))],
                     "replicates": len(values)} for metric, values in results.items()}
