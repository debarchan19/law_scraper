"""
count_judgements.py — parallel URL collector for indiankanoon.org

Two-phase design:
  Phase 1 — navigation: fetches /browse/ → courts → year pages in parallel
             to enumerate every (court, year, month, search-url) tuple.
             ~3 000 HTTP calls; completes in ~1 minute at concurrency=20.
  Phase 2 — collection: fetches all month search-result pages in parallel;
             up to --concurrency requests run simultaneously, each on a
             different proxy when --use-proxies is given.

Every HTTP request (navigation AND result pages) can optionally rotate
through the spys.one proxy pool (--use-proxies).

Output (--out directory):
    pdf_urls.txt    — one PDF URL per line  (main deliverable)
    urls.jsonl      — NDJSON with full metadata per judgment
    summary.json    — counts by court / year / month + grand total
    checkpoint.json — completed month-search URLs  (resume support)

Resume: just re-run the same command; Phase 1 re-enumerates quickly and
Phase 2 skips all months already in checkpoint.json.

Usage:
    python count_judgements.py --use-proxies --concurrency 20
    python count_judgements.py --courts supremecourt --years 2024
    wc -l judgement_count/pdf_urls.txt          # count collected so far
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from bs4 import BeautifulSoup
import uvloop

from indiankanoon.config import BASE_URL, BROWSE_URL, HTML_HEADERS
from indiankanoon.parsers import (
    browse_links,
    month_search_links,
    next_pagenum,
    search_page_results,
    search_url_for_page,
)

try:
    from proxy import ProxyPool
except ImportError:
    ProxyPool = None  # type: ignore[assignment,misc]

# ── Tuning ────────────────────────────────────────────────────────────────────
_INITIAL_BACKOFF = 2.0
_BACKOFF_FACTOR  = 2.0
_MAX_BACKOFF     = 300.0
_JITTER          = 0.25
_MAX_RETRIES     = 8
_REQUEST_TIMEOUT = 60
_POLITE_DELAY    = 0.3   # seconds of courtesy pause after each successful fetch


@dataclass
class _PendingMonth:
    court:     str
    year:      str
    month:     str
    month_url: str


async def _jitter_sleep(seconds: float) -> None:
    seconds = min(seconds, _MAX_BACKOFF)
    j = seconds * _JITTER
    await asyncio.sleep(max(0.0, random.uniform(seconds - j, seconds + j)))


# ── Proxy-aware HTML fetch ────────────────────────────────────────────────────

async def _get_soup(
    session:    aiohttp.ClientSession,
    url:        str,
    sem:        asyncio.Semaphore,
    proxy_pool: "ProxyPool | None" = None,
) -> BeautifulSoup | None:
    """
    Fetch *url* with exponential backoff.  When a proxy pool is provided every
    retry acquires a fresh proxy; bad proxies are discarded automatically.
    """
    backoff = _INITIAL_BACKOFF
    async with sem:
        for attempt in range(1, _MAX_RETRIES + 1):
            proxy_addr: str | None = None
            proxy_host: str | None = None

            if proxy_pool is not None:
                proxy_host = await proxy_pool._get()
                proxy_addr = f"http://{proxy_host}"

            try:
                async with session.get(
                    url,
                    headers=HTML_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
                    proxy=proxy_addr,
                ) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if proxy_pool and proxy_host:
                            proxy_pool._return(proxy_host)
                        await _jitter_sleep(_POLITE_DELAY)
                        return BeautifulSoup(text, "html.parser")

                    if resp.status == 404:
                        if proxy_pool and proxy_host:
                            proxy_pool._return(proxy_host)
                        return None

                    if proxy_pool and proxy_host:
                        proxy_pool._discard(proxy_host)

                    if resp.status in (403, 429) or 500 <= resp.status < 600:
                        wait = float(resp.headers.get("Retry-After") or backoff)
                        print(f"  [{resp.status}] retry {attempt}/{_MAX_RETRIES} in ~{wait:.0f}s  {url}")
                        await _jitter_sleep(wait)
                        backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)
                        continue

                    print(f"  [{resp.status}] giving up  {url}")
                    return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if proxy_pool and proxy_host:
                    proxy_pool._discard(proxy_host)
                print(f"  [error] {exc}  retry {attempt}/{_MAX_RETRIES} in ~{backoff:.0f}s")
                await _jitter_sleep(backoff)
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

    print(f"  [exhausted retries] {url}")
    return None


# ── Phase 1: enumerate months ─────────────────────────────────────────────────

async def _enum_year(
    session:    aiohttp.ClientSession,
    sem:        asyncio.Semaphore,
    proxy_pool: "ProxyPool | None",
    court_slug: str,
    year:       str,
    year_url:   str,
) -> list[_PendingMonth]:
    soup = await _get_soup(session, year_url, sem, proxy_pool)
    if soup is None:
        return []
    return [
        _PendingMonth(court=court_slug, year=year, month=month_name, month_url=month_url)
        for month_name, month_url in month_search_links(soup)
    ]


async def _enum_court(
    session:      aiohttp.ClientSession,
    sem:          asyncio.Semaphore,
    proxy_pool:   "ProxyPool | None",
    court_name:   str,
    court_url:    str,
    years_filter: set[int],
) -> list[_PendingMonth]:
    court_slug = court_url.rstrip("/").split("/")[-1]
    soup = await _get_soup(session, court_url, sem, proxy_pool)
    if soup is None:
        print(f"  [!] could not fetch court page: {court_name}")
        return []

    year_prefix = court_url.replace(BASE_URL, "").rstrip("/") + "/"
    year_links  = browse_links(soup, year_prefix)
    if not year_links:
        return []

    print(f"  {court_name} ({court_slug}): {len(year_links)} years")

    # Fetch all year pages for this court simultaneously
    year_tasks = []
    for year_text, year_url in year_links:
        ym = re.search(r"\b(19|20)\d{2}\b", year_text + year_url)
        if not ym:
            continue
        year = ym.group(0)
        if years_filter and int(year) not in years_filter:
            continue
        year_tasks.append(_enum_year(session, sem, proxy_pool, court_slug, year, year_url))

    results = await asyncio.gather(*year_tasks, return_exceptions=True)
    months: list[_PendingMonth] = []
    for r in results:
        if isinstance(r, list):
            months.extend(r)
    return months


async def _enumerate_all_months(
    session:       aiohttp.ClientSession,
    sem:           asyncio.Semaphore,
    proxy_pool:    "ProxyPool | None",
    courts_filter: set[str],
    years_filter:  set[int],
) -> list[_PendingMonth]:
    """Walk /browse/ → courts → years in parallel; return every month found."""
    soup = await _get_soup(session, BROWSE_URL, sem, proxy_pool)
    if soup is None:
        print("Could not fetch /browse/ — aborting")
        return []

    courts = browse_links(soup, "/browse/")
    print(f"Found {len(courts)} courts")
    print("Phase 1: enumerating all courts and years in parallel …\n")

    court_tasks = [
        _enum_court(session, sem, proxy_pool, court_name, court_url, years_filter)
        for court_name, court_url in courts
        if not courts_filter or court_url.rstrip("/").split("/")[-1] in courts_filter
    ]

    results = await asyncio.gather(*court_tasks, return_exceptions=True)
    all_months: list[_PendingMonth] = []
    for r in results:
        if isinstance(r, list):
            all_months.extend(r)
    return all_months


# ── Phase 2: collect URLs for one month ───────────────────────────────────────

async def _crawl_month_pages(
    session:    aiohttp.ClientSession,
    sem:        asyncio.Semaphore,
    proxy_pool: "ProxyPool | None",
    pm:         _PendingMonth,
    stop:       asyncio.Event,
) -> list[dict]:
    """Paginate all search-result pages for one court/year/month."""
    records: list[dict] = []
    seen:    set[str]   = set()
    pagenum = 0

    while not stop.is_set():
        page_url = search_url_for_page(pm.month_url, pagenum)
        soup = await _get_soup(session, page_url, sem, proxy_pool)
        if soup is None:
            break

        results = search_page_results(soup)
        if not results:
            break

        for doc_id, title, date_slug in results:
            if doc_id in seen:
                continue
            seen.add(doc_id)
            records.append({
                "doc_id":    doc_id,
                "url":       f"{BASE_URL}/doc/{doc_id}/",
                "pdf_url":   f"{BASE_URL}/doc/{doc_id}/?type=pdf",
                "title":     title,
                "court":     pm.court,
                "year":      pm.year,
                "month":     pm.month,
                "date_slug": date_slug,
            })

        nxt = next_pagenum(soup)
        if nxt is None or pagenum >= nxt:
            break
        pagenum += 1

    return records


async def _process_month(
    session:         aiohttp.ClientSession,
    sem:             asyncio.Semaphore,
    proxy_pool:      "ProxyPool | None",
    write_lock:      asyncio.Lock,
    stop:            asyncio.Event,
    pm:              _PendingMonth,
    done:            set[str],
    summary:         dict,
    pdf_txt_path:    Path,
    jsonl_path:      Path,
    summary_path:    Path,
    checkpoint_path: Path,
) -> None:
    """Fetch all pages for one month, write results atomically, update checkpoint."""
    if stop.is_set():
        return

    records = await _crawl_month_pages(session, sem, proxy_pool, pm, stop)

    async with write_lock:
        if records:
            _append_jsonl(jsonl_path, records)
            _append_pdf_urls(pdf_txt_path, records)
            _record_month(summary, pm.court, pm.year, pm.month, len(records))
            _save_summary(summary_path, summary)

        done.add(pm.month_url)
        _save_checkpoint(checkpoint_path, done)

    print(
        f"  {pm.court:20s} {pm.year} {pm.month:<12s}:"
        f" {len(records):5,} judgments"
        f"  [total: {summary['total']:,}]"
    )


# ── Persistence helpers ───────────────────────────────────────────────────────

def _load_checkpoint(path: Path) -> set[str]:
    if path.exists():
        try:
            data  = json.loads(path.read_text())
            done: set[str] = set(data.get("done", []))
            print(f"Resuming: {len(done)} months already completed")
            return done
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _save_checkpoint(path: Path, done: set[str]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"done": sorted(done)}, indent=2))
    tmp.replace(path)


def _append_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _append_pdf_urls(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(rec["pdf_url"] + "\n")


def _recount_from_jsonl(path: Path) -> tuple[int, dict]:
    """Rebuild (total, by_court_year_month) from an existing urls.jsonl."""
    counts: dict[tuple[str, str, str], int] = {}
    if not path.exists():
        return 0, {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                key = (rec["court"], rec["year"], rec["month"])
                counts[key] = counts.get(key, 0) + 1
            except (json.JSONDecodeError, KeyError):
                pass
    total = sum(counts.values())
    by_court: dict[str, Any] = {}
    for (court, year, month), cnt in counts.items():
        by_court.setdefault(court, {}).setdefault(year, {})[month] = cnt
    return total, by_court


def _record_month(summary: dict, court: str, year: str, month: str, count: int) -> None:
    summary["by_court"].setdefault(court, {}).setdefault(year, {})[month] = count
    summary["total"] = sum(
        v
        for cd in summary["by_court"].values()
        for yd in cd.values()
        for v in yd.values()
    )


def _save_summary(path: Path, summary: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    tmp.replace(path)


# ── Main driver ───────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pdf_txt_path    = out / "pdf_urls.txt"
    jsonl_path      = out / "urls.jsonl"
    summary_path    = out / "summary.json"
    checkpoint_path = out / "checkpoint.json"

    done = _load_checkpoint(checkpoint_path)
    existing_total, existing_by_court = _recount_from_jsonl(jsonl_path)
    summary: dict[str, Any] = {"total": existing_total, "by_court": existing_by_court}
    if existing_total:
        print(f"  {existing_total:,} URLs already collected from prior run(s)\n")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: (
                print("\nInterrupt — finishing in-flight months, then stopping …"),
                stop.set(),
            ))
        except NotImplementedError:
            pass

    proxy_pool = None
    if getattr(args, "use_proxies", False):
        if ProxyPool is None:
            print("[warn] proxy package not found — running without proxies")
        else:
            print("[proxy] initialising pool from spys.one …")
            proxy_pool = ProxyPool()
            await proxy_pool.start()

    sem        = asyncio.Semaphore(args.concurrency)
    write_lock = asyncio.Lock()
    connector  = aiohttp.TCPConnector(limit=args.concurrency * 4, ttl_dns_cache=300)

    async with aiohttp.ClientSession(connector=connector) as session:

        # ── Phase 1: enumerate every (court, year, month, url) tuple ─────────
        all_months = await _enumerate_all_months(
            session, sem, proxy_pool,
            courts_filter=set(args.courts or []),
            years_filter=set(args.years  or []),
        )

        # Deduplicate and exclude already-checkpointed months
        claimed: set[str] = set(done)
        pending: list[_PendingMonth] = []
        for pm in all_months:
            if pm.month_url not in claimed:
                claimed.add(pm.month_url)
                pending.append(pm)

        print(
            f"\nPhase 1 complete: {len(all_months):,} months found, "
            f"{len(pending):,} pending (skipping {len(done):,} already done)\n"
        )
        print("Phase 2: collecting judgment URLs in parallel …\n")

        # ── Phase 2: process all pending months concurrently ─────────────────
        tasks = [
            asyncio.create_task(_process_month(
                session, sem, proxy_pool, write_lock, stop, pm,
                done, summary, pdf_txt_path, jsonl_path, summary_path, checkpoint_path,
            ))
            for pm in pending
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    if proxy_pool is not None:
        await proxy_pool.stop()

    print(f"\n{'=' * 60}")
    print(f"  Total judgments found : {summary['total']:,}")
    print(f"  Courts processed      : {len(summary['by_court'])}")
    print(f"  PDF URLs (flat list)  : {pdf_txt_path}")
    print(f"  Full records (NDJSON) : {jsonl_path}")
    print(f"  Summary               : {summary_path}")

    _save_checkpoint(checkpoint_path, done)
    _save_summary(summary_path, summary)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Collect every judgment URL from indiankanoon.org.\n\n"
            "Phase 1: enumerates all courts/years in parallel (~1 min).\n"
            "Phase 2: fetches all month result pages in parallel.\n\n"
            "Main output: pdf_urls.txt — one PDF URL per line.\n"
            "Re-run any time to resume; completed months are skipped."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--out", default="judgement_count",
                   help="Output directory (default: judgement_count)")
    p.add_argument("--concurrency", type=int, default=20,
                   help="Max parallel HTTP requests (default: 20)")
    p.add_argument("--use-proxies", action="store_true", default=False,
                   help="Rotate IPs via free proxies fetched from spys.one")
    p.add_argument("--courts", nargs="*", default=[],
                   help="Court slugs to restrict to (e.g. supremecourt delhi). Default: all.")
    p.add_argument("--years", nargs="*", type=int, default=[],
                   help="Years to restrict to (e.g. 2023 2024). Default: all.")
    return p.parse_args()


def main() -> None:
    uvloop.install()
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
