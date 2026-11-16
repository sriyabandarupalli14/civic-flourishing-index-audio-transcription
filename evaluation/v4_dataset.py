"""Seeded terminology-enriched sample from eligible recordings, independent of ASR."""

import json
import re
from collections import defaultdict

from evaluation.dataset import duplicate_keys, fingerprint
from evaluation.metrics import index_terms, occurrences, tokens
from media_ingestion.pipeline import hash_file, load_manifest
from media_ingestion.transport import kind_for

PASSAGE = re.compile(r"\b(?:[123]\s+)?[a-z][a-z]+\s+\d{1,3}\s*:\s*\d{1,3}\b", re.I)
FIELD_NAMES = ("title", "description", "scripture_reference")


def topic_score(source, term_index):
    text = " ".join(source.get(key) or "" for key in FIELD_NAMES)
    evidence = {"explicit_passage": bool(source.get("scripture_reference") or PASSAGE.search(text)),
                "title_terms": sorted({" ".join(term) for _, term in occurrences(tokens(text), term_index)})}
    return int(evidence["explicit_passage"]) * 4 + min(len(evidence["title_terms"]), 3), evidence


def plan_v4(manifest, previous, lexicon, *, seed=20261117, failed_ids=(), failed_churches=()):
    recordings = load_manifest(manifest)
    old = json.loads(previous.read_text(encoding="utf-8"))["clips"]
    excluded = {row["recording_id"] for row in old}
    blocked = set().union(*(duplicate_keys(recordings[key]) for key in excluded if key in recordings))
    terms = index_terms(row["term"] for row in lexicon)
    pools = defaultdict(list)
    for item in recordings.values():
        if item.identifier in excluded or item.identifier in failed_ids or item.church_id in failed_churches:
            continue
        if item.held_reason() or not any(kind_for(url) == "audio" for url in item.references):
            continue
        if duplicate_keys(item) & blocked:
            continue
        score, evidence = topic_score(item.source_record, terms)
        if score:
            pools[item.church_id].append((score, fingerprint([seed, item.identifier]), item, evidence))
    # Favor church pools with passage-like metadata before the seeded tie break.
    eligible = {key: sorted(pool, key=lambda x: (-x[0], x[1])) for key, pool in pools.items()
                if len(pool) >= 5}
    order = sorted(eligible, key=lambda c: (-sum(row[0] for row in eligible[c][:5]), fingerprint([seed, c])))
    clips = []
    for church in order:
        chosen, within = [], set()
        for score, _, item, evidence in eligible[church]:
            keys = duplicate_keys(item)
            if keys & (blocked | within):
                continue
            chosen.append((item, evidence))
            within.update(keys)
            if len(chosen) == 5:
                break
        if len(chosen) != 5:
            continue
        blocked.update(within)
        development = min(chosen, key=lambda row: fingerprint([seed, "development", row[0].identifier]))[0]
        for item, evidence in chosen:
            clips.append({"clip_id": f"{item.identifier}-30s-60s", "recording_id": item.identifier,
                          "church_id": church, "subset": "development" if item == development else "held_out",
                          "start_seconds": 30, "duration_seconds": 60,
                          "source_record": item.source_record, "selection_evidence": evidence})
        if len(clips) == 50:
            break
    plan = {"schema_version": 1, "stage2_manifest_sha256": hash_file(manifest),
            "previous_clip_manifest_sha256": hash_file(previous), "seed": seed, "target_clips": 50,
            "failed_recordings": sorted(failed_ids), "failed_churches": sorted(failed_churches), "clips": clips}
    return {**plan, "plan_sha256": fingerprint(plan)}
