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

    def test_matched_control_and_treatment_use_same_audio_span(self):
        import tempfile
        import wave
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        from transcription.adaptive import AdaptiveInference

        settings = {**self.policy, "hotword_budget": 8, "term_limit": 2, "relevance": .7,
                    "max_span_seconds": 30, "max_retries": 1}
        row = {"id": 1, "text": "today we read ezekial and pray together", "compression_ratio": 1,
               "no_speech_probability": .1, "avg_logprob": -.9, "start_seconds": 0, "end_seconds": 2}
        neighbor = {**row, "id": 2, "text": "then we continued reading", "start_seconds": 2, "end_seconds": 3}
        baseline = {"segments": [row, neighbor]}

        def decode(path, hotwords):
            self.assertTrue(Path(path).is_file())
            text = row["text"].replace("ezekial", "Ezekiel") if hotwords else row["text"]
            return [SimpleNamespace(text=text, compression_ratio=1, no_speech_prob=.1)], None

        def prepare(_model, _decoding, terms, _budget):
            return (", ".join(terms) or None), {"selected_terms": terms}

        engine = SimpleNamespace(configuration_sha256="base", config={"decoding": {}},
                                 model=SimpleNamespace(transcribe=decode))
        with tempfile.TemporaryDirectory() as folder, patch("transcription.adaptive.prepare_prompt", prepare):
            audio = Path(folder) / "fixture.wav"
            with wave.open(str(audio), "wb") as out:
                out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                out.writeframes(b"\x01\x00" * 48000)
            source = {"title": "Ezekiel"}
            a, control = AdaptiveInference(engine, self.lexicon, settings, False).revise(audio, baseline, source)
            b, treatment = AdaptiveInference(engine, self.lexicon, settings, True).revise(audio, baseline, source)
        self.assertEqual(control[0]["span_sha256"], treatment[0]["span_sha256"])
        self.assertEqual(control[0]["outcome"], "empty_or_unchanged")
        self.assertEqual(treatment[0]["outcome"], "accepted")
        self.assertEqual(a, baseline["segments"])
        self.assertEqual(b[1], neighbor)
        self.assertIn("Ezekiel", b[0]["text"])
        self.assertEqual(b[0]["end_seconds"], row["end_seconds"])
