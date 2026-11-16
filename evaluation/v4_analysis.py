"""Load independent V4 artifacts and summarize reference-relative comparisons."""

import json
import statistics
from collections import Counter
from pathlib import Path

from evaluation.metrics import index_terms, occurrences, tokens
from evaluation.results import load_predictions, compare, pool
from media_ingestion.pipeline import hash_file
from transcription.adaptive import has_loop, relevant_terms, triggers


def freeze_inputs():
    paths = ("configs/whisper.toml", "configs/v4_policies.json", "configs/religious_lexicon.tsv",
             "configs/sermon_vocabulary.json", "transcription/adaptive.py", "scripts/run_v4_condition.py")
    return {path: hash_file(Path(path)) for path in paths}


def clips_for(data, subset):
    manifest = json.loads((data / "clip_manifest.json").read_text())
    return {row["clip_id"]: row for row in manifest["clips"]
            if row["status"] == "ready" and row["subset"] == subset}


def checkpoint_set(directory, clips):
    return load_predictions(directory, {key: row["clip_sha256"] for key, row in clips.items()})


def policy_scores(data, policies, lexicon):
    clips = clips_for(data, "development")
    index = index_terms(row["term"] for row in lexicon)
    refs = {name: checkpoint_set(data / "runs" / name, clips) for name in ("large-v3", "wav2vec2")}
    if any(set(rows) != set(clips) for rows in refs.values()):
        raise ValueError("Development pseudo-references are incomplete")
    scores, exposure = {}, {}
    for name, policy in policies.items():
        result = {}
        for mode in ("V4-A", "V4-B"):
            rows = checkpoint_set(data / "runs/development" / f"{mode}-{name}", clips)
            if set(rows) != set(clips):
                raise ValueError(f"Development predictions incomplete for {mode} {name}")
            if mode == "V4-B":
                exposure[name] = diagnostics(rows)["prompted_retries"]
            result[mode] = {ref: pool(compare(clips, rows, text, index)) for ref, text in refs.items()}
        scores[name] = result

    def rank(name):
        values = list(scores[name]["V4-B"].values())
        f1 = statistics.mean(row["term_f1"] or 0 for row in values)
        wer = statistics.mean(row["wer"] if row["wer"] is not None else 1 for row in values)
        return -f1, wer, policies[name]["hotword_budget"], name

    exposed = [name for name in policies if exposure[name]]
    return min(exposed or policies.keys(), key=rank), scores, exposure


def diagnostics(predictions):
    total_segments, retries, accepted, prompted = 0, 0, 0, 0
    outcomes = Counter()
    for result in predictions.values():
        adaptation = result.get("adaptation", {})
        total_segments += adaptation.get("baseline_segments", len(result["segments"]))
        for event in adaptation.get("events", []):
            retries += 1
            accepted += event["outcome"] == "accepted"
            prompted += bool(event["prompt"]["selected_terms"])
            outcomes[event["outcome"]] += 1
    phase_times = [result["adaptation_phase_inference"]["wall_seconds"] for result in predictions.values()
                   if result.get("adaptation_phase_inference")]
    memory = [result["inference"]["rss_peak_sampled_bytes"] for result in predictions.values()]
    seconds = [result["inference"]["wall_seconds"] for result in predictions.values()]
    return {"clips": len(predictions), "repeat_clips": sum(has_loop(tokens(row["text"])) for row in predictions.values()),
            "segments": total_segments, "retries": retries, "accepted": accepted, "prompted_retries": prompted,
            "retry_rate": retries / total_segments if total_segments else None,
            "acceptance_rate": accepted / retries if retries else None, "outcomes": dict(outcomes),
            "median_seconds": statistics.median(seconds) if seconds else None,
            "median_adaptation_seconds": statistics.median(phase_times) if phase_times else None,
            "median_peak_rss_mib": statistics.median(memory) / 2**20 if memory else None}


def coverage(results, index):
    term_lists = [occurrences(tokens(row["text"]), index) for row in results.values()]
    return {"occurrences": sum(map(len, term_lists)), "clips_with_terms": sum(bool(item) for item in term_lists),
            "distinct_terms": len({term for terms in term_lists for _, term in terms})}


def gate_funnel(predictions, clips, lexicon, policy):
    """Explain abstentions before the per-clip retry cap, using saved V0 rows."""
    counts = Counter()
    for key, result in predictions.items():
        for row in result["segments"]:
            counts["segments"] += 1
            reasons = triggers(row, policy)
            if not reasons:
                continue
            counts["diagnostic_trigger"] += 1
            duration = min(result["audio_duration_seconds"], row["end_seconds"]) - max(0, row["start_seconds"])
            if not 1 <= duration <= policy["max_span_seconds"]:
                counts["outside_duration_bounds"] += 1
                continue
            terms = relevant_terms(lexicon, clips[key]["source_record"], row["text"],
                                   policy["relevance"], policy["term_limit"])
            if terms:
                counts["plausible_hint"] += 1
            elif "repetition" in reasons:
                counts["repetition_only"] += 1
            else:
                counts["insufficient_term_evidence"] += 1
    return dict(counts)
