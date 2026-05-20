#!/usr/bin/env python3

import csv
import hashlib
import json
import logging
import os
import random
import signal
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "http://openlibrary.org/trending/now"
PAGE_LIMIT = 10
OUTPUT_FILE = "output.csv"
PROGRESS_FILE = "progress.json"
LOG_FILE = "scraper.log"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

FIELDS = [
    "title",
    "title_link",
    "author",
    "author_link",
    "series_name",
    "series_position",
    "cover_image",
    "first_published",
    "editions_count",
    "activity_timestamp",
    "cta_button_text",
    "cta_button_link",
]

logger = logging.getLogger(__name__)

csv_file_handle = None
csv_writer = None
total_rows = 0
last_page_completed = 0
seen_hashes = set()


def setup_logging():
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)


def load_progress():
    global last_page_completed, total_rows
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                last_page_completed = data.get("last_page_completed", 0)
                total_rows = data.get("total_rows", 0)
                logger.info(
                    f"Resuming from page {last_page_completed + 1}, total rows so far: {total_rows}"
                )
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Could not load progress file: {e}. Starting fresh.")
            last_page_completed = 0
            total_rows = 0
    else:
        last_page_completed = 0
        total_rows = 0


def save_progress():
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"last_page_completed": last_page_completed, "total_rows": total_rows},
                f,
            )
        logger.debug(
            f"Progress saved: last_page_completed={last_page_completed}, total_rows={total_rows}"
        )
    except IOError as e:
        logger.error(f"Failed to save progress: {e}")


def open_csv():
    global csv_file_handle, csv_writer
    file_exists = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
    csv_file_handle = open(OUTPUT_FILE, "a", newline="", encoding="utf-8-sig")
    csv_writer = csv.DictWriter(csv_file_handle, fieldnames=FIELDS)
    if not file_exists:
        csv_writer.writeheader()
        csv_file_handle.flush()
        logger.info("Created new output CSV with headers.")
    else:
        logger.info("Appending to existing output CSV.")


def close_csv():
    global csv_file_handle
    if csv_file_handle:
        try:
            csv_file_handle.flush()
            csv_file_handle.close()
            logger.info("CSV file closed.")
        except IOError as e:
            logger.error(f"Error closing CSV: {e}")
        csv_file_handle = None


def shutdown_handler(signum, frame):
    logger.info(f"Received signal {signum}. Shutting down gracefully...")
    close_csv()
    save_progress()
    logger.info("Clean shutdown complete.")
    sys.exit(0)


def compute_hash(row):
    values = "".join(str(row.get(f, "")) for f in FIELDS)
    return hashlib.md5(values.encode("utf-8")).hexdigest()


def get_text(tag, selector):
    try:
        element = tag.select_one(selector)
        if element:
            return element.get_text(strip=True)
        return ""
    except Exception:
        return ""


def get_attr(tag, selector, attribute):
    try:
        element = tag.select_one(selector)
        if element and element.has_attr(attribute):
            return element[attribute]
        return ""
    except Exception:
        return ""


def resolve_url(relative_url):
    if not relative_url:
        return ""
    return urljoin(BASE_URL, relative_url)


def fetch_page(url, attempt=0):
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        status_code = response.status_code
        logger.info(f"Fetched URL: {url} | Status: {status_code}")

        if status_code == 200:
            return response
        elif status_code in (429, 503):
            if attempt < 3:
                wait = 2 ** attempt
                logger.warning(
                    f"Status {status_code} received. Retrying in {wait}s (attempt {attempt + 1}/3)..."
                )
                time.sleep(wait)
                return fetch_page(url, attempt + 1)
            else:
                logger.error(
                    f"Status {status_code} after 3 retries. Skipping URL: {url}"
                )
                return None
        elif status_code == 404:
            logger.error(f"404 Not Found: {url}. Skipping.")
            return None
        else:
            logger.error(f"Unexpected status {status_code} for URL: {url}. Skipping.")
            return None
    except requests.RequestException as e:
        if attempt < 3:
            wait = 2 ** attempt
            logger.warning(
                f"Request error: {e}. Retrying in {wait}s (attempt {attempt + 1}/3)..."
            )
            time.sleep(wait)
            return fetch_page(url, attempt + 1)
        else:
            logger.error(f"Request failed after 3 retries for URL: {url}. Error: {e}")
            return None


def parse_items(soup, page_num):
    global total_rows
    items_found = 0
    duplicates_skipped = 0

    container = soup.select_one("ul.list-books")
    if not container:
        logger.warning(f"Page {page_num}: No item container found (ul.list-books).")
        return items_found, duplicates_skipped

    items = container.select("li.searchResultItem")
    logger.info(f"Page {page_num}: Found {len(items)} items.")

    for item in items:
        title = get_text(item, "h3.booktitle a.results")
        title_link_raw = get_attr(item, "h3.booktitle a.results", "href")
        title_link = resolve_url(title_link_raw)

        author = get_text(item, "span.bookauthor a")
        author_link_raw = get_attr(item, "span.bookauthor a", "href")
        author_link = resolve_url(author_link_raw)

        series_name = get_text(item, "span.bookseries a")
        series_position = get_text(item, "span.bookseries__position")

        cover_image_raw = get_attr(item, "span.bookcover img", "src")
        cover_image = resolve_url(cover_image_raw)

        first_published = get_text(item, "span.resultDetails span:first-child")
        editions_count = get_text(item, "span.resultDetails span a:first-of-type")

        activity_timestamp = get_text(item, "div.details")

        cta_button_text = get_text(item, "div.searchResultItemCTA .cta-btn")
        cta_button_link_raw = get_attr(item, "div.searchResultItemCTA .cta-btn", "href")
        cta_button_link = resolve_url(cta_button_link_raw)

        row = {
            "title": title,
            "title_link": title_link,
            "author": author,
            "author_link": author_link,
            "series_name": series_name,
            "series_position": series_position,
            "cover_image": cover_image,
            "first_published": first_published,
            "editions_count": editions_count,
            "activity_timestamp": activity_timestamp,
            "cta_button_text": cta_button_text,
            "cta_button_link": cta_button_link,
        }

        row_hash = compute_hash(row)
        if row_hash in seen_hashes:
            logger.debug(f"Duplicate row skipped: {title}")
            duplicates_skipped += 1
            continue

        seen_hashes.add(row_hash)
        csv_writer.writerow(row)
        total_rows += 1
        items_found += 1

    if items_found > 0 or duplicates_skipped > 0:
        csv_file_handle.flush()

    logger.info(
        f"Page {page_num}: Wrote {items_found} new rows | Duplicates skipped: {duplicates_skipped} | Running total: {total_rows}"
    )
    return items_found, duplicates_skipped


def build_page_url(page_num):
    if page_num == 1:
        return BASE_URL
    return f"{BASE_URL}?page={page_num}"


def main():
    global last_page_completed, total_rows

    setup_logging()
    logger.info("Starting Open Library trending books scraper.")

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    load_progress()
    open_csv()

    start_page = last_page_completed + 1

    if start_page > PAGE_LIMIT:
        logger.info("All pages already completed. Nothing to do.")
        close_csv()
        return

    for page_num in range(start_page, PAGE_LIMIT + 1):
        url = build_page_url(page_num)
        logger.info(f"Processing page {page_num}/{PAGE_LIMIT}: {url}")

        response = fetch_page(url)

        if response is None:
            logger.warning(f"Skipping page {page_num} due to fetch failure.")
            last_page_completed = page_num
            save_progress()
            if page_num < PAGE_LIMIT:
                sleep_time = random.uniform(1.0, 2.0)
                logger.debug(f"Sleeping {sleep_time:.2f}s before next page.")
                time.sleep(sleep_time)
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        items_found, duplicates_skipped = parse_items(soup, page_num)

        last_page_completed = page_num
        save_progress()

        if page_num < PAGE_LIMIT:
            sleep_time = random.uniform(1.0, 2.0)
            logger.debug(f"Sleeping {sleep_time:.2f}s before next page.")
            time.sleep(sleep_time)

    logger.info(f"Scraping complete. Total rows written: {total_rows}")
    close_csv()
    save_progress()


if __name__ == "__main__":
    main()