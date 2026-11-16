"""Content-bound, atomic transcript checkpoints shared by batch entry points."""

import json

from media_ingestion.pipeline import atomic_json, hash_text


def digest(value):
    return hash_text(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def read_checkpoint(path, identity):
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        result = envelope["result"]
        if (envelope["identity"] == identity and envelope["result_sha256"] == digest(result)
                and result["audio_sha256"] == identity["audio_sha256"]):
            return result
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def save_checkpoint(path, identity, result):
    if result["audio_sha256"] != identity["audio_sha256"]:
        raise ValueError("Cannot save a result for different audio bytes")
    atomic_json(path, {"identity": identity, "result_sha256": digest(result), "result": result})
