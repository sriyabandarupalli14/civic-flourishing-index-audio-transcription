"""Rank the fixed lexicon using only the frozen clip manifest's source metadata."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from media_ingestion.pipeline import atomic_json, hash_file
from vocabulary.retrieval import MiniLM, Retrieval, read_lexicon


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("outputs/evaluation/clip_manifest.json"))
    parser.add_argument("--lexicon", type=Path, default=Path("configs/religious_lexicon.tsv"))
    parser.add_argument("--baseline-vocabulary", type=Path, default=Path("configs/sermon_vocabulary.json"))
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/evaluation/vocabulary_rankings.json"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    fallback = json.loads(args.baseline_vocabulary.read_text(encoding="utf-8"))["hotwords"]
    encoder = MiniLM(args.embedding_model)
    retriever = Retrieval(read_lexicon(args.lexicon), encoder)
    ranks = {}
    for clip in manifest["clips"]:
        if clip["status"] != "ready":
            continue
        start = perf_counter()
        ranks[clip["clip_id"]] = {
            method: retriever.rank(clip["source_record"], method=method, top_k=40, fallback=fallback)
            for method in ("exact", "lexical", "semantic", "hybrid")
        }
        ranks[clip["clip_id"]]["combined_retrieval_seconds"] = perf_counter() - start
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, {"plan_sha256": manifest["plan_sha256"], "lexicon_sha256": hash_file(args.lexicon),
                              "embedding": encoder.provenance, "rankings": ranks})
    print(f"Ranked vocabulary for {len(ranks)} verified clips")


if __name__ == "__main__":
    main()
