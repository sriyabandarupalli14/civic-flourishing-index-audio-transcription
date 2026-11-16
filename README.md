# Civic Flourishing Index — Audio Transcription

## Overview

The Civic Flourishing Index (CFI) is a research initiative at UC Berkeley that aims to develop a multidimensional framework for measuring civic health across American communities.

This repository focuses on the development of an end-to-end pipeline for collecting, processing, and transcribing church sermons to support the study of civic life within religious institutions.

## Project Objectives

- Automate sermon collection across heterogeneous websites and media platforms.
- Standardize, deduplicate, and validate sermon metadata.
- Develop a scalable speech-to-text pipeline using Whisper.
- Evaluate the effectiveness of domain-specific vocabulary prompting on transcription accuracy.
- Produce a structured research dataset for downstream textual analysis.

## Research Context

University of California, Berkeley\
Data Discovery Program\
Civic Flourishing Index

## Repository Setup

| Directory | Purpose |
| --- | --- |
| `scripts/` | Command-line entry points for collection, replay, cleaning, and media ingestion |
| `collectors/` | Church registry, platform adapters, collection dispatch, consolidation, and cached replay |
| `processing/` | Metadata normalization, recording grouping, and Stage 2 reconciliation |
| `media_ingestion/` | Eligibility, media transport, decoding, checkpoints, and reports |
| `transcription/` | Local ASR inference, checkpointing, and Stage 3 handoff |
| `evaluation/` | Seeded clip sampling, reference generation, and agreement metrics |
| `vocabulary/` | Metadata-only lexical and semantic ranking and prompt budgeting |
| `configs/` | Versioned model, evaluation, and vocabulary settings |
| `docs/` | Research plan and reproduction instructions |
| `tests/` | Offline regression tests |
| `outputs/` | Git-ignored local research outputs |

Use Python 3.11 or newer. Run these commands from the repository root on macOS or Linux.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-transcription.txt
python -m unittest discover
```

Chromium is required for live collectors that use Playwright. Install it with
`python -m playwright install chromium` before running those collectors.

The command-line modules retain their existing arguments and default output paths.
Run them from the repository root with `python3 -m scripts.<module>`.

| Module | Operation |
| --- | --- |
| `scripts.sermon_collectors` | Collect metadata for an active church key or `all`. Use `--help` for supported keys. This uses live sources. |
| `scripts.collection_outputs` | Combine saved first-20 church outputs. Use `--output` to point to their directory. |
| `scripts.collect_stage1b` | Replay historical captures and build the all-44 consolidation offline. Pass `--reference` for the original research directory. |
| `scripts.process_stage2` | Reconcile the consolidated CSV and produce the Stage 2 manifests offline. |
| `scripts.ingest_media` | Plan or report offline, or probe and ingest explicitly selected recording IDs. |

## Data Collection

Stage 1 reviewed all 44 church entries in the master registry. It collected and preserved sermon references and source metadata from heterogeneous websites and media platforms.

### Collection Architecture

- **Church registry** stores church IDs, archive URLs, platform assignments, and collection status. Church-specific configuration stays separate from shared parsing and request logic.
- **Shared utilities** handle metadata-only HTTP requests, URL resolution, and extraction from saved responses. Platform adapters handle static HTML, browser-rendered catalogs, embedded players, feeds, and public APIs using Python, BeautifulSoup, Playwright, and yt-dlp where applicable.
- **Standardized output** carries source references, titles, dates and their meaning, speakers, durations, and media URLs where available. The consolidation step preserves original fields and provenance, including missing values and URL query strings.

### Stage 1 Results

| Metric | Count |
| --- | ---: |
| Master-sheet church entries reviewed | 44 |
| Church IDs represented in the consolidated export | 40 |
| Entries with no retained observations | 4 |
| Stage 1A observations carried forward | 5,390 |
| Saved November observations for entries #21–44 | 10,920 |
| Saved November observations for inactive entry #8 | 915 |
| **Consolidated raw observations** | **17,225** |
| Offline cache-replay rows, counted separately | 10,882 |
| Stage 1B observations without a media reference | 48 |
| Automated tests passed | 41 |

One observation can represent a source listing, a file or player reference, or another appearance of the same recording. The export retains repeats, alternate media references, and potentially overlapping archives. A unique sermon count awaits record reconciliation and deduplication.

### Cached Replay and Verification

Stage 1B replayed saved November HTML, JSON, and feed responses without network access. The comparison checks exact media references and available titles, dates, speakers, and durations against the preserved observations. The consolidated CSV carries forward saved observations, with replay rows stored separately to avoid counting them again. The full consolidation was verified with HTTP requests disabled.

To reproduce the offline comparison, run `python3 -m scripts.collect_stage1b --reference "$CFI_REFERENCE"` with `CFI_REFERENCE` set to the original research directory and the Stage 1A combined CSV available locally. Generated CSVs, coverage reports, and replay results are Git-ignored.

### Known Limitations

- Entries #8 and #18 remain inactive. Entry #8 contributes saved observations only. Entries #18, #21, #31, and #33 have no retained observations. The November review held out 959 unrelated channel observations associated with #31.
- Some archives were unavailable or incomplete in the saved evidence. Candidate identities, attachment relevance, shared archives, and blocked Drive listings remain open. Missing captures prevented full replay equivalence for some routes.
- Offline verification establishes what can be recovered and compared from saved evidence. It does not establish live archive completeness, current media availability, or playback. Signed URLs may expire. Final metadata validation, deduplication, and media accessibility checks are pending.

## Data Cleaning and Deduplication

Stage 2 processes the Stage 1 consolidated CSV offline. It standardizes whitespace and recognized missing values, parses dates into ISO calendar dates while preserving their source meaning, and converts durations to seconds.

### Duplicate Detection and Grouping

1. **Exact duplicates.** The pipeline collapses repeated normalized payloads when the church, scoped source identifiers, metadata, date meaning, exact typed references, and review flags agree. Rows without reliable reference evidence require a verbatim match within the same church scope.
2. **Recording groups.** A church ID and a scoped platform or message ID can tie multiple observations to one group. An exact file or individual player URL can group otherwise unidentified observations when the metadata is compatible, or attach them to one unambiguous platform group.
3. **Review boundaries.** Titles, URL paths, and shared stream endpoints alone do not trigger a merge. Full query strings are retained. Conflicting platform IDs and cross-church matches stay separate for review.

### Provenance and Outputs

The input CSV remains unchanged. A lineage table links every input record to its exact-payload representative and provisional recording group, including source observation IDs and evidence locators where available. The manifest retains alternate audio, video, stream, and player URLs alongside unresolved endpoints and non-media assets. Normalized metadata variants remain available when display fields differ across observations.

Run `python3 -m scripts.process_stage2` from the repository root with the Stage 1 consolidated CSV available locally. The Git-ignored Stage 2 output directory contains the recording manifest, the partition without classified media, a review subset spanning both partitions, the lineage table, and the before-and-after report.

### Before and After

| Measure | Count |
| --- | ---: |
| Stage 1 input observations | 17,225 |
| Verbatim repeated input rows | 0 |
| Exact normalized payload repeats collapsed | 528 |
| Observations after exact deduplication | 16,697 |
| Further observations combined through identifier or reference grouping | 2,165 |
| **Provisional recording groups** | **14,532** |
| Groups with a classified media reference | 14,190 |
| Groups without classified media, including three with unresolved endpoints | 342 |
| Groups flagged for review across both partitions | 3,567 |
| Automated tests passed | 52 |

Review flags cover unresolved church or campus identities, shared archives and cross-church references, conflicting metadata or platform identifiers, signed URL variants, attachment relevance, and absent or malformed media references. Some input references were player-library scripts and remain recorded separately from media. Flags can overlap. Group counts remain provisional pending review, and no live accessibility or playback check was performed.

## Media Validation and Audio Ingestion

Stage 3 reads the Stage 2 recording manifest. The offline eligibility plan preserves recording IDs, source metadata, alternate URLs, observation lineage, and all original review flags. Eligible groups can enter media validation. Transcription candidates require decoded full audio and a verified checkpoint.

### Eligibility Rules

| Offline disposition | Recording groups |
| --- | ---: |
| **Eligible for media validation** | **12,092** |
| Inactive church archive | 715 |
| Unresolved church identity | 680 |
| Content relevance review | 384 |
| No supported media reference | 238 |
| Recording ambiguity | 81 |
| **Held** | **2,098** |
| **Total** | **14,190** |

The revised rules released 1,360 warning-only groups. These include 593 groups with incidental player-library assets and supported media, 425 Monroeville groups whose titles differ by an `- Audio` suffix under the same Sermon.net recording ID, 277 separate groups sharing a title and date, and 65 Brierfield groups with historical archive availability warnings. The held manifest retains overlapping reasons. The table counts each group by its primary hold. Stage 2's 342 groups without classified media remain in their separate partition.

### Ingestion Methodology

- Direct audio and video references use an HTTP HEAD request and a bounded ranged GET. YouTube and SoundCloud use yt-dlp for metadata-only resolution before checking the media endpoint. HLS checks the playlist and a prefix of its first segment.
- Ingestion stages a bounded file or a selection of HLS segments. PyAV extracts and decodes the audio to mono 16 kHz PCM WAV. The pipeline records decoded duration, sample rate, channel count, and a file checksum.
- Retryable failures use bounded retries. The pipeline can try alternate references from the same group and refresh signed URLs through known public BoxCast and Adventist routes. Local JSONL logs record attempts and failure reasons. Verified WAV checkpoints can be reused when the inputs and settings match.

Install the dependencies above and make the Stage 2 manifest available locally. Substitute an eligible ID for `RECORDING_ID` in the two network commands.

```sh
python3 -m scripts.ingest_media plan
python3 -m scripts.ingest_media probe --recording RECORDING_ID
python3 -m scripts.ingest_media ingest --recording RECORDING_ID --sample-seconds 15 --max-mib 8
python3 -m scripts.ingest_media report
python3 -m unittest discover
```

Planning and reporting run offline. Probing and ingestion require explicit IDs. The default output directory, `outputs/sermon_collection/stage3/`, is Git-ignored. It contains the eligibility manifests, logs, WAV files, checkpoints, and separate inventories for decoded previews and full-audio transcription candidates.

### Pilot Results

A live pilot on six selected recording groups used a 15-second target and an 8 MiB staging limit per reference attempt. Five previews decoded successfully. The pilot produced zero full-audio transcription candidates.

| Media type | Observed outcome |
| --- | --- |
| Direct audio | An MP3 prefix decoded successfully. |
| Direct video | One MP4 prefix failed to decode after a successful probe. A second MP4 recording yielded a preview. |
| YouTube | A preview decoded after metadata resolution. Separate probes encountered a transport error, including on retry. |
| SoundCloud | The player resolved to HLS, the first segment probe succeeded, and a preview decoded. |
| HLS | A BoxCast reference expired. Refresh recovered a stream for the same broadcast, and a preview decoded. |

All 66 automated tests passed offline. Corpus-wide media validation, audio ingestion, and transcription are pending.

### Known Limitations

- A successful probe establishes partial access. Video containers can require metadata beyond the fetched prefix, and full recording availability remains unverified.
- HLS support covers unencrypted playlists. Encrypted, byte-range, and gap playlists are unsupported. Full HLS capture requires a completed playlist.
- The 238 unsupported groups comprise 156 Google Drive and 82 Facebook references at the group level. An offline yt-dlp check recognized the URL formats for all 156 Drive groups and 78 Facebook groups. Four Facebook short share links need redirect resolution. Anonymous access, download permissions, and usable media transport remain unverified.

## Local Transcription Research

The study compares Whisper base.en, small.en and medium.en, then evaluates original,
expanded and metadata-selected vocabulary prompts. Whisper large-v3 and independently
trained wav2vec2-CTC serve as separate automated pseudo-references. Word, character and
domain-term metrics describe reference-relative agreement. Human-validated accuracy
remains unmeasured.

The seed-20261116 sample contains 49 verified one-minute clips across 10 churches,
with 10 development clips and 39 held-out clips. The missing 50th slot and all bounded
acquisition failures are recorded locally. Refer to the [research plan](docs/ML_PLAN.md),
[local reproduction commands](docs/ML_RUNBOOK.md), and
[vocabulary methodology](docs/VOCABULARY.md). Generated audio, predictions, metrics,
figures and local model weights remain Git-ignored.

### Local benchmark findings

On 39 held-out clips, the unprompted baselines produced the following results on
the same CPU INT8 settings. WER is relative to the named automated reference.

| Model | WER relative to large-v3 | WER relative to wav2vec2 | Median inference per 60 s | Median sampled peak RSS |
| --- | ---: | ---: | ---: | ---: |
| base.en | 18.09% | 31.51% | 2.87 s | 648 MiB |
| small.en | 8.68% | 27.13% | 7.97 s | 1,424 MiB |
| medium.en | 8.01% | 25.99% | 22.28 s | 2,472 MiB |

All three shared the same baseline domain-term F1 against each reference.
Each prompted base.en and small.en condition lowered domain-term F1 against
both references on this sample. The provisional production default is
unprompted small.en on CPU INT8. It met the study's predeclared agreement and
runtime thresholds against unprompted medium.en. The detailed local tables,
figures and error audit are generated under `outputs/evaluation/runs/`.
