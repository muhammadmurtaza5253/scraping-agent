# LLM-Powered Dynamic Web Scraper — System Specification

## Overview

Build a CLI-based Python web scraping agent. The user provides a URL, and the system automatically analyzes the page structure using an LLM, identifies scrapable data, generates scraping code, executes it, and produces a CSV — all without the user needing to inspect HTML or write selectors.

---

## Core Workflow

### Step 1 — User Input

Prompt the user for:

1. **URL** (required): The target website URL.
2. **Row/page limit** (optional): Either a target row count OR a page count. If not provided, default to scraping a maximum of **10 pages**.

Store these inputs in a `config.json` file in the working directory so they survive restarts.

### Step 2 — Fetch & Analyze

1. Fetch the HTML of the provided URL using `requests` (with a realistic `User-Agent` header).
2. If the response is not `200`, log the error and exit gracefully.
3. Clean the raw HTML to reduce token usage before sending to the LLM:
   - Strip `<script>`, `<style>`, `<noscript>`, `<svg>`, and `<link>` tags entirely.
   - Remove all HTML comments.
   - Collapse excessive whitespace.
   - If the cleaned HTML is still very large (>80,000 characters), truncate it but keep the first and last portions so the LLM can see headers/footers and the main content area.
4. Send the cleaned HTML to the LLM (Anthropic API, model `claude-haiku-4-5-20251001`) with a structured prompt asking it to:
   - Identify the **type of page** (listing, table, directory, search results, article index, etc.).
   - List all **repeating data items** it can find (e.g., product cards, job listings, table rows).
   - For each item, list the **columns/fields** that can be extracted (e.g., title, price, link, date).
   - Identify the **CSS selectors or XPath** for the container, each item, and each field.
   - Identify the **pagination mechanism** (next button, page numbers, infinite scroll, API endpoint, URL pattern like `?page=2`).
   - Return this analysis as structured JSON.

### Step 3 — Log Analysis & Confirm

1. Save the full LLM analysis JSON to `analysis.json`.
2. Print a human-readable summary to the console:
   - Page type detected
   - Number of items found on the first page
   - Columns/fields identified (as a table)
   - Pagination method detected
3. Ask the user for confirmation to proceed. Allow them to optionally exclude columns at this step.
4. Show estimated cost before the LLM call — after cleaning the HTML, calculate the approximate token count and print something like "This will use ~18,000 tokens (~$0.06). Proceed

### Step 4 — Generate Scraping Code

Send a second LLM call that receives the analysis JSON and generates a complete, self-contained Python scraping script (`scraper.py`) that:

- Uses `requests` + `BeautifulSoup` (bs4).
- Implements the pagination strategy identified in the analysis.
- Respects the row/page limit from user input.
- Writes results to `output.csv` **incrementally** — each page of results is appended to the CSV as soon as it is scraped (not buffered in memory until the end). This ensures partial results are saved if the process is interrupted.
- Includes a polite delay between requests (`time.sleep` of 1–2 seconds).
- Handles common HTTP errors gracefully (retries on 429/503 with exponential backoff, logs and skips on 404).
- Includes a `User-Agent` header that mimics a real browser.
- Logs every action to both console and a `scraper.log` file.

Save the generated script as `scraper.py`.

### Step 5 — Execute & Collect

1. Run `scraper.py` as a subprocess.
2. Stream its stdout/stderr to the console in real time so the user can see progress.
3. On completion (or interruption), report:
   - Total rows scraped
   - Total pages processed
   - Path to the output CSV
   - Path to the log file

---

## Project File Structure

```
project_dir/
├── config.json          # User inputs (URL, limits) — persisted for restarts
├── analysis.json        # Full LLM page analysis output
├── scraper.py           # LLM-generated scraping script
├── scraper.log          # Detailed execution log from the scraper
├── output.csv           # Incrementally written scraped data
└── run.py               # Main orchestrator script (entry point)
```

---

## Resilience & Interruption Handling

This is critical. The system must handle mid-run interruptions gracefully:

1. **Incremental CSV writes**: The generated `scraper.py` must open the CSV in append mode and flush after each page. Use `csv.writer` with the file opened in `'a'` mode. Write the header row only if the file is empty or doesn't exist.
2. **Progress checkpointing**: After each page is scraped, write the current page number to a `progress.json` file (`{"last_page_completed": N, "total_rows": M}`). On restart, the scraper reads this file and resumes from page N+1.
3. **Signal handling**: Register `SIGINT` and `SIGTERM` handlers in the scraper that:
   - Flush and close the CSV file.
   - Update `progress.json`.
   - Log a clean shutdown message.
   - Exit with code 0.
4. **The orchestrator (`run.py`)** should detect if `progress.json` exists on startup and ask the user whether to resume or start fresh.

---

## Logging & Tracing Requirements

Every significant action must be logged with a timestamp. Use Python `logging` module with both a `StreamHandler` (console) and a `FileHandler` (`scraper.log`).

Log the following events at minimum:

| Event | Log Level | Details |
|---|---|---|
| URL received from user | INFO | The URL and any limits |
| HTML fetch attempt | INFO | URL, response status code, content length |
| HTML fetch failure | ERROR | URL, status code, error message |
| LLM analysis request sent | INFO | Token count of the prompt (approximate) |
| LLM analysis received | INFO | Summary of detected structure |
| LLM code generation request sent | INFO | — |
| Scraper script saved | INFO | File path |
| Scraper started | INFO | Target pages/rows |
| Page fetch (each page) | INFO | Page number, URL, status code, items found |
| Page fetch retry | WARNING | Page number, attempt count, wait time |
| Rows written to CSV | INFO | Count of new rows, running total |
| Progress checkpoint saved | DEBUG | Page number, total rows |
| Scraper interrupted | WARNING | Signal received, rows saved so far |
| Scraper completed | INFO | Total rows, total pages, elapsed time |

---

## Technical Requirements

### Dependencies

- `requests` — HTTP fetching
- `beautifulsoup4` — HTML parsing
- `anthropic` — LLM API calls (use the `anthropic` Python SDK)
- `csv` (stdlib) — CSV writing
- `logging` (stdlib) — Logging
- `signal` (stdlib) — Interrupt handling
- `json` (stdlib) — Config/progress persistence
- `time` (stdlib) — Polite delays

Install via: `pip install requests beautifulsoup4 anthropic`

### LLM Configuration

- Model: `claude-sonnet-4-20250514`
- Use the `anthropic` Python SDK.
- API key: Read from the `ANTHROPIC_API_KEY` environment variable. If not set, prompt the user or exit with a clear error.
- For the analysis call, set `max_tokens` to `4096`.
- For the code generation call, set `max_tokens` to `8192`.

### LLM Prompt Design

**Analysis prompt** should instruct the model to return valid JSON only (no markdown fences, no preamble) with this schema:

```json
{
  "page_type": "string",
  "description": "string",
  "items": {
    "container_selector": "string",
    "item_selector": "string",
    "fields": [
      {
        "name": "string",
        "selector": "string",
        "attribute": "string | null",
        "description": "string"
      }
    ],
    "sample_count": "number"
  },
  "pagination": {
    "type": "next_button | page_numbers | url_pattern | api | none",
    "selector_or_pattern": "string | null",
    "notes": "string"
  }
}
```

**Code generation prompt** should include:
- The full analysis JSON.
- Explicit instructions to write incrementally to CSV.
- Instructions to implement progress checkpointing.
- Instructions to handle signals for graceful shutdown.
- The base URL for resolving relative links.
- The user's page/row limit.

---

## Edge Cases to Handle

1. **No repeating data found**: If the LLM analysis finds no structured repeating data, inform the user and exit. Don't generate a scraper for nothing.
2. **JavaScript-rendered content**: If the fetched HTML contains very little content (e.g., just a root div), warn the user that the site likely requires JavaScript rendering, and suggest they try with a different tool or provide a different URL. Do not attempt to use Playwright/Selenium — keep the system simple with `requests` only.
3. **Very large pages**: If the HTML is extremely large, truncate as described in Step 2 and note this in the analysis log.
4. **Pagination not found**: If no pagination is detected, scrape only the single page and note this.
5. **CSV encoding**: Write the CSV with `utf-8-sig` encoding so it opens correctly in Excel.
6. **Duplicate rows**: The generated scraper should track already-seen rows (by a hash of all field values) and skip duplicates. Log when duplicates are skipped.
7. **Empty fields**: If a field selector finds no match for a particular item, write an empty string for that cell rather than crashing.

---

## Usage Example

```
$ python run.py

═══════════════════════════════════════════
  LLM Web Scraper
═══════════════════════════════════════════

Enter the URL to scrape: https://example.com/products

How many pages to scrape? (default: 10, press Enter to use default): 5

[INFO] Fetching https://example.com/products ...
[INFO] Received 45,231 bytes of HTML (status 200)
[INFO] Sending HTML to LLM for analysis...
[INFO] Analysis complete.

Page type: Product listing
Items found on first page: 24
Pagination: Next button detected

Columns identified:
  1. product_name     — Name of the product
  2. price            — Listed price
  3. rating           — Star rating (out of 5)
  4. review_count     — Number of reviews
  5. product_url      — Link to product detail page

Proceed with scraping? [Y/n]: y

[INFO] Generating scraper code...
[INFO] Scraper saved to scraper.py
[INFO] Starting scraper (target: 5 pages)...
[INFO] Page 1/5 — 24 items scraped (24 total)
[INFO] Page 2/5 — 24 items scraped (48 total)
[INFO] Page 3/5 — 24 items scraped (72 total)
^C
[WARNING] Interrupt received. Saving progress...
[INFO] Progress saved. 72 rows written to output.csv

$ python run.py

Previous progress detected (72 rows, 3/5 pages).
Resume? [Y/n]: y

[INFO] Resuming from page 4...
[INFO] Page 4/5 — 24 items scraped (96 total)
[INFO] Page 5/5 — 24 items scraped (120 total)
[INFO] Scraping complete. 120 rows saved to output.csv
```

---

## Summary Checklist

- [ ] Single entry point: `python run.py`
- [ ] Only required user input is a URL (page/row limit is optional)
- [ ] HTML is fetched, cleaned, and sent to LLM for structural analysis
- [ ] LLM analysis is saved to `analysis.json` with full tracing
- [ ] User sees detected columns and confirms before scraping begins
- [ ] LLM generates a tailored `scraper.py` with correct selectors
- [ ] CSV is written incrementally (row by row or page by page)
- [ ] Progress is checkpointed to `progress.json` after each page
- [ ] SIGINT/SIGTERM handlers ensure clean shutdown and data preservation
- [ ] All actions are logged with timestamps to console and `scraper.log`
- [ ] Resume support: detects prior progress and offers to continue
- [ ] Graceful handling of JS-only sites, missing pagination, empty results
- [ ] Polite scraping: delays between requests, proper User-Agent
