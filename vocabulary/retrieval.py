"""Rank curated vocabulary from permitted sermon metadata fields."""

import csv
import re

from evaluation.metrics import tokens
from media_ingestion.pipeline import hash_file

FIELDS = ("church_name", "title", "description", "scripture_reference", "speaker")


def read_lexicon(path):
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows or len({tuple(tokens(row["term"])) for row in rows}) != len(rows):
        raise ValueError("Expected a unique normalized term per row")
    return rows


def metadata_query(source):
    return " ".join(str(source[key]) for key in FIELDS if source.get(key))


def contains(words, term):
    return any(words[index:index + len(term)] == term for index in range(len(words) - len(term) + 1))


class Retrieval:
    def __init__(self, lexicon, encode=None):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.entries = lexicon
        self.texts = [f"{row['term']} {row['category']} {row['context']}" for row in lexicon]
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
        self.lexical = self.vectorizer.fit_transform(self.texts)
        self.encode = encode
        self.semantic = encode(self.texts) if encode else None

    def rank(self, source, method="hybrid", top_k=24, fallback=()):
        query = metadata_query(source)
        # Titles consisting only of a generic service label carry little topical evidence.
        topical = " ".join(str(source[key]) for key in ("title", "description", "scripture_reference")
                           if source.get(key))
        if not tokens(topical) or re.fullmatch(
                r"(?:[\d /-]*\s*)?(?:(?:sunday|morning|evening)\s+)?(?:sermon|livestream|service|worship)(?:\s*[\d /-]*)?",
                topical.casefold().strip()):
            return list(fallback)[:top_k]
        if method not in {"exact", "lexical", "semantic", "hybrid"}:
            raise ValueError("Unsupported retrieval method")
        words = tokens(query)
        exact = [int(contains(words, tokens(row["term"]))) for row in self.entries]
        lexical = (self.lexical @ self.vectorizer.transform([query]).T).toarray().ravel()
        if method in {"semantic", "hybrid"}:
            if self.semantic is None:
                raise ValueError("The selected retrieval method requires a local embedding model")
            semantic = (self.semantic @ self.encode([query])[0]).tolist()
        else:
            semantic = [0.0] * len(self.entries)
        if method == "exact":
            scores = exact
        elif method == "lexical":
            scores = [a + b for a, b in zip(exact, lexical)]
        elif method == "semantic":
            scores = semantic
        else:
            scores = [2 * a + 0.5 * b + 0.5 * max(0, c) for a, b, c in zip(exact, lexical, semantic)]
        ranked = sorted(range(len(scores)), key=lambda i: (-scores[i], self.entries[i]["term"].casefold()))
        terms = [self.entries[i]["term"] for i in ranked if scores[i] > 0][:top_k]
        return (terms + [term for term in fallback if term not in terms])[:top_k]


class MiniLM:
    """Mean-pool and L2-normalize local MiniLM outputs."""

    def __init__(self, snapshot):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        torch.set_num_threads(4)
        self.tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
        self.model = AutoModel.from_pretrained(snapshot, local_files_only=True, use_safetensors=True).eval()
        self.provenance = {"model": "sentence-transformers/all-MiniLM-L6-v2", "revision": snapshot.name,
                           "files_sha256": {p.name: hash_file(p) for p in sorted(snapshot.iterdir()) if p.is_file()}}

    def __call__(self, texts):
        vectors = []
        with self.torch.inference_mode():
            for offset in range(0, len(texts), 32):
                inputs = self.tokenizer(texts[offset:offset + 32], padding=True, truncation=True,
                                        max_length=256, return_tensors="pt")
                mask = inputs["attention_mask"].unsqueeze(-1)
                hidden = self.model(**inputs).last_hidden_state
                average = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
                vectors.append(self.torch.nn.functional.normalize(average, dim=1))
        return self.torch.cat(vectors).cpu().numpy()


def balanced_static(lexicon):
    """Interleave categories without using sample metadata or recognition results."""
    categories = {}
    for row in lexicon:
        categories.setdefault(row["category"], []).append(row["term"])
    return [terms[index] for index in range(max(map(len, categories.values())))
            for terms in categories.values() if index < len(terms)]


def rankings_fingerprint(artifact):
    from transcription.checkpoints import digest

    rankings = {key: {method: row[method] for method in ("exact", "lexical", "semantic", "hybrid")}
                for key, row in artifact["rankings"].items()}
    return digest({"plan_sha256": artifact["plan_sha256"], "lexicon_sha256": artifact["lexicon_sha256"],
                   "embedding": artifact["embedding"], "rankings": rankings})
