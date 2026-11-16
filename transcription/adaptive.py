"""V4 selective ASR retries, with a matched no-hints control."""

import math
import re
import wave
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluation.metrics import index_terms, occurrences, tokens
from media_ingestion.pipeline import hash_file
from vocabulary.prompts import prepare_prompt

from .checkpoints import digest
from .inference import measure_resources
from .quality import assess_quality


def has_loop(words):
    for size in range(1, 5):
        for start in range(len(words) - 4 * size + 1):
            if words[start:start + size] * 4 == words[start:start + 4 * size]:
                return True
    return False


def phonetic(word):
    normalized = word.replace("ph", "f").replace("ck", "k").replace("qu", "k").replace("th", "t")
    if not normalized:
        return ""
    return normalized[0] + re.sub(r"(.)\1+", r"\1", re.sub("[aeiouy]", "", normalized[1:]))


def similarity(left, right):
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def relevant_terms(lexicon, source, text, cutoff, limit):
    spans = tokens(text)
    metadata = " ".join(source.get(key) or "" for key in ("title", "description", "scripture_reference"))
    topic = " " + " ".join(tokens(metadata)) + " "
    matches = []
    for entry in lexicon:
        term = entry["term"]
        words = tokens(term)
        if not words or sum(map(len, words)) < 5:
            continue
        phrase = " ".join(words)
        if f" {phrase} " not in topic:
            continue
        for start in range(len(spans) - len(words) + 1):
            window = spans[start:start + len(words)]
            if window == words:
                continue
            surface = similarity(phrase, " ".join(window))
            phonemes = [phonetic(word) for word in words]
            heard = [phonetic(word) for word in window]
            sound = similarity(" ".join(phonemes), " ".join(heard))
            evidence = max(surface, sound * .95)
            if evidence >= cutoff and surface >= .4:
                matches.append((evidence, term))
                break
    return [term for _, term in sorted(matches, key=lambda row: (-row[0], row[1].casefold()))][:limit]


def triggers(row, policy):
    notes = []
    if row["avg_logprob"] < policy["logprob"]:
        notes.append("low_logprob")
    if row["compression_ratio"] > policy["compression"] or has_loop(tokens(row["text"])):
        notes.append("repetition")
    if row["no_speech_probability"] > policy["silence"]:
        notes.append("possible_silence")
    return notes


def acceptance(original, candidate, plausibles, term_index, policy):
    before, after = tokens(original["text"]), tokens(candidate["text"])
    if not after or after == before:
        return "empty_or_unchanged"
    if has_loop(after) or candidate["compression_ratio"] > policy["compression"]:
        return "repetitive_retry"
    if candidate["no_speech_probability"] > policy["silence"]:
        return "possible_silence"
    before_terms = Counter(term for _, term in occurrences(before, term_index))
    after_terms = Counter(term for _, term in occurrences(after, term_index))
    supported = {tuple(tokens(term)) for term in plausibles}
    if any(count > max(before_terms[term], int(term in supported)) for term, count in after_terms.items()):
        return "unsupported_terminology"
    # Protect untargeted wording. Match boundaries and permit short edits, with
    # a narrow exception for deleting an obvious repeated surplus.
    changes = SequenceMatcher(None, before, after, autojunk=False)
    loop = has_loop(before) and len(after) < len(before) and all(
        action in {"equal", "delete"} for action, *_ in changes.get_opcodes()
    )
    if not loop:
        if changes.ratio() < policy["text_similarity"]:
            return "excessive_text_changes"
        if len(before) >= 5 and before[:2] != after[:2] and before[-2:] != after[-2:]:
            return "changed_both_boundaries"
    return "accepted"


class AdaptiveInference:
    def __init__(self, engine, lexicon, policy, prompted, baseline_cache=None):
        self.engine = engine
        self.lexicon = lexicon
        self.term_index = index_terms(row["term"] for row in lexicon)
        self.policy = policy
        self.prompted = prompted
        self.baseline_cache = baseline_cache
        if not 1 <= policy["term_limit"] <= 5 or not 1 <= policy["hotword_budget"] <= 16:
            raise ValueError("V4 budgets exceed the configured limits")
        version = "v4.1" if baseline_cache is None else "v4.1-measured-shared-first-pass"
        self.configuration_sha256 = digest([engine.configuration_sha256, lexicon, policy, prompted, version])

    def transcribe(self, audio, *, metadata=None, expected_sha256=None):
        with measure_resources() as end_to_end:
            if self.baseline_cache is None:
                baseline = self.engine.transcribe(audio, metadata=metadata, expected_sha256=expected_sha256)
            else:
                baseline = self.baseline_cache[(metadata or {})["clip_id"]]
                if (baseline["audio_sha256"] != expected_sha256
                        or baseline["configuration_sha256"] != self.engine.configuration_sha256):
                    raise ValueError("Cached first pass differs from the expected audio or decode configuration")
            rows, events = self.revise(audio, baseline, (metadata or {}).get("source_record", {}))
            text = " ".join(row["text"] for row in rows).strip()
            quality = assess_quality(text, rows, baseline["audio_duration_seconds"])
        measured = end_to_end
        if self.baseline_cache is not None:
            first = baseline["inference"]
            measured = {**end_to_end, "wall_seconds": first["wall_seconds"] + end_to_end["wall_seconds"],
                        "rss_peak_sampled_bytes": max(first["rss_peak_sampled_bytes"],
                                                      end_to_end["rss_peak_sampled_bytes"]),
                        "measurement": "measured_first_pass_plus_measured_adaptation"}
        return {**baseline, "text": text, "segments": rows, "quality": quality,
                "configuration_sha256": self.configuration_sha256,
                "first_pass_inference": baseline["inference"], "inference": measured,
                "adaptation_phase_inference": end_to_end if self.baseline_cache is not None else None,
                "adaptation": {"prompted": self.prompted, "policy": self.policy, "events": events,
                               "baseline_segments": len(baseline["segments"])}}

    def revise(self, audio, baseline, source):
        rows, events = list(baseline["segments"]), []
        with wave.open(str(audio), "rb") as pcm, TemporaryDirectory() as folder:
            if (pcm.getframerate(), pcm.getnchannels(), pcm.getsampwidth()) != (16000, 1, 2):
                raise ValueError("V4 expects mono 16 kHz PCM16 WAV")
            for index, row in enumerate(rows):
                notes = triggers(row, self.policy)
                start = max(0, math.floor(row["start_seconds"] * 16000))
                end = min(pcm.getnframes(), math.ceil(row["end_seconds"] * 16000))
                if not notes or not 1 * 16000 <= end - start <= self.policy["max_span_seconds"] * 16000:
                    continue
                plausible = relevant_terms(self.lexicon, source, row["text"], self.policy["relevance"],
                                           self.policy["term_limit"])
                if not plausible and "repetition" not in notes:
                    continue
                path = Path(folder) / "span.wav"
                pcm.setpos(start)
                with wave.open(str(path), "wb") as out:
                    out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    out.writeframes(pcm.readframes(end - start))
                decoding = dict(self.engine.config["decoding"])
                terms = plausible if self.prompted else []
                decoding["hotwords"], usage = prepare_prompt(self.engine.model, decoding, terms,
                                                              self.policy["hotword_budget"])
                candidate_segments, _ = self.engine.model.transcribe(str(path), **decoding)
                parts = list(candidate_segments)
                candidate = {"text": " ".join(seg.text.strip() for seg in parts),
                             "compression_ratio": max((seg.compression_ratio for seg in parts), default=0),
                             "no_speech_probability": max((seg.no_speech_prob for seg in parts), default=1)}
                decision = acceptance(row, candidate, plausible, self.term_index, self.policy)
                events.append({"segment": row["id"], "reasons": notes, "plausible_terms": plausible,
                               "prompt": usage, "outcome": decision, "original": row["text"],
                               "retry": candidate, "span_sha256": hash_file(path),
                               "start_seconds": start / 16000, "end_seconds": end / 16000})
                if decision == "accepted":
                    # Retain the original span and its provenance. Fresh per-word
                    # timestamps are unavailable for this aligned replacement.
                    rows[index] = {**row, **candidate, "words": [], "v4_replaced": True}
                if len(events) >= self.policy["max_retries"]:
                    break
        return rows, events
