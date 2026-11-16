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
