---
name: run-law-scraper
description: run, start, build, test, smoke, screenshot, drive, download, crawl, verify the law_scraper / indiankanoon PDF downloader CLI
---

# run-law-scraper

Async crawler + PDF downloader for [Indian Kanoon](https://indiankanoon.org).
Entry point: `download_pdfs.py`. Driven via `smoke.sh` (the agent path) or
invoked directly for real downloads. No browser, no GUI — pure CLI.

Paths below are relative to the repo root (`law_scraper/`).

---

## Prerequisites

Python 3.12 venv managed by `uv`. All dependencies already installed.
If starting fresh on a new machine:

```bash
uv venv .venv
uv pip install aiohttp aiofiles beautifulsoup4 uvloop playwright
.venv/bin/playwright install chromium
```

---

## Build

No compilation step. The package is plain Python — import directly from `indiankanoon/`.

---

## Run: agent path (smoke driver)

Use `smoke.sh` to launch, let it run, and verify output structure without
blocking forever.

```bash
SMOKE_TIMEOUT=25 bash .claude/skills/run-law-scraper/smoke.sh /tmp/ik_smoke_test
```

What the script does:
1. Launches `download_pdfs.py --courts supremecourt --years 2024` in background.
2. Sends SIGINT after `SMOKE_TIMEOUT` seconds — the crawler drains in-flight work then exits cleanly (it does **not** crash on SIGINT).
3. Asserts: at least one PDF was downloaded, `checkpoint.json` was written, and every PDF path matches the `court/year/month/date/` hierarchy.
4. Prints sample paths and exits 0 on pass, 1 on fail.

Expected output:
```
=== law_scraper smoke test ===
Output dir : /tmp/ik_smoke_test
Crawl court: supremecourt, year 2024 (January only)

Found 60 courts
=== Court: Supreme Court of India (supremecourt) ===
  Found 77 year links
  Year 2024: 12 months
Interrupt — draining in-flight work...
    January: 70 judgments queued
  progress: 70/70  downloaded=70  skipped=0  failed=0
...
=== Results ===
PDFs downloaded : 70
Misnamed files  : 0
Sample paths:
/tmp/ik_smoke_test/supremecourt/2024/January/08_Jan_2024/supremecourt_2024_January_08_Jan_2024_47574125.pdf
...
PASS
```

---

## Run: direct invocation (real downloads)

```bash
# One court, one year — sensible starting point
.venv/bin/python download_pdfs.py \
  --courts supremecourt \
  --years 2024 \
  --out pdfs

# Multiple courts, specific year
.venv/bin/python download_pdfs.py \
  --courts supremecourt delhi \
  --years 2023 2024 \
  --crawl-concurrency 4 \
  --download-concurrency 8 \
  --out pdfs

# With free proxies (scrapes spys.one on startup — adds ~60s before first download)
.venv/bin/python download_pdfs.py \
  --courts supremecourt \
  --years 2024 \
  --use-proxies \
  --out pdfs

# All courts, all years (very long — use only with --courts/--years filters in practice)
.venv/bin/python download_pdfs.py --out pdfs
```

Output structure:
```
pdfs/
  <court>/
    <year>/
      <month>/
        <DD_Mon_YYYY>/
          <court>_<year>_<month>_<DD_Mon_YYYY>_<doc_id>.pdf
```

Example: `pdfs/supremecourt/2024/January/08_Jan_2024/supremecourt_2024_January_08_Jan_2024_47574125.pdf`

---

## Resume / checkpoint

The crawler is safe to interrupt with Ctrl-C at any time. It writes
`<out>/checkpoint.json` after completing each month. Re-running the
same command skips already-completed months automatically.

Already-downloaded PDFs are also skipped (checked by file existence + size > 0).

---

## Test: proxy pool in isolation

```bash
# Scrape and validate proxies from spys.one (takes ~2–3 min)
.venv/bin/python - <<'EOF'
import asyncio
from proxy import scrape_proxies, validate_batch

async def main():
    raw = await scrape_proxies()
    live = await validate_batch(raw)
    print(f"live proxies: {len(live)}")
    for p in live[:5]:
        print(" ", p)

asyncio.run(main())
EOF
```

---

## Gotchas

- **Date in title, not in a separate tag.** indiankanoon encodes the judgment date inside the result-title link text: `"Case Title on 8 January, 2024"`. The parser (`indiankanoon/parsers.py:_extract_date_slug`) checks the `result_title` element first, then falls back to `<cite>` / `<span>` / `<div>`. Earlier versions that only checked `<cite>` produced `unknown_date` for every file.

- **SIGINT is graceful, SIGKILL is not.** Sending `kill -INT` lets the crawler finish in-flight downloads before exiting. `kill -9` / `kill -KILL` leaves `.part` files and an incomplete checkpoint. The smoke script always uses `kill -INT`.

- **`--use-proxies` startup cost.** When `--use-proxies` is passed, the proxy pool scrapes spys.one (Playwright, ~30s) then liveness-checks all proxies concurrently (~60s). The first download won't start until that's done. This is normal.

- **spys.one JS-obfuscated ports.** The proxy scraper uses Playwright (headless Chromium) to render the page so the JS port-deobfuscation runs before extraction. Plain `requests` returns ports as empty strings.

- **60 courts total.** `indiankanoon/browse/` currently lists 60 courts. Running without `--courts` queues all of them; combined with all years this is tens of thousands of PDFs. Always scope with `--courts` and `--years` unless you mean to run overnight.

---

## Troubleshooting

**`No module named 'uvloop'`** — `uv pip install uvloop`

**`No module named 'aiofiles'`** — `uv pip install aiofiles`

**`playwright._impl._errors.Error: Executable doesn't exist`** — `.venv/bin/playwright install chromium`

**All files land in `unknown_date/`** — the date regex didn't match. Check the actual HTML: fetch a search page and print `article.prettify()` to see where the date lives. As of 2024, it's in the `result_title` `<a>` text.

**`0 judgments queued` for a month** — that month has no search results (court may not have sittings every month). Normal for smaller courts.

**`failed=N` in progress counter** — check `<out>/failures.json` for per-doc reasons. Common causes: 403 (rate-limited), non-PDF content (session expired), 404 (doc removed).
