# Poster figures

The vector PDFs and 300 dpi PNGs show aggregate results from saved held-out
predictions. WER and terminology F1 measure agreement with automated
reference transcripts. Human-verified accuracy was not measured.

| Figure | Cohort | Files |
| --- | --- | --- |
| Model benchmark | Original 39-clip study, unprompted base.en, small.en and medium.en | [PDF](model-benchmark.pdf) · [PNG](model-benchmark.png) |
| Vocabulary V0–V3 | Original 39-clip study, small.en | [PDF](vocabulary-original.pdf) · [PNG](vocabulary-original.png) |
| Vocabulary follow-up | Separate 40-clip holdout, small.en only | [PDF](vocabulary-follow-up.pdf) · [PNG](vocabulary-follow-up.png) |

The follow-up flagged three of 626 segments and performed zero retries.
Partial Medium and canceled V5 have no rows in the follow-up figure.
Use the [research report](../ASR_RESEARCH_FINDINGS.md) for exact scores,
confidence intervals, methods, and limitations.

Rebuild the panels from the saved local comparison JSON files without
running inference. Run from the repository root with the transcription
dependencies installed.

```sh
MPLBACKEND=Agg python -m scripts.render_poster_figures --output /tmp/cfi-poster-figures
```
