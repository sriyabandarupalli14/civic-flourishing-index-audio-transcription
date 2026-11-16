# ML research plan

**H1** compares base.en, small.en and medium.en at consistent baseline decoding and hardware settings. **H2** tests whether vocabulary adaptation improves smaller models' agreement with independent references at lower computational cost. Each hypothesis can remain unsupported.

| Milestone | Evidence required |
| --- | --- |
| 1. Inference | Cached base.en smoke test, actual weights and runtime provenance, verified checkpoints |
| 2. Sample | Seeded 50-slot plan, immutable clip checksums, recording-disjoint partitions and coverage ledger |
| 3. References | Independent local large-v3 and wav2vec2-CTC outputs with disagreement counts |
| 4. Benchmark | Matched unprompted predictions, separate reference-relative metrics, time and memory |
| 5. Vocabulary | Published baseline list, documented expanded vocabulary, metadata-only retrieval and token budgets |
| 6. Adaptation | Frozen V0–V3 settings, development tuning, paired held-out comparisons |
| 7. Analysis | Tables and figures rebuilt from verified predictions, limitations and configuration recommendation |
| 8. Production | Resumable verified Stage 3 handoff, quality flags, one full-length local test |

## Sampling and experimental controls

Freeze the recording IDs, window, WAV bytes and subsets before experimental inference. A bounded replacement round can substitute inaccessible media using the same seeded ranking, based only on fetch and decode outcomes. Preserve the original plan and all acquisition outcomes. One previously explored sermon belongs to development. Treat missing audio and predictions as explicit coverage gaps. Use one clip per provisional recording, exclude potentially duplicate source or same-church title matches, and check byte-identical audio or clips. Recording separation does not establish separation by speaker or church.

Use the same held-out clips and reference-independent vocabulary across conditions. V0 has no prompt. V1 uses the original 30 entries. V2 uses a sourced expanded lexicon, and V3 retrieves from that lexicon using only church/sermon metadata. Fit or rank using vocabulary contexts and development evidence only. Use a common hotword token budget and record actual token usage. Context stays fixed across prompted primary conditions. Treat context-only and hotwords-only as development comparisons.

For practical comparability, on the same held-out clip set, a smaller prompted model must trail the larger unprompted model by at most **5 percentage points in term F1** and **3 points in WER** against each available reference, while having at least **20% lower median inference wall time** on the same hardware. A comparison needs at least 20 reference vocabulary occurrences per reference and reference-relative results from both models on every included clip. Report uncertainty, missing coverage and any dependence on just one reference. These are predeclared descriptive thresholds, with no equivalence or significance claim.

## Reference-relative evaluation

Use unprompted Whisper large-v3 and [wav2vec2-base-960h](https://huggingface.co/facebook/wav2vec2-base-960h), a CTC model trained on LibriSpeech, as separate local pseudo-references. Wav2vec2 is independent of Whisper's architecture and decoding procedure. Its read-speech training and vocabulary can be a poor match for church recordings. Whisper-family agreement can favor shared model errors, and both references can mishear terminology, omit words, or disagree on contractions, punctuation, silence and singing. Agreement measures therefore describe consistency with each reference under a pinned normalization. Reference consensus supplies no human-validated ground truth. Report each direction of reference disagreement and never replace one reference with their consensus.

Track resources in separate model-loading and inference intervals. CPU process RSS is sampled and includes model-resident memory. Transient peaks between polls and discrete GPU allocation are outside this RSS measure. Separate real-time factor from startup time and keep hardware, threading, checkpoint revisions and compute settings with each result.
