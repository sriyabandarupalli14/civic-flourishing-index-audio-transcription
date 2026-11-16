"""Reference-relative text alignment with occurrence-level terminology scores."""

import re
import unicodedata
from collections import Counter


def tokens(text):
    """Match the original evaluation's English tokenization and normalization."""
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*", normalized)


def align(reference, hypothesis):
    n, m = len(reference), len(hypothesis)
    distance = [list(range(m + 1))] + [[i] + [0] * m for i in range(1, n + 1)]
    for i, ref in enumerate(reference, 1):
        for j, pred in enumerate(hypothesis, 1):
            distance[i][j] = min(distance[i - 1][j - 1] + (ref != pred),
                                 distance[i - 1][j] + 1, distance[i][j - 1] + 1)
    i, j = n, m
    changes, matches = Counter(), {}
    while i or j:
        if i and j and distance[i][j] == distance[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            if reference[i - 1] == hypothesis[j - 1]:
                matches[i - 1] = j - 1
            else:
                changes["substitutions"] += 1
            i -= 1
            j -= 1
        elif i and distance[i][j] == distance[i - 1][j] + 1:
            changes["deletions"] += 1
            i -= 1
        else:
            changes["insertions"] += 1
            j -= 1
    return changes, matches


def character_distance(first, second):
    previous = list(range(len(second) + 1))
    for i, char in enumerate(first, 1):
        current = [i]
        for j, other in enumerate(second, 1):
            current.append(min(previous[j - 1] + (char != other), previous[j] + 1, current[-1] + 1))
        previous = current
    return previous[-1]


def occurrences(words, lexicon):
    # Longest match at the same position avoids nested counts (e.g. Christ in Jesus Christ).
    found = []
    position = 0
    while position < len(words):
        term = next((term for term in lexicon.get(words[position], ())
                     if words[position:position + len(term)] == term), None)
        if term:
            found.append((position, tuple(term)))
            position += len(term)
        else:
            position += 1
    return found


def index_terms(terms):
    index = {}
    for term in terms:
        words = tokens(term)
        if words:
            index.setdefault(words[0], []).append(words)
    for candidates in index.values():
        candidates.sort(key=lambda term: (-len(term), term))
    return index


def counts(reference, prediction, lexicon):
    ref, hyp = tokens(reference), tokens(prediction)
    changes, matches = align(ref, hyp)
    reference_terms = occurrences(ref, lexicon)
    predicted_terms = set(occurrences(hyp, lexicon))
    matched = 0
    for position, term in reference_terms:
        indices = [matches.get(i) for i in range(position, position + len(term))]
        if indices[0] is not None and indices == list(range(indices[0], indices[0] + len(term))):
            matched += (indices[0], term) in predicted_terms
    ref_chars, hyp_chars = " ".join(ref), " ".join(hyp)
    return {"reference_words": len(ref), "reference_chars": len(ref_chars),
            "substitutions": changes["substitutions"], "deletions": changes["deletions"],
            "insertions": changes["insertions"], "word_errors": sum(changes.values()),
            "character_errors": character_distance(ref_chars, hyp_chars),
            "reference_terms": len(reference_terms), "predicted_terms": len(predicted_terms),
            "matched_terms": matched, "false_term_insertions": len(predicted_terms) - matched}


def rates(counts):
    def ratio(numerator, denominator):
        return numerator / denominator if denominator else None

    return {"wer": ratio(counts["word_errors"], counts["reference_words"]),
            "cer": ratio(counts["character_errors"], counts["reference_chars"]),
            "term_precision": ratio(counts["matched_terms"], counts["predicted_terms"]),
            "term_recall": ratio(counts["matched_terms"], counts["reference_terms"]),
            "term_f1": ratio(2 * counts["matched_terms"], counts["predicted_terms"] + counts["reference_terms"])}
