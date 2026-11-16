import unittest

from evaluation.metrics import counts, index_terms, rates, tokens


class ReferenceMetricTests(unittest.TestCase):
    def test_alignment_counts_inserted_and_recovered_terms(self):
        lexicon = index_terms(["Jesus Christ", "Christ", "redemption"])
        result = counts("Jesus Christ spoke.", "Jesus Christ redemption spoke.", lexicon)
        self.assertEqual((result["insertions"], result["matched_terms"], result["false_term_insertions"]), (1, 1, 1))
        self.assertEqual(rates(result)["term_recall"], 1)
        self.assertEqual(rates(result)["term_precision"], 0.5)
        self.assertEqual(tokens("Don’t."), ["don't"])
        self.assertIsNone(rates(counts("words", "words", lexicon))["term_f1"])

    def test_zero_denominators_and_character_edits(self):
        lexicon = index_terms(["redemption"])
        result = counts("", "Redemption!", lexicon)
        self.assertIsNone(rates(result)["wer"])
        self.assertEqual(result["word_errors"], 1)
        self.assertEqual(result["character_errors"], 10)
        self.assertEqual(result["false_term_insertions"], 1)

    def test_comparable_unprompted_model_remains_preferred_without_prompt_gain(self):
        from scripts.render_ml_report import recommend

        refs = ("independent", "whisper")
        benchmark = {"reference_predictions": dict.fromkeys(refs, 39), "summaries": [
            {"condition": name, "reference": ref, "wer": wer, "missing_pairs": 0, "term_f1": .8,
             "median_inference_seconds": runtime}
            for ref in refs for name, wer, runtime in (("medium.en", .08, 20), ("small.en", .09, 8))
        ]}
        adaptation = {"summaries": [*benchmark["summaries"], *(
            {"condition": "small.en-dynamic", "reference": ref, "term_f1": .8,
             "median_inference_seconds": 7} for ref in refs
        )], "paired_comparisons": [
            {"first": name, "second": "medium.en", "reference": ref, "meets_comparability_thresholds": True}
            for name in ("small.en", "small.en-dynamic") for ref in refs
        ]}
        self.assertIn("**small.en**", recommend(benchmark, adaptation))

    def test_prompt_echo_requires_contiguous_first_three_hints(self):
        from scripts.audit_prompt_errors import prompt_echo

        output = {"text": "We read Exodus, Passover, Moses.",
                  "prompt": {"token_usage": {"selected_terms": ["Exodus", "Passover", "Moses"]}}}
        self.assertTrue(prompt_echo(output))
        output["text"] = "We read Exodus, then Passover and Moses."
        self.assertFalse(prompt_echo(output))
