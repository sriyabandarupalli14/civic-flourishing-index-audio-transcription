# Local ML reproduction and continuation

Run from the repository root on a machine with Python 3.11. The source, configuration and test dependencies are tracked. The original research directory supplies historical evidence and remains read-only.

```sh
python3 -m pip install -r requirements-transcription.txt
python3 -m unittest discover
```

## Frozen dataset

`outputs/evaluation/plan.json` contains the final seed, exclusions, development anchor, Stage 2 manifest SHA-256, selected recordings, and full source rows. `plans/` contains earlier selection snapshots and `logs/` contains the three acquisition ledgers. Media requests used Stage 3 ingestion with one direct-audio reference per recording, a four-MiB cap, and at most 90 decoded seconds. The final clip inventory points to exactly 60 seconds of mono PCM16 at 16 kHz, starting 30 seconds into each recording. It distinguishes ready and missing slots. The 10 development and 39 held-out clips represent different provisional recording groups. Both partitions share churches. Eligible direct audio and successful access determine the sample frame and can bias coverage. Speaker and topic diversity were limited by metadata. The ready clips include 33 with an identified speaker, spanning 13 distinct stored names, 45 with a title, and 39 with a date. Sampling did not stratify by speaker or recording conditions.

Rebuild exact windows from the preserved prefix cache and historical audio:

```sh
python3 -m scripts.prepare_evaluation materialize --cache-index outputs/evaluation/cache_index.json
```

To reconstruct the plan independently, pass every `development_ids`, `failed_recordings` and `failed_churches` entry in the saved plan through respectively `--development-recording`, `--failed-recording` and `--failed-church`. The input manifest and config must have the original bytes and settings. Compare the resulting `plan_sha256`. Calling `acquire` makes bounded live requests and may produce different source bytes if a URL changes. The saved sample and its checksums are the basis for rerunning the experiments.

## Local weights

Use revision-pinned Hugging Face snapshots in the ignored `models/<name>/<revision>/` hierarchy. On this workstation the base.en weights were already cached externally. Do not send audio or metadata to a model service.

| Label | Upstream identifier | Snapshot revision |
| --- | --- | --- |
| base.en | Systran/faster-whisper-base.en | `3d3d5dee26484f91867d81cb899cfcf72b96be6c` |
| small.en | Systran/faster-whisper-small.en | `d1d751a5f8271d482d14ca55d9e2deeebbae577f` |
| medium.en | Systran/faster-whisper-medium.en | `a29b04bd15381511a9af671baec01072039215e3` |
| large-v3 | Systran/faster-whisper-large-v3 | `edaa852ec7e145841d8ffdb056a99866b5f0a478` |
| wav2vec2 | facebook/wav2vec2-base-960h | `22aad52d435eb6dbaf354bdad9b0da84ce7d6156` |
| minilm | sentence-transformers/all-MiniLM-L6-v2 | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` |

For a single ASR job, specify the exact local directory containing the model files. This example creates independent reference checkpoints:

```sh
python3 -m scripts.run_asr_batch --engine wav2vec2 \
  --model-dir models/wav2vec2/22aad52d435eb6dbaf354bdad9b0da84ce7d6156 \
  --output outputs/evaluation/runs/wav2vec2
```

## Study and scoring

```sh
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
python3 -m scripts.rank_vocabulary \
  --embedding-model models/minilm/1110a243fdf4706b3f48f1d95db1a4f5529b4d41
python3 -m scripts.run_ml_study --base-snapshot "$CFI_BASE_SNAPSHOT"
```

Set `CFI_BASE_SNAPSHOT` to the local base.en snapshot first. Resume with the same command and paths. Jobs match checkpoint configuration, metadata, audio bytes and transcript integrity. Each result is saved atomically. Use `--resume-after-baselines` when baseline and reference checkpoints already exist. The driver records per-condition logs and a study ledger. Development selection ranks dynamic candidates by highest mean term F1 against available complete references, then lowest mean WER, lower K, and method name. The guard freezes the selection on disk. Do not discard it after held-out results exist.

`scripts.score_asr` independently verifies checkpoints against the clip manifest and computes pooled, per-clip and per-church metrics. Use `--reference label=directory`, `--prediction label=directory` and `--compare first=second`. WER normalizes punctuation and case and counts insertions, deletions and substitutions. CER uses the normalized word string with single spaces. Domain scores count aligned, contiguous term occurrences with longest-match precedence. Common terms can have a nonreligious sense. Empty denominators yield undefined metrics. Small church-cluster bootstrap intervals are descriptive and can be unstable. Only comparisons with complete clip pairing and adequate term evidence are checked against the practical thresholds in the plan. Results remain reference-relative.

## Production handoff

`scripts.transcribe_recordings` accepts explicitly selected Stage 2 recording IDs and verifies Stage 3 full-audio checkpoints before inference. A signed-source change, held identity, preview-only checkpoint, or damaged checksum remains outside production inference. Use `scripts.stage_local_recording` for an isolated full-length local smoke test with a pinned source checksum and historical metadata JSON. That command writes normalized audio to a separate Stage 3-compatible directory. The existing Stage 3 outputs can remain read-only.

The production output contains text and timestamped segments with low confidence, timestamp, potential silence hallucination and repetition flags. Candidate speech is retained for review. The pipeline checkpoints by recording, audio SHA-256, source provenance and configuration, with hash-based sharding supported. The optional scripts.retry_transcription_spans command writes one to three alternate decodes of flagged spans to a separate review artifact. Original speech and timing are preserved. Validate alternate decode quality before applying any correction. For Savio, stage the same model revisions and verified audio, choose a compatible CUDA/CTranslate2 setup, smoke-test one shard, and verify runtime, memory and filesystem locking before increasing concurrency.

### Error analysis and report

Use the same reference and prediction paths with `scripts.audit_prompt_errors` to write `term_errors.json` under the run directory. It counts unmatched lexicon occurrences, how many were offered as hints, and literal appearances of the first three hints together. These diagnostics are reference-relative. A prompt-term mismatch alone cannot identify an actual hallucination. Then run `python3 -m scripts.render_ml_report` to rebuild the local research report from the saved comparisons, frozen settings, retrieval rankings and term audit. Add `--production-run outputs/transcription/production_small` to include verified full-length checkpoint statistics.

The production preset is `configs/production.toml`. It uses
small.en unprompted on four CPU threads with INT8. Use it on explicitly
selected verified audio as follows:

```sh
python3 -m scripts.transcribe_recordings --recording "$CFI_RECORDING_ID" \
  --model-dir models/small.en/d1d751a5f8271d482d14ca55d9e2deeebbae577f
```

This command consumes existing full Stage 3 audio. For an isolated full
handoff use `--audio-dir` pointing to the audio directory produced by
`scripts.stage_local_recording`. A custom config can also enable the
original static vocabulary or supply configuration for the optional
expanded or dynamic production selector.
