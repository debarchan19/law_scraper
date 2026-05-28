# Indian Kanoon — Judgment URL Collector

## Goal

Collect the PDF URL of every judgment published on indiankanoon.org across all 60 courts, all years.
Output: a flat file (`pdf_urls.txt`) with one URL per line and full metadata in `urls.jsonl`.
No PDFs are downloaded in this phase — URLs only.

---

## Current State (as of 2026-05-28)

| Metric | Value |
|---|---|
| Total courts on site | 60 |
| Total months to process | ~28,944 |
| Months checkpointed (done) | ~11,649 |
| URLs collected so far | ~168,226 |
| Data files location | `judgement_count/` |

The script was stopped mid-run on a MacBook and will be **resumed on a RunPod GPU/CPU server**.
All output files must be transferred via SCP before resuming (see below).

---

## Architecture

### Entry point
```
count_judgements.py
```

### Two-phase design
**Phase 1 — Navigation** (~1–2 min):
Fetches `/browse/` → all 60 court pages → all year pages in parallel.
Builds the full list of `(court, year, month, search_url)` tuples (~28,944 total).
Already-checkpointed months are filtered out automatically.

**Phase 2 — URL Collection** (hours/days):
All pending months processed in parallel (bounded by `--concurrency`).
Each month: paginates through search result pages, extracts `doc_id` + metadata.
Writes results to files atomically under `asyncio.Lock`.
Saves checkpoint after every month — safe to stop/restart at any time.

### Concurrency
- `--concurrency 20` = 20 parallel HTTP requests at all times
- Semaphore shared across Phase 1 + Phase 2
- Optional proxy rotation via spys.one (`--use-proxies`) — helps avoid 403s but spys.one free proxies have low liveness (~5–10%)

### Output files
```
judgement_count/
  pdf_urls.txt      ← main deliverable: one PDF URL per line
  urls.jsonl        ← full NDJSON metadata per judgment (large ~50MB+)
  checkpoint.json   ← list of completed month-search URLs (resume key)
  summary.json      ← counts by court / year / month + grand total
  run.log           ← stdout from last nohup run
```

### Resume mechanism
`checkpoint.json` stores every completed month's search URL. On restart, Phase 1 re-enumerates all months (fast), then Phase 2 skips any URL already in `checkpoint.json`.
**The checkpoint file must be present on the server for resume to work.**

---

## Package structure

```
law_scraper/
  count_judgements.py       ← URL collector (the active script)
  download_pdfs.py          ← PDF downloader (separate concern, not used here)
  indiankanoon/             ← shared crawler package
    config.py               ← BASE_URL, headers, timeouts
    parsers.py              ← browse_links, search_page_results, month_search_links
    http.py                 ← fetch_html with backoff
    crawler.py              ← crawl_producer (used by downloader)
    downloader.py           ← download workers (used by downloader)
    cli.py                  ← CLI for PDF downloader
    models.py, state.py
  proxy/                    ← proxy pool (optional, for spys.one rotation)
    pool.py                 ← ProxyPool class
    spys_scraper.py         ← Playwright scraper for spys.one
    validator.py            ← aiohttp liveness checker
    config.py               ← SPYS_ONE_URL, timeouts, watermarks
```

---

## RunPod Server Setup

### 1. Transfer files from MacBook → RunPod

Transfer the **code** and the **checkpoint + output files** so the run resumes seamlessly:

```bash
# From your MacBook — replace <RUNPOD_IP> and <PORT>
scp -P <PORT> -r \
  ~/workspace/repos/law_scraper \
  root@<RUNPOD_IP>:/workspace/law_scraper
```

Or if you want to transfer only the checkpoint (code via git clone):
```bash
# Clone code on server first, then push just the data
scp -P <PORT> \
  ~/workspace/repos/law_scraper/judgement_count/checkpoint.json \
  ~/workspace/repos/law_scraper/judgement_count/pdf_urls.txt \
  ~/workspace/repos/law_scraper/judgement_count/urls.jsonl \
  ~/workspace/repos/law_scraper/judgement_count/summary.json \
  root@<RUNPOD_IP>:/workspace/law_scraper/judgement_count/
```

### 2. Set up Python environment on server

```bash
cd /workspace/law_scraper
pip install uv
uv venv .venv
uv pip install aiohttp aiofiles beautifulsoup4 uvloop playwright
.venv/bin/playwright install chromium   # only needed if using --use-proxies
```

### 3. Resume the run

```bash
cd /workspace/law_scraper
nohup .venv/bin/python -u count_judgements.py --concurrency 20 \
  > judgement_count/run.log 2>&1 &
echo $!
```

To monitor progress:
```bash
tail -f judgement_count/run.log
wc -l judgement_count/pdf_urls.txt
```

### 4. Optional: use proxies (reduces 403 rate limiting)

```bash
nohup .venv/bin/python -u count_judgements.py --use-proxies --concurrency 20 \
  > judgement_count/run.log 2>&1 &
```

Note: spys.one free proxies have low liveness (~5–10%). The crawl works without them but may get more 403 retries. Both modes use exponential backoff and handle 403/429 gracefully.

---

## Key CLI flags

```
--out          Output directory (default: judgement_count)
--concurrency  Max parallel HTTP requests (default: 20)
--use-proxies  Rotate IPs via spys.one
--courts       Restrict to specific courts (e.g. --courts supremecourt allahabad)
--years        Restrict to specific years (e.g. --years 2023 2024)
```

---

## Estimated remaining runtime

At concurrency 20 without proxies on a fast server:
- ~27,000 remaining months × avg ~3 pages/month × 0.3s/page = ~22 hours pure fetch time
- Add 403 retry overhead: estimate **1–3 days** total to complete all 60 courts
- Final URL count estimate: **2–4 million** judgments across all courts

---

## What to do after collection is complete

1. Verify: `wc -l judgement_count/pdf_urls.txt` — should be 2M+
2. Deduplicate: `sort -u judgement_count/pdf_urls.txt > judgement_count/pdf_urls_dedup.txt`
3. Download PDFs: use `download_pdfs.py` with the collected URLs (separate task)
4. Transfer `pdf_urls.txt` back to local or object storage (S3/GCS)
