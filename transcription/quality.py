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
