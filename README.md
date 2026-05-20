# LLM-Powered Dynamic Web Scraper

An intelligent web scraping system that uses Claude AI to automatically analyze website structure, generate tailored scraping code, and extract data into CSV format — **no HTML selectors required**.

## Features

✨ **Zero-Config Scraping** — Just provide a URL. The system analyzes the page, identifies repeating data patterns, and generates scraping code automatically.

🤖 **LLM-Powered Analysis** — Uses Claude to understand page structure, detect columns, and identify pagination mechanisms.

💾 **Incremental CSV Output** — Data is written to CSV as it's scraped. If interrupted, progress is saved and can be resumed.

📊 **Detailed Logging** — Every action is logged with timestamps. Full traces available for debugging.

⏸️ **Interruption-Safe** — Handle `Ctrl+C` gracefully. Resume scraping from where you left off.

🎯 **User-Friendly** — Optional page/row limits. Sensible defaults (10 pages max). Clear console output at every step.

---

## Quick Start

### Prerequisites

- Python 3.8+
- An Anthropic API key ([get one here](https://console.anthropic.com))

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/llm-web-scraper.git
   cd scraping-agent
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set your API key:**
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```
   
   Or create a `.env.local` file:
   ```
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   ```

### Usage

```bash
python run.py
```

You'll be prompted for:
1. **URL** to scrape (required)
2. **Page/row limit** (optional; defaults to 10 pages)

The system will:
- Fetch and analyze the page
- Show you detected columns
- Ask for confirmation
- Scrape the data
- Save results to `output.csv`

### Example

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

## How It Works

### Step-by-Step

1. **HTML Fetch & Clean** — Fetches the page, removes scripts/styles/comments, and reduces token usage.

2. **LLM Analysis** — Sends cleaned HTML to Claude (Haiku), which identifies:
   - Page structure and type
   - Repeating data items
   - Extractable columns/fields
   - CSS selectors for each field
   - Pagination mechanism

3. **User Confirmation** — Shows you what was detected. You can exclude columns if needed.

4. **Code Generation** — Claude (Sonnet) generates a tailored Python script that:
   - Uses `requests` + `BeautifulSoup`
   - Implements the pagination strategy
   - Respects your page/row limits
   - Writes incrementally to CSV
   - Includes polite delays and error handling

5. **Execution** — Runs the generated scraper, streams progress to console, and saves results.

---

## Project Structure

```
llm-web-scraper/
├── run.py              # Main orchestrator (entry point)
├── requirements.txt    # Python dependencies
├── .env.local          # Your API key (DO NOT COMMIT)
├── config.json         # User inputs (persisted for restarts)
├── analysis.json       # Full LLM page analysis output
├── scraper.py          # LLM-generated scraping script
├── scraper.log         # Detailed execution log
├── output.csv          # Scraped data (incremental writes)
├── progress.json       # Current page/row progress
└── README.md           # This file
```

---

## Features in Depth

### Interruption Handling

If you hit `Ctrl+C` during scraping:
- Current page is flushed to CSV
- Progress is saved to `progress.json`
- You can re-run `python run.py` and resume from where you left off

No data is lost.

### Logging

All actions are logged to both console and `scraper.log` with timestamps:
- HTTP requests/responses
- LLM API calls
- Page scrapes
- Retries and errors
- Graceful shutdowns

Useful for debugging and auditing.

### Error Handling

- **Network errors**: Retries with exponential backoff (429, 503). Logs and skips on 404.
- **Missing fields**: Writes empty strings instead of crashing.
- **Large pages**: Intelligently truncates HTML to fit token limits while preserving structure.
- **No pagination**: Scrapes single page and notes in logs.
- **JavaScript-only sites**: Detects and warns; suggests alternatives.

### Cost Management

Uses **Haiku** for analysis (~$0.002 per 25K tokens) and **Sonnet** for code generation (~$0.075). Typical run costs **$0.10–$0.20**.

To reduce costs:
- Lower the HTML character limit in `run.py`
- Use smaller pages or fewer pagination pages
- Reuse analysis for the same site

---

## Requirements

### Python Packages

```
requests>=2.31.0
beautifulsoup4>=4.12.0
anthropic>=0.25.0
python-dotenv>=1.0.0
```

Install all at once:
```bash
pip install -r requirements.txt
```

### Anthropic API Key

Get one from [console.anthropic.com](https://console.anthropic.com). Free tier includes token limits; paid accounts have higher limits.

---

## Configuration

### Environment Variables

Set in `.env.local` (never commit this file):

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### User Inputs

Provided interactively when you run `python run.py`:
- **URL** — Required. The website to scrape.
- **Page limit** — Optional. How many pages to scrape (default: 10).
- **Row limit** — Optional. Target number of rows (ignores page limit if set).

These are saved to `config.json` for reference.

---

## Limitations

- **JavaScript-rendered content**: This tool uses `requests` + `BeautifulSoup`, so it only sees HTML returned by the server. If a site loads content via JavaScript, it won't be scraped. (Use Playwright/Selenium if you need JS rendering.)
- **Rate limiting**: The scraper includes polite delays (1–2 seconds between requests). Some sites may still block aggressive scraping. Check `robots.txt` and the site's Terms of Service.
- **Dynamic URLs**: If pagination relies on client-side state or session tokens, it may not work.
- **Login-protected content**: The scraper doesn't handle authentication. You'd need to pre-set cookies or handle login manually.

---

## Troubleshooting

### "ANTHROPIC_API_KEY not set"
Make sure your API key is exported or in `.env.local`:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python run.py
```

### "No repeating data found"
The LLM couldn't detect structured data on the page. This might mean:
- The page is mostly static/unstructured text.
- The page requires JavaScript to render content.
- Try a different page or URL.

### "403 Forbidden" or "429 Too Many Requests"
The site is blocking your requests. Try:
- Increasing the delay between requests in `scraper.py`.
- Checking the site's `robots.txt` and Terms of Service.
- Using a different URL or tool.

### Scraper hangs or crashes
Check `scraper.log` for details. Common causes:
- Slow network connection (increase timeouts in `scraper.py`).
- Site is down or blocking requests.
- Missing or malformed selectors (check `analysis.json`).

---

## Development

### Adding Features

The system is modular:
- **`run.py`** — Orchestrator logic (fetching, LLM calls, user prompts)
- **`scraper.py`** — Generated on-the-fly; safe to edit for manual tweaks
- **Logs & progress** — All stored as JSON or text files

To modify behavior, edit `run.py` or the LLM prompts used for analysis/code generation.

### Running Tests

Not included yet, but you can test manually:
```bash
python run.py
# Enter: https://example.com
# Press Ctrl+C after a page or two
# Re-run and test resume functionality
```

---

## Cost Estimate

| Component | Tokens | Cost |
|---|---|---|
| HTML analysis (Haiku) | ~25K | ~$0.002 |
| Code generation (Sonnet) | ~2K | ~$0.06 |
| **Total per run** | ~27K | **~$0.08** |

Prices based on Anthropic's current rates. Exact costs depend on page size and LLM output length.

To cut costs: Use smaller HTML samples, fewer pages, or Haiku for both steps.

---

## Legal & Ethics

⚠️ **Respect robots.txt and Terms of Service.** Always check if a site allows scraping. Some sites prohibit it.

This tool is for educational and authorized scraping only. The user is responsible for:
- Ensuring they have permission to scrape the target site
- Not overloading servers with requests
- Respecting copyright and data privacy laws
- Complying with the site's Terms of Service

---

## Contributing

Contributions are welcome! Areas for improvement:
- JavaScript rendering support (Playwright integration)
- Authentication handling (cookies, login flows)
- More robust pagination detection
- Performance optimizations

Please open an issue or submit a pull request.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

## Support

- **Issues**: Open a GitHub issue if you encounter bugs or have questions.
- **Docs**: See the [spec](SPEC.md) for detailed architecture.
- **API**: [Anthropic API docs](https://docs.anthropic.com)

---

## Acknowledgments

Built with:
- [Anthropic Claude API](https://www.anthropic.com)
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/)
- [Requests](https://requests.readthedocs.io/)

---

**Happy scraping!** 🚀
