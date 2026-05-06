# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the scraper (default: 40 decisions)
uv run python scrape.py

# Fetch a custom number of decisions
uv run python scrape.py --limit 60

# Fetch everything (no limit)
uv run python scrape.py --limit 0

# Write to a custom output file
uv run python scrape.py --output other.csv
```

After the first `uv sync` or `uv run`, dependencies are managed automatically. If Playwright's Chromium is missing, run `uv run playwright install firefox` (the site blocks Chromium).

## Architecture

Single-file scraper (`scrape.py`) with two-phase fetching:

**Phase 1 — search page** (`https://www.gao.gov/search?f[0]=ctype_search:Bid Protest Decision&page=N`): paginates 20 results per page. Each `div.c-search-result` yields docket, protester name, decision date ("Published" `<time>`), public release date, and the detail URL.

**Phase 2 — detail page** (`/products/b-XXXXXX`): fetched once per result to extract the outcome from `div.status.highlighted-status` (e.g., "We deny the protest.").

`case_type` is inferred from the protester title string ("--Reconsideration" → Reconsideration, "--Reimbursement" → Request for Reimbursement, else Protest).

**Resume**: `checkpoint.json` tracks the current page number and a set of already-processed docket strings. On restart the scraper re-fetches the checkpoint page and skips processed dockets. The CSV is rewritten on every new record, so it is always consistent with the checkpoint.

**Bot detection**: The GAO site (Akamai CDN) blocks headless Chromium; Firefox headless passes through. All fetching uses `playwright.sync_api` with Firefox.

## Key data notes

- `filed_date` and `due_date` are not available as structured HTML fields anywhere on the GAO site — they would require parsing prose from the full decision text.
- Dockets with multiple numbers (e.g., `B-424168.2,B-424168.3`) appear as a comma-joined string and are used as the CSV key.
- `decision_date` is the "Published" date from the search results (ISO 8601, YYYY-MM-DD). `publicly_released_date` is when the protective order was lifted.
