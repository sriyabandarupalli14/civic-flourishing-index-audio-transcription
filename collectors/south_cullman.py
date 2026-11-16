"""Metadata collector for the south cullman archive."""

import asyncio
import os
import re
from pathlib import Path

import pandas as pd
from playwright.async_api import TimeoutError as PWTimeout, async_playwright

from .metadata import metadata_browser_page
from .records import format_collection

DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2}")   # card dates look like 9/20/26

# Runs inside the browser: for each unique mp3 link, climb to the block holding the card
# text (the date line contains "|") and return that text line by line.
GET_CARDS = r"""() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.href;
    if (!h.toLowerCase().split('?')[0].endsWith('.mp3') || seen.has(h)) continue;
    seen.add(h);
    let box = a;
    for (let i = 0; i < 6 && box.parentElement; i++) {
      box = box.parentElement;
      if (box.innerText.includes('|')) break;
    }
    out.push({href: h, pieces: box.innerText.split('\n').map(s => s.trim()).filter(Boolean)});
  }
  return out;
}"""

RAW_COLS = ["audio_url", "sermon_title", "speaker", "date_card", "tags", "date_file", "page"]


async def load_page(page, url, tries=3):
    """Open one list page. Returns (http_status, cards). Retries slow pages."""
    for t in range(1, tries + 1):
        try:
            # networkidle can hang on some pages, so wait for the mp3 links themselves instead
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if resp is None:
                return "no_response", []
            if resp.status != 200:
                return resp.status, []
            try:
                await page.wait_for_selector('a[href*=".mp3"]', timeout=10000)
            except PWTimeout:
                pass   # no mp3 links appeared; the caller decides whether the page is empty
            return 200, await page.evaluate(GET_CARDS)
        except PWTimeout:
            await asyncio.sleep(3 * t)
    return "timeout", []


async def collect_cards_raw(base_url, raw_csv, page_param="c_page", max_pages=70):
    """Read every list page and save the raw cards to `raw_csv`, one page at a time.

    If `raw_csv` already exists, the run resumes after the last saved page.
    """
    if os.path.exists(raw_csv):
        old = pd.read_csv(raw_csv, dtype=str)
        seen, start = set(old["audio_url"]), int(old["page"].astype(int).max()) + 1
    else:
        seen, start = set(), 1
    empty_streak = 0
    Path(raw_csv).parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await metadata_browser_page(browser)
        for n in range(start, max_pages + 1):
            url = base_url if n == 1 else f"{base_url}?{page_param}={n}"
            status, cards = await load_page(page, url)
            if status == 200 and not cards:   # check once more before trusting "empty"
                await asyncio.sleep(3)
                status, cards = await load_page(page, url, tries=1)
            if status != 200:
                raise RuntimeError(f"Page {n} failed ({status}); checkpoint retained, final export not replaced")

            new_rows = []
            for c in cards:
                if c["href"] in seen:
                    continue
                seen.add(c["href"])
                pieces = c["pieces"]
                # Find the date line by pattern so a missing speaker cannot shift the others
                date_piece = next((s for s in pieces if DATE_RE.match(s)), "")
                parts = [x.strip() for x in date_piece.split("|")]
                speaker = pieces[1] if len(pieces) > 1 and pieces[1] != date_piece else ""
                m = re.search(r"/(\d{8})_", c["href"])   # filenames start with YYYYMMDD
                new_rows.append({
                    "audio_url": c["href"],
                    "sermon_title": pieces[0] if pieces else "",
                    "speaker": speaker,
                    "date_card": parts[0] if parts else "",
                    "tags": " | ".join(parts[1:]),
                    "date_file": m.group(1) if m else "",
                    "page": n,
                })
            if new_rows:   # save this page now, so a crash never costs more than one page
                previous = pd.read_csv(raw_csv, dtype=str, keep_default_na=False) if os.path.exists(raw_csv) else pd.DataFrame(columns=RAW_COLS)
                checkpoint = Path(raw_csv)
                temporary = checkpoint.with_suffix(".tmp")
                pd.concat([previous, pd.DataFrame(new_rows, columns=RAW_COLS)], ignore_index=True).to_csv(temporary, index=False)
                temporary.replace(checkpoint)

            empty_streak = empty_streak + 1 if not new_rows else 0
            if empty_streak >= 2:   # two empty pages in a row means the list has ended
                break
            await asyncio.sleep(1)   # be gentle with the site
        await browser.close()
    if not os.path.exists(raw_csv):
        raise ValueError("No cards collected; no checkpoint exists")
    return pd.read_csv(raw_csv, dtype=str, keep_default_na=False)


def cards_to_rows(raw):
    """Convert the raw cards into the standard table."""
    card_iso = pd.to_datetime(raw["date_card"], format="%m/%d/%y", errors="coerce").dt.strftime("%Y-%m-%d")
    file_iso = pd.to_datetime(raw["date_file"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
    title = raw["sermon_title"].fillna("")

    # service_type = first piece of the site's tags (Sun AM, Gospel Meeting, ...)
    service = (raw["tags"].fillna("").str.split("|").str[0].str.strip()
               .replace({"N/A": "unlabeled", "": "unlabeled"}))

    # Notes: flag cut-off titles, and keep the filename date visible when the two dates disagree
    conflict = card_iso.notna() & file_iso.notna() & (card_iso != file_iso)
    note = pd.Series("", index=raw.index)
    note = note.mask(title.str.endswith("..."), "title cut off by site")
    note.loc[conflict] = note.loc[conflict].map(lambda s: s + "; " if s else "") + "date conflict, filename says " + file_iso.loc[conflict]

    return pd.DataFrame({
        "audio_url": raw["audio_url"],
        "sermon_title": title,
        "sermon_date": card_iso,
        "speaker": raw["speaker"].fillna(""),
        "service_type": service,
        "data_note": note,
    })


def checkpoint_result(raw, base_url):
    metadata = cards_to_rows(raw).fillna("")
    for key in ("date_card", "date_file", "tags", "page"):
        metadata[key] = raw[key]
    metadata["date_basis"] = metadata["sermon_date"].map(lambda date: "displayed card date" if date else "")
    return format_collection(metadata.to_dict("records"), base_url,
                             coverage=[{"checkpoint_cards": len(raw), "last_card_page": int(raw["page"].astype(int).max())}],
                             limitations=["Page checkpoints retain previous cards; start a new checkpoint to rescan page one"])


async def collect_cards(base_url, raw_csv, page_param="c_page", max_pages=70):
    raw = await collect_cards_raw(base_url, raw_csv, page_param, max_pages)
    return checkpoint_result(raw, base_url)


def merge_south_checkpoint(final, raw):
    """Retain checkpoint metadata without double-counting the same media URL."""
    left = final.fillna("").astype(str).copy()
    right = raw.fillna("").astype(str).copy()
    normalized = cards_to_rows(right).fillna("").astype(str)
    for col in normalized:
        if col not in right: right[col] = normalized[col]
    for df in [left, right]:
        df["audio_url"] = df.audio_url.str.strip()
        if df.audio_url.eq("").any() or df.audio_url.duplicated().any():
            raise ValueError("South Cullman merge requires unique, nonblank audio URLs")
    common = (set(left) & set(right)) - {"audio_url"}
    merged = left.merge(right, on="audio_url", how="outer", suffixes=("", "_checkpoint"), validate="one_to_one").fillna("")
    for col in sorted(common):
        alt = col + "_checkpoint"
        blank = merged[col].eq("")
        merged.loc[blank, col] = merged.loc[blank, alt]
        conflicts = merged[alt].ne("") & merged[col].ne(merged[alt])
        if not conflicts.any(): merged = merged.drop(columns=alt)
    merged["additional_source_csv"] = "south_cullman_pages.csv"
    return merged
