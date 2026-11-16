# Experiment 2 extension: selective vocabulary adaptation

V4 tests whether a bounded vocabulary retry adds value over unprompted small.en
and over an otherwise identical retry without hints. The prior sample,
checkpoints, configurations and the unprompted production preset are independent.

## Sample and references

Seed 20261117 ranks eligible direct-audio recordings by passage-like metadata
and literal lexicon matches in the title, description or passage field. Seeded
hashes break ties. The plan takes five provisional recording groups per church
across ten churches, with one development recording per church. Each clip spans
30–90 seconds. The planner excludes every recording in the prior sample and
checks its platform identity, URL paths and same-church title keys as additional
duplicate guards. Materialization checks audio and clip checksums, including
exact clip overlap with the earlier study. The two partitions share churches.

Stage 3 fetches at most one 4 MiB prefix per selected recording per acquisition
round. Replacement rounds use download and decode failures only. The three
rounds fetched 61 candidates to materialize 50 clips. Passage-like titles and
early sermon windows can enrich the sample, with no guarantee that a title term
is spoken in that minute. Local large-v3 Whisper and independent
wav2vec2-base-960h produce separate automated references. WER, CER and term
agreement are reference-relative. The study has no human transcript labels.

## Matched control and acceptance

Both V4 arms begin with the same unprompted small.en decoding configuration.
They look for low log probability, high compression, possible silence and
short repeated phrases. A candidate hint requires an exact topical metadata
match and a plausible surface or approximate phonetic match in a first-pass
span. The compact phonetic key is a heuristic, and there is no padding or
generic fallback. With no plausible term, both arms abstain unless a repetition
trigger warrants an unprompted retry. V4-A and V4-B use the same candidate gate,
scope and acceptance rules. Both retry at most one segment of 1–30 seconds.
Only V4-B supplies selected hints, within a five-term and 16-hotword-token cap.

The merge retains all neighboring segments and the original timestamp envelope.
It rejects empty or unchanged output, repetitive output, possible silence,
unsupported increases in lexicon counts, and excessive changes to local
wording or both segment boundaries. A narrow exception permits deletion of
obvious repeated surplus. An accepted segment stores the original and alternate
text in the event ledger and omits stale word-level timings. Raw decoder
confidence never decides acceptance by itself.

Compare the two predefined policies on the ten development recordings using
mean V4-B term F1 across references, then mean WER and the lower token cap.
The frozen policy applies to both arms on held-out clips. The reported success
check requires V4-B to improve term F1 against V0 and V4-A on both references
without increasing WER. The efficiency target is at least 20% lower median
runtime than unprompted medium.en. Clip-level timing includes baseline decode,
live retrieval and prompt construction where applicable, span extraction,
retry, and merge, with initialization separate. Sampled peak RSS is process
memory. V3 uses live hybrid retrieval with the original top-32 and 96-token
settings.

## Reproduction

Run with the pinned transcription dependencies and local model directories.
Use a fresh ignored output directory for sample acquisition.

```sh
python3 -m scripts.prepare_v4 --acquire --output outputs/evaluation_v4
python3 -m scripts.run_asr_batch --engine wav2vec2 --model-dir models/wav2vec2/22aad52d435eb6dbaf354bdad9b0da84ce7d6156 --manifest outputs/evaluation_v4/clip_manifest.json --output outputs/evaluation_v4/runs/wav2vec2
python3 -m scripts.run_asr_batch --model large-v3 --prompt none --model-dir models/large-v3/edaa852ec7e145841d8ffdb056a99866b5f0a478 --manifest outputs/evaluation_v4/clip_manifest.json --output outputs/evaluation_v4/runs/large-v3
for policy in conservative moderate; do
  for mode in V4-A V4-B; do
    python3 -m scripts.run_v4_condition "$mode" --data outputs/evaluation_v4 --subset development --policy "$policy" --label "$mode-$policy"
  done
done
python3 -m scripts.run_v4_study --data outputs/evaluation_v4
python3 -m scripts.report_v4 --data outputs/evaluation_v4
```

The saved manifest resolves clips relative to its own directory. Acquisition
ledgers retain the original capture paths and hashes. For archived prefix
re-materialization after relocation, use the matching basename in the copied
prefix cache and verify the original recorded checksum.
