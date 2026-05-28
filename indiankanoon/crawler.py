"""Crawl producer: walks Court -> Year -> Month and enqueues download Jobs."""

from __future__ import annotations

import asyncio
import re

import aiohttp

from .config import BASE_URL, BROWSE_URL
from .http import fetch_html
from .models import Job
from .parsers import (
    browse_links,
    month_search_links,
    next_pagenum,
    search_page_results,
    search_url_for_page,
)
from .state import Checkpoint


async def crawl_producer(
    session: aiohttp.ClientSession,
    queue: asyncio.Queue,
    crawl_sem: asyncio.Semaphore,
    checkpoint: Checkpoint,
    stop: asyncio.Event,
    courts_filter: set[str],
    years_filter: set[int],
) -> None:
    soup = await fetch_html(session, BROWSE_URL, crawl_sem)
    if soup is None:
        print("Could not fetch /browse/ — nothing to do")
        return

    courts = browse_links(soup, "/browse/")
    print(f"Found {len(courts)} courts")

    for court_name, court_url in courts:
        if stop.is_set():
            break
        court_slug = court_url.rstrip("/").split("/")[-1]
        if courts_filter and court_slug not in courts_filter:
            continue
        print(f"\n=== Court: {court_name} ({court_slug}) ===")

        court_soup = await fetch_html(session, court_url, crawl_sem)
        if court_soup is None:
            print(f"  [!] Could not fetch court page: {court_url}")
            continue

        year_prefix = court_url.replace(BASE_URL, "").rstrip("/") + "/"
        year_links = browse_links(court_soup, year_prefix)
        print(f"  Found {len(year_links)} year links")

        for year_text, year_url in year_links:
            if stop.is_set():
                break
            ymatch = re.search(r"\b(19|20)\d{2}\b", year_text + year_url)
            if not ymatch:
                continue
            year = int(ymatch.group(0))
            if years_filter and year not in years_filter:
                continue

            year_soup = await fetch_html(session, year_url, crawl_sem)
            if year_soup is None:
                print(f"  [!] Could not fetch year page: {year_url}")
                continue

            months = month_search_links(year_soup)
            print(f"  Year {year}: {len(months)} months")

            for month_name, month_url in months:
                if stop.is_set():
                    break
                if checkpoint.has(month_url):
                    print(f"    [skip] {month_name} (already done)")
                    continue

                queued = await _crawl_month(
                    session, queue, crawl_sem, stop,
                    court_slug, str(year), month_name, month_url,
                )
                print(f"    {month_name}: {queued} judgments queued")
                await checkpoint.add(month_url)


async def _crawl_month(
    session: aiohttp.ClientSession,
    queue: asyncio.Queue,
    crawl_sem: asyncio.Semaphore,
    stop: asyncio.Event,
    court_slug: str,
    year: str,
    month_name: str,
    month_url: str,
) -> int:
    """Paginate through all search result pages for one month and enqueue Jobs."""
    total = 0
    pagenum = 0
    while not stop.is_set():
        page_url = search_url_for_page(month_url, pagenum)
        page_soup = await fetch_html(session, page_url, crawl_sem)
        if page_soup is None:
            print(f"    [!] Could not fetch {month_name} page {pagenum}")
            break

        results = search_page_results(page_soup)
        if not results:
            break

        for doc_id, _title, date_slug in results:
            await queue.put(Job(
                court_slug=court_slug,
                year=year,
                month=month_name,
                date=date_slug,
                doc_id=doc_id,
            ))
            total += 1

        max_pn = next_pagenum(page_soup)
        if max_pn is None or pagenum >= max_pn:
            break
        pagenum += 1

    return total
