"""Shared transcript quality checks; flags require listening review."""

import re


def repetition_flags(text: str, minimum_repeats: int = 4) -> list[dict]:
    words = re.findall(r"\b[\w']+\b", text.casefold())
    findings = {}
    for width in range(4, 9):
        position = 0
        while position + width * minimum_repeats <= len(words):
            phrase = words[position:position + width]
            count = 1
            while words[position + count * width:position + (count + 1) * width] == phrase:
                count += 1
            if count >= minimum_repeats:
                label = " ".join(phrase)
                findings[label] = max(findings.get(label, 0), count)
                position += count * width
            else:
                position += 1
    return [dict(phrase=key, consecutive_repeats=value) for key, value in findings.items()]


def assess_quality(text, segments, duration):
    """Surface suspicious spans without editing the transcript."""
    repetitions = repetition_flags(text)
    spans = []
    preceding = 0
    for row in segments:
        start, end = row["start_seconds"], row["end_seconds"]
        reasons = []
        if start < 0 or end <= start or end > duration + 0.25 or start < preceding - 0.25:
            reasons.append("invalid_timestamps")
        if row["avg_logprob"] < -1.0:
            reasons.append("low_average_log_probability")
        if row["no_speech_probability"] > 0.6 and row["avg_logprob"] < -1.0 and row["text"]:
            reasons.append("text_in_possible_silence")
        if row["compression_ratio"] > 2.4:
            reasons.append("high_compression_possible_repetition")
        if reasons:
            spans.append({"segment_id": row["id"], "start_seconds": start, "end_seconds": end, "reasons": reasons})
        preceding = max(preceding, start)
    return {"empty_transcript": not text.strip(), "repetition_flags": repetitions,
            "segment_flags": spans, "needs_review": not text.strip() or bool(repetitions or spans)}
