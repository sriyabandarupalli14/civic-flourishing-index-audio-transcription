import unittest

from vocabulary.prompts import fit_hotwords
from vocabulary.retrieval import Retrieval, metadata_query


class VocabularyTests(unittest.TestCase):
    def test_metadata_only_and_sparse_fallback(self):
        entries = [{"term": "Pentecost", "category": "practice", "context": "Holy Spirit feast"},
                   {"term": "Passover", "category": "practice", "context": "Exodus deliverance lamb"}]
        engine = Retrieval(entries)
        source = {"title": "Exodus and Passover", "transcript": "Pentecost"}
        self.assertEqual(metadata_query(source), "Exodus and Passover")
        self.assertEqual(engine.rank(source, "lexical", top_k=1), ["Passover"])
        self.assertEqual(engine.rank({"title": "Sunday Service"}, "lexical", fallback=["Pentecost"]),
                         ["Pentecost"])

    def test_fits_whole_terms(self):
        value, details = fit_hotwords(["Holy Spirit", "Passover", "grace"], str.split, 3)
        self.assertEqual(value, "Holy Spirit, Passover")
        self.assertEqual(details["hotword_tokens"], 3)
        self.assertTrue(details["truncated"])
