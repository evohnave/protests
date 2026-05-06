"""Scrape GAO bid protest decisions from the public search and docket pages."""

import csv
import json
import time
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

SEARCH_URL = "https://www.gao.gov/search?f%5B0%5D=ctype_search%3ABid%20Protest%20Decision&page={page}"
DOCKET_URL = "https://www.gao.gov/docket/{file_number}"
CHECKPOINT_FILE = Path("checkpoint.json")
DEFAULT_OUTPUT = Path("protests.csv")
DEFAULT_LIMIT = 40
REQUEST_DELAY = 2

CSV_FIELDS = [
    "docket",
    "file_number",
    "protester",
    "decision_date",
    "filed_date",
    "due_date",
    "publicly_released_date",
    "outcome",
    "case_type",
]


def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text())
    return {"page": 0, "processed": []}


def save_checkpoint(checkpoint):
    CHECKPOINT_FILE.write_text(json.dumps(checkpoint, indent=2))


def load_csv(path):
    rows = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows[row["docket"]] = row
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows.values())


def parse_search_page(html):
    soup = BeautifulSoup(html, "lxml")
    results = []
    for item in soup.select("div.c-search-result"):
        link = item.select_one("h4.c-search-result__header a")
        if not link:
            continue

        docket_el = item.select_one("span.d-block.text-small")
        docket = docket_el.get_text(strip=True) if docket_el else ""
        if not docket:
            continue

        # First time = "Publicly Released on", second = "Published" (decision date)
        times = item.select("span.text-small time[datetime]")
        publicly_released = times[0]["datetime"][:10] if len(times) > 0 else ""

        results.append(
            {
                "docket": docket,
                "publicly_released_date": publicly_released,
            }
        )
    return results


def docket_to_file_number(docket):
    """Return the primary (first) file number from a possibly comma-joined docket string."""
    return docket.split(",")[0].strip().lower()


def parse_docket_page(html):
    soup = BeautifulSoup(html, "lxml")

    def field_text(css_name):
        el = soup.select_one(f"div.field--name-{css_name} div.field__item")
        return el.get_text(strip=True) if el else ""

    def field_date(css_name):
        el = soup.select_one(f"div.field--name-{css_name} time[datetime]")
        return el["datetime"][:10] if el else ""

    # File number has no field--name-* class; find by its label text
    file_number = ""
    for label in soup.find_all(["h2", "header"], class_="field__label"):
        if label.get_text(strip=True) == "File number":
            item = label.find_next_sibling("div", class_="field__item")
            if item:
                file_number = item.get_text(strip=True)
            break

    return {
        "file_number": file_number,
        "protester": field_text("field-protestor"),
        "outcome": field_text("field-outcome"),
        "decision_date": field_date("field-decision-date"),
        "filed_date": field_date("field-filed-date"),
        "due_date": field_date("field-due-date"),
        "case_type": field_text("field-case-type"),
    }


def main():
    parser = argparse.ArgumentParser(description="Scrape GAO bid protest decisions")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="Max number of decisions to fetch (0 = no limit)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output CSV path",
    )
    args = parser.parse_args()

    checkpoint = load_checkpoint()
    processed = set(checkpoint.get("processed", []))
    rows = load_csv(args.output)

    count = 0
    page = checkpoint.get("page", 0)

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context()

        try:
            while args.limit == 0 or count < args.limit:
                print(f"Fetching search page {page}...")
                pg = context.new_page()
                pg.goto(
                    SEARCH_URL.format(page=page),
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                html = pg.content()
                pg.close()

                results = parse_search_page(html)
                if not results:
                    print("No more results.")
                    break

                for result in results:
                    if args.limit > 0 and count >= args.limit:
                        break

                    docket = result["docket"]
                    if docket in processed:
                        print(f"  Skipping {docket} (already processed)")
                        continue

                    time.sleep(REQUEST_DELAY)
                    file_number = docket_to_file_number(docket)
                    print(f"  Fetching docket for {docket}...")

                    dp = context.new_page()
                    dp.goto(
                        DOCKET_URL.format(file_number=file_number),
                        wait_until="networkidle",
                        timeout=30000,
                    )
                    docket_html = dp.content()
                    dp.close()

                    docket_data = parse_docket_page(docket_html)

                    new_row = {
                        "docket": docket,
                        "publicly_released_date": result["publicly_released_date"],
                        **docket_data,
                    }

                    existing = rows.get(docket)
                    if existing is None or existing["outcome"] != new_row["outcome"]:
                        rows[docket] = new_row
                        write_csv(args.output, rows)

                    processed.add(docket)
                    checkpoint["processed"] = list(processed)
                    save_checkpoint(checkpoint)
                    count += 1
                    print(f"  Saved: {docket} | {docket_data['outcome'] or '(no outcome)'} | filed {docket_data['filed_date']}")

                if args.limit > 0 and count >= args.limit:
                    break

                page += 1
                checkpoint["page"] = page
                save_checkpoint(checkpoint)
                time.sleep(REQUEST_DELAY)

        finally:
            browser.close()

    print(f"\nDone. Fetched {count} new records. Total in {args.output}: {len(rows)}")


if __name__ == "__main__":
    main()
