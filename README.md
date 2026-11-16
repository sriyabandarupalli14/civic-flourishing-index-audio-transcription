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

To reproduce the offline comparison, run `python3 collect_stage1b.py --reference "$CFI_REFERENCE"` with `CFI_REFERENCE` set to the original research directory and the Stage 1A combined CSV available locally. Generated CSVs, coverage reports, and replay results are Git-ignored.

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

Run `python3 process_stage2.py` from the repository root with the Stage 1 consolidated CSV available locally. The Git-ignored Stage 2 output directory contains the recording manifest, the partition without classified media, a review subset spanning both partitions, the lineage table, and the before-and-after report.

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
