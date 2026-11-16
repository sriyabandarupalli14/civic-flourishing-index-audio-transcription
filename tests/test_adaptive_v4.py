import unittest

from evaluation.metrics import index_terms
from evaluation.v4_dataset import topic_score
from transcription.adaptive import acceptance, has_loop, relevant_terms, triggers


class AdaptiveTests(unittest.TestCase):
    lexicon = [{"term": word} for word in ("Ezekiel", "Gethsemane")]
    policy = {"compression": 2.4, "silence": .6, "text_similarity": .65, "logprob": -.8}

    def test_short_repetition_and_candidate_gate(self):
        self.assertTrue(has_loop(["jesus", "christ"] * 4))
        self.assertEqual(relevant_terms(self.lexicon, {"title": "Ezekiel 2:3"}, "we read ezekial today", .7, 5),
                         ["Ezekiel"])
        self.assertEqual(relevant_terms(self.lexicon, {"church_name": "Ezekiel"}, "we read ezekial", .7, 5), [])
        self.assertEqual(relevant_terms(self.lexicon, {"title": "Ezekiel"}, "we read Ezekiel", .7, 5), [])
        self.assertEqual(topic_score({"title": "Ezekiel 2:3"}, index_terms(["Ezekiel"]))[0], 5)

    def test_acceptance_limits_novel_terms_and_protects_anchors(self):
        index = index_terms(item["term"] for item in self.lexicon)
        original = {"text": "today we read ezekial and pray together"}
        clean = {"text": "today we read Ezekiel and pray together", "compression_ratio": 1, "no_speech_probability": .1}
        self.assertEqual(acceptance(original, clean, ["Ezekiel"], index, self.policy), "accepted")
        self.assertEqual(acceptance(original, clean, [], index, self.policy), "unsupported_terminology")
        self.assertEqual(acceptance(original, {**clean, "text": "Ezekiel " * 4}, ["Ezekiel"], index, self.policy),
                         "repetitive_retry")
        self.assertEqual(acceptance(original, {**clean, "text": "today we read Ezekiel Ezekiel and pray together"},
                                   ["Ezekiel"], index, self.policy), "unsupported_terminology")
        self.assertEqual(acceptance(original, {**clean, "text": "now we read Ezekiel and pray alone"},
                                   ["Ezekiel"], index, {**self.policy, "text_similarity": .55}), "changed_both_boundaries")

    def test_trigger_does_not_require_low_logprob(self):
        row = {"text": "safe text", "avg_logprob": -.1, "no_speech_probability": .8, "compression_ratio": 4}
        self.assertEqual(triggers(row, self.policy), ["repetition", "possible_silence"])
