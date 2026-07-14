# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A single-file Playwright scraper (`main.py`) that drives a real Google Maps browser session, extracts business listings, and writes them to CSV. There is no test suite, linter, or build step — the only artifact is the script itself.

## Commands

```bash
pip install -r requirements.txt
playwright install                                  # required once; installs browser binaries

python main.py -s "Turkish Restaurants in Toronto" -t 20
python main.py -s "..." -t 20 -o out.csv --append   # append instead of overwrite
python main.py -s "..." -t 50 --emails --require-contact
```

Flags: `-s/--search` (default `"turkish stores in toronto Canada"`), `-t/--total` (default `1`), `-o/--output` (default `result.csv`), `--append`, `--emails`, `--require-contact`.

`-t` is a ceiling, not a target — the scroll loop stops early when the result list is genuinely exhausted, so an unreachably high `-t` means "scrape everything".

Python 3.9+ (developed on 3.13). `requirements.txt` uses minimum-version constraints rather than exact pins: the old `numpy==1.26.4` pin had no wheel for 3.13 and sent pip into a meson source build that fails. Only the three direct imports are listed — numpy, greenlet, pyee and the rest arrive transitively. Re-pinning exactly will reintroduce that failure on any Python newer than the pin.

## Architecture

Four stages, all in `main.py`:

1. `scrape_places()` — launches a **non-headless** Chromium, loads a hardcoded Google Maps URL with a world-level zoom, fills the search box, then scroll-loops (`page.mouse.wheel`) until either `total` listing anchors exist or the count stops growing between iterations. It collects `//a[contains(@href, ".../maps/place")]` anchors and walks up one level (`xpath=..`) to get clickable listing containers.
2. Per listing: click, wait for the name element, `time.sleep(1.5)`, then `extract_place()`.
3. `extract_place()` — resolves ~14 XPaths into a `Place` dataclass. Every read goes through `extract_text()`, which returns `""` on any failure and logs a warning, so a broken XPath degrades silently rather than crashing. Listings with an empty `name` are skipped.
4. `save_places_to_csv()` — `Place` → DataFrame → CSV.

### Things that bite

**XPaths are the whole product.** They are obfuscated Google class names (`TIHn2`, `DUwDvf lfPIob`, `LTs0Rc`) hardcoded as string locals in `extract_place()`, plus the search-input and listing-anchor locators in `scrape_places()`. Google changes these without notice. When the scraper returns empty fields or zero results, the XPaths are the first suspect — not the logic. Some fields already carry fallbacks for this reason (`opens_at` tries `opens_at_xpath`, then `opens_at_xpath2`).

**The output schema depends on the data.** `drop_uninformative_columns()` removes any column whose values are all identical, which is the intentional "data cleansing" the README advertises — but it means a run where every business shares a phone number drops `phone_number` entirely. Two constraints keep this from corrupting output, and both are load-bearing:

- It is skipped for single-row frames, since one row makes every column look constant. Without this the default `-t 1` writes a row with no columns at all.
- It is skipped entirely when appending. `save_places_to_csv()` instead reads the existing header and reindexes onto it, so rows conform to what is already on disk and missing fields become empty cells. Dropping columns on an append path produces rows wider than the header and a CSV that pandas cannot even parse back.

**The Windows Chrome path is hardcoded** to `C:\Program Files\Google\Chrome\Application\chrome.exe` in `scrape_places()`; non-Windows uses Playwright's bundled Chromium. Headless mode is off deliberately — Google Maps behaves differently headless.

**Parsing is locale-sensitive.** `reviews_average` does `.replace(',', '.')` to handle European decimal commas, and several fields strip non-breaking spaces (`\xa0`, ` `) and split on `·` / `⋅`. Preserve this when touching parsing code.

## Email extraction (`--emails`)

Google Maps never exposes an email — only a link to the business site — so the
`email` field is filled by a second pass (`enrich_with_emails()`) that visits
each website in a headless browser. Three things in it are load-bearing and
were each established against real sites, not guessed:

- **`emails_on_page()` scans the raw HTML as well as the rendered text.**
  Addresses routinely live in script variables and data attributes rather than
  visible text; scanning text alone misses them.
- **`contact_links()` follows the site's own contact link rather than trying a
  list of guessed paths.** Real sites use `/contacto/`, `contacto.html`,
  `contacto.php` — guessing misses nearly all of them.
- **`open_page()` waits for `networkidle` after `domcontentloaded`.** The
  address usually sits in a footer that renders late. Without this wait the
  extractor is *non-deterministic*: the same site yields an address on one run
  and nothing on the next. If a future change makes the yield mysteriously
  flaky, look here first.

Measured yield is roughly 5 of 8 sites, and about 70% of scraped businesses
have a website at all. `is_plausible_email()` rejects asset filenames
(`logo@2x.png`) and analytics/theme addresses; `pick_best_email()` prefers an
address on the company's own domain over a personal gmail on the same page.

`--require-contact` drops records with neither a phone nor an email, and logs
how many it dropped rather than discarding them silently.

## Branches

`main` is the reference implementation. `Latest_Libraries` tracks current dependency versions (known to be less stable), `Linux` carries Linux-specific fixes. Changes to scraping logic may need porting across all three.
