#!/usr/bin/env python3
"""
LLM-Powered Dynamic Web Scraper
Entry point: python run.py
"""

import os
import sys
import json
import re
import signal
import logging
import subprocess
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup, Comment
import anthropic
from dotenv import load_dotenv

load_dotenv()

# ── File paths ────────────────────────────────────────────────────────────────
CONFIG_FILE = "config.json"
ANALYSIS_FILE = "analysis.json"
SCRAPER_FILE = "scraper.py"
OUTPUT_FILE = "output.csv"
LOG_FILE = "scraper.log"
PROGRESS_FILE = "progress.json"

# ── LLM config ────────────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"
MAX_TOKENS_ANALYSIS = 4096
MAX_TOKENS_CODEGEN = 8192

# Approximate cost per token (Sonnet 4 input ~$3/M)
COST_PER_TOKEN = 3e-6

HTML_MAX_CHARS = 80_000

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ── Logging ───────────────────────────────────────────────────────────────────
def setup_logging() -> None:
    fmt = "[%(levelname)s] %(asctime)s — %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
        ],
    )


logger = logging.getLogger(__name__)

# ── Display helpers ───────────────────────────────────────────────────────────
def banner() -> None:
    print("\n" + "═" * 45)
    print("  LLM Web Scraper")
    print("═" * 45 + "\n")


def section(title: str) -> None:
    print(f"\n{'─' * 45}")
    print(f"  {title}")
    print("─" * 45)


# ── Persistence helpers ───────────────────────────────────────────────────────
def load_json(path: str) -> Optional[dict]:
    p = Path(path)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Token / cost estimation ───────────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    return len(text) // 4


# ── HTML helpers ──────────────────────────────────────────────────────────────
def clean_html(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "link"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()
    text = soup.prettify()
    text = re.sub(r"\n\s*\n", "\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text


def truncate_html(html: str) -> str:
    if len(html) <= HTML_MAX_CHARS:
        return html
    half = HTML_MAX_CHARS // 2
    logger.warning(
        "HTML too large (%s chars). Truncating to ~%s chars.",
        f"{len(html):,}",
        f"{HTML_MAX_CHARS:,}",
    )
    return html[:half] + "\n\n... [TRUNCATED — middle section omitted] ...\n\n" + html[-half:]


# ── Step 1 — User input ───────────────────────────────────────────────────────
def get_user_inputs() -> dict:
    url = input("Enter the URL to scrape: ").strip()
    if not url:
        print("URL is required.")
        sys.exit(1)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    raw_limit = input(
        "How many pages to scrape? (default: 10, press Enter to use default): "
    ).strip()
    page_limit = int(raw_limit) if raw_limit.isdigit() else 10

    config = {"url": url, "page_limit": page_limit}
    save_json(CONFIG_FILE, config)
    logger.info("URL received: %s | Page limit: %d", url, page_limit)
    return config


# ── Step 2 — Fetch & analyse ──────────────────────────────────────────────────
def fetch_html(url: str) -> str:
    logger.info("Fetching %s ...", url)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.error("Failed to fetch %s: %s", url, exc)
        sys.exit(1)

    logger.info(
        "Received %s bytes (status %d)",
        f"{len(resp.content):,}",
        resp.status_code,
    )

    if resp.status_code != 200:
        logger.error("HTTP %d for %s", resp.status_code, url)
        sys.exit(1)

    return resp.text


ANALYSIS_SCHEMA = """{
  "page_type": "string",
  "description": "string",
  "items": {
    "container_selector": "string",
    "item_selector": "string",
    "fields": [
      {
        "name": "string",
        "selector": "string",
        "attribute": "string or null",
        "description": "string"
      }
    ],
    "sample_count": 0
  },
  "pagination": {
    "type": "next_button | page_numbers | url_pattern | api | none",
    "selector_or_pattern": "string or null",
    "notes": "string"
  }
}"""

ANALYSIS_PROMPT_PREFIX = (
    "You are an expert web scraper analyst. Analyze the following HTML and return "
    "ONLY valid JSON — no markdown fences, no preamble, no explanation.\n\n"
    "Identify:\n"
    "1. The type of page (listing, table, directory, search results, article index, etc.)\n"
    "2. All repeating data items (product cards, job listings, table rows, etc.)\n"
    "3. For each item: the columns/fields extractable (title, price, link, date, etc.)\n"
    "4. CSS selectors for the container, each item, and each field\n"
    "5. The pagination mechanism (next button, page numbers, url pattern, none)\n\n"
    "Return ONLY this JSON structure (no extra text):\n"
    + ANALYSIS_SCHEMA
    + "\n\nHTML to analyze:\n"
)


def analyze_with_llm(html: str, client: anthropic.Anthropic) -> dict:
    cleaned = clean_html(html)
    cleaned = truncate_html(cleaned)

    prompt = ANALYSIS_PROMPT_PREFIX + cleaned
    token_est = estimate_tokens(prompt)
    cost_est = token_est * COST_PER_TOKEN

    print(
        f"\nThis will use ~{token_est:,} tokens (~${cost_est:.4f}). Proceed? [Y/n]: ",
        end="",
        flush=True,
    )
    if input().strip().lower() == "n":
        print("Aborted.")
        sys.exit(0)

    logger.info("Sending HTML to LLM for analysis (~%s tokens)...", f"{token_est:,}")

    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_ANALYSIS,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # Strip accidental markdown fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    try:
        analysis = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("LLM returned invalid JSON: %s\nRaw (first 500 chars): %s", exc, raw[:500])
        sys.exit(1)

    save_json(ANALYSIS_FILE, analysis)
    logger.info(
        "Analysis saved to %s | Page type: %s | Items: %s",
        ANALYSIS_FILE,
        analysis.get("page_type", "?"),
        analysis.get("items", {}).get("sample_count", "?"),
    )
    return analysis


# ── Step 3 — Display analysis & confirm ───────────────────────────────────────
def display_and_confirm(analysis: dict) -> dict:
    page_type = analysis.get("page_type", "Unknown")
    description = analysis.get("description", "")
    items = analysis.get("items", {})
    fields = items.get("fields", [])
    sample_count = items.get("sample_count", 0)
    pagination = analysis.get("pagination", {})

    section("Analysis Results")
    print(f"  Page type     : {page_type}")
    if description:
        print(f"  Description   : {description}")
    print(f"  Items on page : {sample_count}")
    print(
        f"  Pagination    : {pagination.get('type', 'none')} — {pagination.get('notes', '')}"
    )
    print("\n  Columns identified:")
    for i, field in enumerate(fields, 1):
        print(f"    {i:2}. {field['name']:<22} — {field['description']}")
    print()

    if not fields:
        logger.error("No repeating data found. Cannot generate a scraper.")
        sys.exit(1)

    exclude_raw = input(
        "Enter column numbers to EXCLUDE (comma-separated) or press Enter to keep all: "
    ).strip()

    if exclude_raw:
        exclude_idx = {
            int(x.strip()) - 1
            for x in exclude_raw.split(",")
            if x.strip().isdigit()
        }
        kept = [f for i, f in enumerate(fields) if i not in exclude_idx]
        analysis["items"]["fields"] = kept
        print(f"  Keeping: {', '.join(f['name'] for f in kept)}")

    confirm = input("\nProceed with scraping? [Y/n]: ").strip().lower()
    if confirm == "n":
        print("Aborted.")
        sys.exit(0)

    return analysis


# ── Step 4 — Generate scraper ─────────────────────────────────────────────────
CODEGEN_PROMPT_TEMPLATE = """You are an expert Python developer. Generate a complete, self-contained Python web scraping script.

Base URL: {base_url}
Page limit: {page_limit}

Page analysis:
{analysis_json}

Requirements — follow ALL of these exactly:

1. Use requests + BeautifulSoup (bs4) only. No Playwright/Selenium.
2. Implement pagination using the strategy in the analysis JSON.
3. Stop after {page_limit} pages (or fewer if no more pages exist).
4. Write results to "output.csv" in APPEND mode (open with 'a', newline='', encoding='utf-8-sig').
   - Write the CSV header only if the file doesn't exist or is empty.
   - Flush the writer after each page.
5. Track seen rows using hashlib.md5 of all field values joined. Skip and log duplicates.
6. Write empty string for any field whose selector finds no match (never raise on missing).
7. Progress checkpointing: after each page, write progress.json:
   {{"last_page_completed": N, "total_rows": M}}
8. On startup: if progress.json exists, resume from last_page_completed + 1.
9. Register signal.SIGINT and signal.SIGTERM handlers that:
   - Flush and close the CSV.
   - Update progress.json.
   - Log a clean shutdown message.
   - Call sys.exit(0).
10. Polite delay: time.sleep(random.uniform(1.0, 2.0)) between page requests.
11. HTTP error handling:
    - 429 or 503: retry up to 3 times with exponential backoff (2^attempt seconds).
    - 404: log and skip.
    - Other non-200: log and skip.
12. User-Agent header: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
13. Logging: use Python logging module with both StreamHandler (console) and FileHandler ("scraper.log").
    Log: page number, URL fetched, status code, items found per page, running total rows, duplicate skips.
14. Resolve all relative URLs against the base URL using urllib.parse.urljoin.

Return ONLY raw Python code. No markdown fences. No explanation. Start with #!/usr/bin/env python3
"""


def generate_scraper(analysis: dict, config: dict, client: anthropic.Anthropic) -> None:
    logger.info("Generating scraper code...")

    prompt = CODEGEN_PROMPT_TEMPLATE.format(
        base_url=config.get("url", ""),
        page_limit=config.get("page_limit", 10),
        analysis_json=json.dumps(analysis, indent=2),
    )

    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_CODEGEN,
        messages=[{"role": "user", "content": prompt}],
    )

    code = message.content[0].text.strip()
    code = re.sub(r"^```python\n?", "", code)
    code = re.sub(r"^```\n?", "", code)
    code = re.sub(r"\n?```$", "", code)

    with open(SCRAPER_FILE, "w", encoding="utf-8") as f:
        f.write(code)

    logger.info("Scraper saved to %s", SCRAPER_FILE)


# ── Step 5 — Execute & collect ────────────────────────────────────────────────
def run_scraper(config: dict) -> None:
    page_limit = config.get("page_limit", 10)
    logger.info("Starting scraper (target: %d pages)...", page_limit)

    proc = subprocess.Popen(
        [sys.executable, SCRAPER_FILE],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        for line in proc.stdout:
            print(line, end="", flush=True)
        proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        proc.wait()

    progress = load_json(PROGRESS_FILE)
    rows = progress.get("total_rows", "?") if progress else "?"
    pages = progress.get("last_page_completed", "?") if progress else "?"

    print("\n" + "═" * 45)
    print(f"  Total rows scraped : {rows}")
    print(f"  Pages processed    : {pages}")
    print(f"  Output CSV         : {OUTPUT_FILE}")
    print(f"  Log file           : {LOG_FILE}")
    print("═" * 45 + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    setup_logging()
    banner()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Resume detection
    progress = load_json(PROGRESS_FILE)
    if progress:
        config = load_json(CONFIG_FILE) or {}
        rows = progress.get("total_rows", 0)
        pages = progress.get("last_page_completed", 0)
        limit = config.get("page_limit", 10)
        print(f"Previous progress detected ({rows} rows, {pages}/{limit} pages).")
        resume = input("Resume? [Y/n]: ").strip().lower()
        if resume != "n":
            run_scraper(config)
            return
        # Start fresh — clear progress
        Path(PROGRESS_FILE).unlink(missing_ok=True)

    # ── Fresh run ────────────────────────────────────────────────────────────
    config = get_user_inputs()
    html = fetch_html(config["url"])

    # JS-only site check
    body_text = BeautifulSoup(html, "html.parser").get_text(strip=True)
    if len(body_text) < 500:
        logger.warning(
            "Very little text content detected (%d chars). "
            "This site likely requires JavaScript rendering. "
            "requests-based scraping may not capture meaningful data.",
            len(body_text),
        )
        if input("Proceed anyway? [y/N]: ").strip().lower() != "y":
            sys.exit(0)

    analysis = analyze_with_llm(html, client)
    analysis = display_and_confirm(analysis)
    generate_scraper(analysis, config, client)
    run_scraper(config)


if __name__ == "__main__":
    main()
