# Production handoff

The default is the pinned, unprompted `small.en` CPU INT8 preset in
[`configs/production.toml`](../configs/production.toml). The local checkpoint
verifies full-length execution and reuse. Savio hardware and scheduling still
need verification. V4 remains an optional research entry point documented in
[the V4 protocol](EXPERIMENT_2_V4.md). It is disabled in the production path.

## Savio deployment

- [ ] Confirm project allocation, storage permissions, and the approved
  recording set. Keep held identities, ambiguous recordings, and unreconciled
  content separate.
- [ ] Validate media, ingest selected full audio, and verify input checksums.
  Stage approved manifests, audio and the pinned model snapshot outside Git.
  Keep source URLs and transcripts inside project-approved storage. Use the
  [Savio data transfer guide](https://docs-research-it.berkeley.edu/services/high-performance-computing/user-guide/data/transferring-data/)
  and [staging guide](https://docs-research-it.berkeley.edu/services/high-performance-computing/user-guide/data/staging-data/).
- [ ] Build the Python 3.11 environment on Savio and run the tests. Use a
  compute node through Slurm. If evaluating CUDA for deployment, verify the
  compatible CTranslate2 runtime with a separate local config and record the
  changed hardware settings. [Savio user guide](https://docs-research-it.berkeley.edu/services/high-performance-computing/user-guide/)
- [ ] Smoke-test one approved full recording and test checkpoint reuse before
  parallelizing. Confirm consistent input hashes, model revision, audio
  duration, timestamps, output permissions, and resource consumption.
- [ ] Submit the approved batch in deterministic shards. Pass the identical
  approved ID list and shard count to each job, use a zero-based shard index,
  and use a persistent output directory. Resume with the same inputs,
  configuration, and shard arguments. Monitor failed and missing IDs before
  retrying or expanding coverage.

Example command for a future approved single-recording smoke test. Start from
the repository root. The environment variables refer to authorized inputs and
a work directory outside Git.

```sh
python -m scripts.transcribe_recordings \
  --manifest "$CFI_RECORDING_MANIFEST" --audio-dir "$CFI_VERIFIED_AUDIO" \
  --model-dir "$CFI_SMALL_SNAPSHOT" --config configs/production.toml \
  --recording "$CFI_RECORDING_ID" --shard-count 1 --shard-index 0 \
  --output "$CFI_TRANSCRIPTION_OUTPUT"
```

## Quality checks and Parquet delivery

- [ ] Reconcile every approved recording ID against run status, checkpoint
  coverage, and the timestamped transcript. Review silence, repetition,
  timestamp and confidence flags and preserve exclusions and failure reasons.
- [ ] Define the recording and segment export schemas with the research team.
  Preserve join keys, timestamps, source lineage, audio hashes, and model
  configuration identifiers. Review which source fields are appropriate for
  delivery.
- [ ] Implement and run the final Parquet export. Verify row counts, ID
  uniqueness, recording-to-segment joins, timestamp bounds, null handling, and
  read-back checks. Save an export manifest and checksums with the approved
  dataset in research storage.
