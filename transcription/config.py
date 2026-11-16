"""Read and resolve versioned Whisper run settings."""

import json
import tomllib


def read_config(path):
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    model = config["model"]
    if model["name"] not in {"base.en", "small.en", "medium.en", "large-v3"}:
        raise ValueError("Unknown Whisper model")
    hardware = config["hardware"]
    if hardware["device"] not in {"cpu", "cuda"}:
        raise ValueError("Choose explicit CPU or CUDA hardware")
    decoding = dict(config["decoding"])
    if {"hotwords", "initial_prompt"} & decoding.keys():
        raise ValueError("Set vocabulary and initial context in the prompt configuration")

    prompt = dict(config.get("prompt", {"mode": "none"}))
    if prompt["mode"] == "none":
        decoding.update(initial_prompt=None, hotwords=None)
    elif prompt["mode"] == "static":
        vocabulary_path = path.parent / prompt.pop("vocabulary")
        vocabulary = json.loads(vocabulary_path.read_text(encoding="utf-8"))
        context, words = vocabulary["initial_prompt"], vocabulary["hotwords"]
        if (not isinstance(context, str) or not isinstance(words, list)
                or any(not isinstance(word, str) or not word.strip() for word in words)):
            raise ValueError("Expected an initial prompt and a list of nonempty vocabulary terms")
        decoding.update(initial_prompt=context if prompt.get("use_context", True) else None,
                        hotwords=", ".join(words) if prompt.get("use_hotwords", True) else None)
        prompt["vocabulary"] = vocabulary
    else:
        raise ValueError("Choose none or static prompting")
    return dict(model=model, hardware=hardware, decoding=decoding, prompt=prompt)
