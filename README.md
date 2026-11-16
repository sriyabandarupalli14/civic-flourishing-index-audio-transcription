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

The collection pipeline is designed to extract individual sermon references and associated metadata from 44 church archives across heterogeneous websites and media-hosting platforms.

### Collection Architecture

The pipeline uses a modular, platform-based scraping architecture that separates reusable functionality from platform-specific extraction methods and church-specific configurations.

- **Church registry:** Maintains church identifiers, archive URLs, hosting platforms, and collection status.
- **Shared utilities:** Handle HTTP requests, URL resolution, and common metadata extraction.
- **Platform-specific collectors:** Support static HTML, dynamically rendered websites, embedded media players, and platform APIs.
- **Standardized schema:** Provides a consistent output format across heterogeneous sources.

### Collection Methods

The pipeline utilizes Python, BeautifulSoup, Playwright, and yt-dlp to discover sermon-level records and extract available metadata, including titles, dates, speakers, durations, and media URLs.

### Initial Collection Results

| Metric                        | Count |
| ----------------------------- | ----- |
| Churches reviewed             | 20    |
| Church collectors implemented | 18    |
| Churches excluded             | 2     |
| Consolidated records          | 5,390 |
| Automated tests passed        | 32    |

Regression testing against historical collection outputs and cached website responses was used to evaluate the refactored collectors. Incomplete cached responses prevented full equivalence testing for some sources.

The consolidated dataset contains both historical and newly collected records. Final deduplication, media accessibility checks, and metadata validation will be performed during subsequent processing stages.
