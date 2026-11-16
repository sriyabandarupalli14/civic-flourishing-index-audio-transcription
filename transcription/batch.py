"""Reuse model instances and checkpoint each verified recording separately."""

from pathlib import Path

from media_ingestion.pipeline import hash_file, lock
from media_ingestion.transport import MediaError

from .checkpoints import digest, read_checkpoint, save_checkpoint


def run_batch(entries, model_factory, output, *, shard_count=1, shard_index=0):
    if not 0 <= shard_index < shard_count:
        raise ValueError("Invalid shard index or count")
    output.mkdir(parents=True, exist_ok=True)
    model = None
    initialization_error = None
    for row in entries:
        record_id = row["clip_id"] if "clip_id" in row else row["recording_id"]
        if int(digest(record_id), 16) % shard_count != shard_index:
            continue
        try:
            path = Path(row["audio_file"])
            sha256 = row["audio_sha256"]
            if hash_file(path) != sha256:
                raise ValueError("audio_checksum_mismatch")
            if model is None and initialization_error is None:
                try:
                    model = model_factory()
                except (OSError, ValueError, RuntimeError) as error:
                    initialization_error = type(error).__name__
            if initialization_error:
                yield {"record_id": record_id, "status": "failed", "error_type": initialization_error,
                       "reason": "model_initialization_failed"}
                continue
            identity = {"record_id": record_id, "audio_sha256": sha256,
                        "configuration_sha256": model.configuration_sha256,
                        "metadata_sha256": digest({key: value for key, value in row.items() if key != "audio_file"})}
            checkpoint = output / f"{digest(identity)}.json"
            with lock(checkpoint.with_suffix(".lock")):
                result = read_checkpoint(checkpoint, identity)
                status = "reused" if result is not None else "completed"
                if result is None:
                    result = model.transcribe(path, metadata=row, expected_sha256=sha256)
                    save_checkpoint(checkpoint, identity, result)
            yield {"record_id": record_id, "status": status, "checkpoint": checkpoint.name}
        except (OSError, ValueError, RuntimeError, MediaError) as error:
            yield {"record_id": record_id, "status": "failed", "error_type": type(error).__name__,
                   "reason": str(error) if str(error) == "audio_checksum_mismatch" else "inference_or_io_error"}
