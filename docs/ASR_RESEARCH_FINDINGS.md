# Civic Flourishing Index ASR research findings

## Study design

The first study benchmarked three unprompted English Whisper models and their vocabulary strategies on 39 held-out one-minute clips, with 10 development clips across the same 10 churches. The second study used a recording-disjoint, metadata-enriched sample with 10 development and 40 held-out one-minute clips across 10 churches. Both studies scored the same predictions independently against Whisper large-v3 and wav2vec2-base-960h. The baseline used faster-whisper with CTranslate2, CPU INT8, four threads, beam size 5, temperature 0, and no previous-text conditioning. In both studies, windows ran from 30 to 90 seconds. V5 was canceled before sample acquisition or inference.

## Hypothesis 1 — Model size and efficiency

| Condition | large-v3 WER / CER | large-v3 term P / R / F1 | large-v3 unmatched | wav2vec2 WER / CER | wav2vec2 term P / R / F1 | wav2vec2 unmatched | Median seconds | Median peak RSS MiB |
| --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: |
| base.en | 18.09% / 12.93% | 100.00% / 100.00% / 100.00% | 0 | 31.51% / 17.83% | 69.57% / 96.97% / 81.01% | 14 | 2.87 | 648.3 |
| small.en | 8.68% / 6.80% | 100.00% / 100.00% / 100.00% | 0 | 27.13% / 14.90% | 69.57% / 96.97% / 81.01% | 14 | 7.97 | 1424.0 |
| medium.en | 8.01% / 6.33% | 100.00% / 100.00% / 100.00% | 0 | 25.99% / 13.57% | 69.57% / 96.97% / 81.01% | 14 | 22.28 | 2472.2 |

Both references favored larger models by point estimate. Relative to medium.en, small.en used 64.23% less median inference time and scored +0.67 pp WER against large-v3 and +1.14 pp against wav2vec2. Term F1 was equal for the two models on each reference. The paired church-resampling intervals for medium.en minus small.en WER included zero (−4.90 to +1.70 pp against large-v3 and −4.97 to +1.02 pp against wav2vec2), so the incremental medium.en advantage is uncertain at this sample size.

## Hypothesis 2 — Vocabulary adaptation

### V0–V3 on the original held-out clips

V0 is unprompted. V1 used the original static lexicon. V2 used the expanded, category-balanced lexicon. V3 ranked the expanded lexicon against sermon metadata with the frozen hybrid retriever, top 32, and a 96-hotword-token budget. Original V3 clip timing excludes cached retrieval. The automated references contained 46 and 33 scored term occurrences respectively.

| Condition | large-v3 WER / CER | large-v3 term P / R / F1 | large-v3 unmatched | wav2vec2 WER / CER | wav2vec2 term P / R / F1 | wav2vec2 unmatched | Median seconds | Median peak RSS MiB |
| --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: |
| base.en V0 | 18.09% / 12.93% | 100.00% / 100.00% / 100.00% | 0 | 31.51% / 17.83% | 69.57% / 96.97% / 81.01% | 14 | 2.87 | 648.3 |
| base.en V1 | 15.56% / 13.54% | 43.81% / 100.00% / 60.93% | 59 | 31.94% / 19.19% | 30.48% / 96.97% / 46.38% | 73 | 3.11 | 725.2 |
| base.en V2 | 15.30% / 16.37% | 25.41% / 100.00% / 40.53% | 135 | 30.67% / 21.16% | 17.68% / 96.97% / 29.91% | 149 | 3.01 | 718.5 |
| base.en V3 | 19.54% / 18.42% | 63.89% / 100.00% / 77.97% | 26 | 35.11% / 24.80% | 44.44% / 96.97% / 60.95% | 40 | 2.94 | 714.0 |
| small.en V0 | 8.68% / 6.80% | 100.00% / 100.00% / 100.00% | 0 | 27.13% / 14.90% | 69.57% / 96.97% / 81.01% | 14 | 7.97 | 1424.0 |
| small.en V1 | 13.96% / 18.55% | 12.33% / 100.00% / 21.96% | 327 | 29.81% / 23.72% | 8.58% / 96.97% / 15.76% | 341 | 10.27 | 1566.2 |
| small.en V2 | 18.03% / 20.07% | 13.61% / 100.00% / 23.96% | 292 | 33.84% / 25.67% | 9.47% / 96.97% / 17.25% | 306 | 9.77 | 1756.7 |
| small.en V3 | 9.54% / 9.13% | 34.35% / 97.83% / 50.85% | 86 | 27.58% / 16.61% | 24.43% / 96.97% / 39.02% | 99 | 9.22 | 1543.7 |
| medium.en V0 | 8.01% / 6.33% | 100.00% / 100.00% / 100.00% | 0 | 25.99% / 13.57% | 69.57% / 96.97% / 81.01% | 14 | 22.28 | 2472.2 |
| medium.en V1 | 13.67% / 13.21% | 44.55% / 97.83% / 61.22% | 56 | 31.13% / 20.33% | 29.70% / 90.91% / 44.78% | 71 | 23.85 | 2590.5 |
| medium.en V2 | 12.14% / 11.20% | 92.00% / 100.00% / 95.83% | 4 | 30.11% / 19.18% | 64.00% / 96.97% / 77.11% | 18 | 23.42 | 2693.2 |
| medium.en V3 | 8.91% / 7.65% | 97.83% / 97.83% / 97.83% | 1 | 28.14% / 16.50% | 69.57% / 96.97% / 81.01% | 14 | 24.43 | 2734.2 |

No prompted smaller-model condition met all prespecified thresholds against medium.en on both references. Against large-v3, small.en V1 and V2 produced 327 and 292 unmatched occurrences, all from their respective hint sets. V3 reduced that count to 86, with a substantial F1 penalty relative to V0. Some base.en prompt variants reduced WER against large-v3 while losing term precision.

### V4 on the separate held-out sample

V4-A and V4-B share the verified unprompted first pass, retry gate, candidate spans, and merge checks. V4-A retries without hints. V4-B adds a small evidence-gated term list. V3 in this study includes live retrieval time. V4 runtime adds the measured shared V0 cost and the measured per-arm adaptation cost. V4 peak RSS is the larger of the measured phase peaks. Model initialization is measured separately. V4 addresses adaptation to small.en on this cohort. The follow-up medium.en process was stopped after 2 completed clips, which were preserved locally and excluded from every reported score. Experiment 1 remains the basis for comparing model sizes.

| Condition | large-v3 WER / CER | large-v3 term P / R / F1 | large-v3 unmatched | wav2vec2 WER / CER | wav2vec2 term P / R / F1 | wav2vec2 unmatched | Median seconds | Median peak RSS MiB |
| --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: |
| V0 | 6.30% / 5.22% | 99.10% / 97.35% / 98.21% | 1 | 14.87% / 6.95% | 77.48% / 100.00% / 87.31% | 25 | 13.17 | 1285.3 |
| V3 | 10.93% / 9.42% | 81.54% / 93.81% / 87.24% | 24 | 19.84% / 11.53% | 63.85% / 96.51% / 76.85% | 47 | 15.29 | 1705.8 |
| V4-A | 6.30% / 5.22% | 99.10% / 97.35% / 98.21% | 1 | 14.87% / 6.95% | 77.48% / 100.00% / 87.31% | 25 | 13.17 | 1285.3 |
| V4-B | 6.30% / 5.22% | 99.10% / 97.35% / 98.21% | 1 | 14.87% / 6.95% | 77.48% / 100.00% / 87.31% | 25 | 13.17 | 1285.3 |

Development froze the **conservative** policy. The held-out first pass contained 626 segments, with 3 diagnostic triggers. V4-A retried 0 spans and accepted 0. V4-B retried 0 spans, used hints on 0, and accepted 0. The criterion requiring higher term F1 than V0 and V4-A on both references without added WER did not pass. Both V4 arms retained all V0 transcripts. With zero retries, the acceptance rate is undefined and the experiment cannot estimate the effect of vocabulary on a retried span.

V3 had 3 clips with repetition compared with 0 for V0 and either V4 arm. The most frequent unmatched V3 term was “Jesus Christ,” with 18 occurrences against large-v3 and 19 against wav2vec2. V4-B added 0.0006 s of median adaptation overhead per clip. This measures the abstention path because no second pass ran.

## Production decision

The production preset uses **unprompted small.en**. medium.en posted the strongest unprompted WER in the original benchmark, while small.en met the efficiency and agreement thresholds. The selected preset pins the local model revision and uses CPU INT8, four threads, beam size 5 and temperature 0. The existing full-length production checkpoint was reverified against its audio, model files, settings and transcript hash. It covers 50.45 minutes, 823 timestamped segments, 550.43 seconds of inference and 3.15 GiB sampled peak RSS. This single recording verifies execution and checkpoint reuse.

## Reference disagreement and coverage

On the original held-out sample, large-v3 scored 24.36% WER relative to wav2vec2. wav2vec2 scored 25.76% WER relative to large-v3. On the V4 sample, wav2vec2 scored 18.23% WER relative to large-v3. large-v3 scored 17.53% WER relative to wav2vec2.
The V4 large-v3 reference contained 113 occurrences of 38 scored terms across 33 held-out clips.
The V4 wav2vec2 reference contained 86 occurrences of 26 scored terms across 28 held-out clips.

WER, CER and terminology scores measure agreement with automated pseudo-references. large-v3 shares the Whisper model family. wav2vec2 uses a different architecture. Both can err on names, song, silence and word boundaries. Scoring aligns normalized tokens and counts repeated, contiguous lexicon occurrences with longest-match precedence. Literal lexicon counts cannot distinguish every religious sense or spelling variant. Terms unmatched against one reference require caution. Accessible direct-audio recordings, early clips, shared churches and limited treatment exposure restrict generalization. Human transcription accuracy is unknown.

## Local results and reproduction

The original tables, per-church results, repetition analysis and figures are under `outputs/evaluation/runs/`. V4 holds its paired comparisons, retry ledger, term audit and three comparative figures under `outputs/evaluation_v4/report/`. Both output trees are Git-ignored. See the [ML runbook](ML_RUNBOOK.md) and [V4 protocol](EXPERIMENT_2_V4.md) for reproduction.
