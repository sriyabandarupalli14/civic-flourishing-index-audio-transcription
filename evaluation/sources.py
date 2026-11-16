"""Collect bounded audio prefixes for an explicit seeded evaluation plan."""

import json
from concurrent.futures import ThreadPoolExecutor

import requests

from media_ingestion.pipeline import Limits, MediaPipeline, hash_file, load_manifest, task_key
from media_ingestion.transport import MediaError

from .dataset import verify_plan


def acquire_prefixes(plan, manifest, output, existing):
    verify_plan(plan)
    if hash_file(manifest) != plan["stage2_manifest_sha256"]:
        raise ValueError("Stage 2 manifest differs from the one used for sampling")
    recordings = load_manifest(manifest)
    clips = plan["clips"]
    if len(clips) > 50 or any(clip["start_seconds"] + clip["duration_seconds"] > 90 for clip in clips):
        raise ValueError("Bounded acquisition covers at most 50 selected recordings and 90 seconds each")
    by_id = {item["recording_id"]: item for item in existing}
    jobs = [clip for clip in clips if clip["recording_id"] not in by_id]

    def acquire(clip):
        item = recordings[clip["recording_id"]]
        extent = clip["start_seconds"] + clip["duration_seconds"]
        limits = Limits(max_bytes=4 * 1024**2, max_seconds=extent, sample_seconds=extent,
                        attempts=1, max_references=1)
        try:
            with requests.Session() as session:
                session.max_redirects = 5
                result = MediaPipeline(session, output, refresh=lambda *_: None).run(
                    item, mode="ingest", kind="audio", limits=limits,
                )
        except (OSError, MediaError) as error:
            code = error.code if isinstance(error, MediaError) else "local_io_error"
            result = {"status": "failed", "reason": code}
        source = None
        if result["status"] in {"decoded_audio", "reused"}:
            key = task_key(item, "audio", limits)
            checkpoint = output / f"{item.identifier}-{key}.checkpoint.json"
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            source = {"recording_id": item.identifier, "audio_file": str(output / f"{item.identifier}-{key}.wav"),
                      "sha256": state["sha256"], "reference_sha256": state["source"]["reference_sha256"],
                      "kind": "stage3_bounded_preview", "checkpoint": state}
        event = {"recording_id": item.identifier, "status": result["status"],
                 "reason": result.get("reason", ""), "duration_seconds": result.get("duration_seconds")}
        return source, event

    sources, events = list(existing), []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for source, event in pool.map(acquire, jobs):
            if source:
                sources.append(source)
            events.append(event)
    return sources, events
