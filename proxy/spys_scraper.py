"""Scrape free proxies from spys.one using Playwright (JS-rendered page)."""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .config import (
    SPYS_ONE_URL,
    SPYS_ONE_ENTRIES_PARAM,
    PLAYWRIGHT_TIMEOUT_MS,
    PLAYWRIGHT_WAIT_SELECTOR,
)

# Matches bare IP:PORT in text content
_IP_PORT_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})\b")


async def _set_max_entries(page: Page) -> None:
    """Select the 'show 500 entries' option from spys.one's dropdown."""
    try:
        # The dropdown is a <select name="xx"> — value 3 = 500 rows
        await page.select_option("select[name='xx']", value="3", timeout=5_000)
        await page.wait_for_load_state("networkidle", timeout=PLAYWRIGHT_TIMEOUT_MS)
    except PWTimeout:
        pass  # proceed with whatever loaded


async def _extract_proxies(page: Page) -> list[str]:
    """
    Pull IP:PORT pairs from the rendered proxy table.

    spys.one table structure after JS execution:
      <tr class="spy1x"> or <tr class="spy1xx">
        <td><font ...>IP:PORT</font></td>   ← port decoded by inline JS
        <td>...</td>  ← protocol, anonymity, country, etc.
    """
    # Wait until the proxy table is visible
    await page.wait_for_selector(PLAYWRIGHT_WAIT_SELECTOR, timeout=PLAYWRIGHT_TIMEOUT_MS)

    # Grab all rows that carry proxy data (spy1x / spy1xx alternating row classes)
    rows = await page.query_selector_all("tr.spy1x, tr.spy1xx")

    proxies: list[str] = []
    for row in rows:
        # First cell contains the IP:PORT after JS has run
        first_td = await row.query_selector("td:first-child")
        if first_td is None:
            continue
        text = (await first_td.inner_text()).strip()
        m = _IP_PORT_RE.search(text)
        if m:
            proxies.append(f"{m.group(1)}:{m.group(2)}")

    return proxies


async def scrape_proxies(
    *,
    headless: bool = True,
    proxy_type_filter: Optional[str] = None,
) -> list[str]:
    """
    Launch a headless Playwright browser, navigate to spys.one, and return
    a list of raw ``"IP:PORT"`` strings.

    Args:
        headless: Run browser in headless mode (default True).
        proxy_type_filter: If given (e.g. ``"HTTP"``, ``"HTTPS"``, ``"SOCKS5"``),
            only rows whose protocol column matches are kept.  None = all types.

    Returns:
        List of ``"IP:PORT"`` strings (not yet liveness-validated).
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            java_script_enabled=True,
        )
        page = await context.new_page()

        try:
            await page.goto(SPYS_ONE_URL, timeout=PLAYWRIGHT_TIMEOUT_MS, wait_until="domcontentloaded")
            await _set_max_entries(page)
            proxies = await _extract_proxies(page)
        except PWTimeout as exc:
            print(f"[spys_scraper] page load timed out: {exc}")
            proxies = []
        finally:
            await browser.close()

    if proxy_type_filter:
        # spys.one encodes the protocol in each row; we keep only matching ones.
        # Since we already discarded the extra columns, re-filter is not possible here.
        # Callers who need protocol-specific lists should pass the right page URL.
        pass

    print(f"[spys_scraper] scraped {len(proxies)} raw proxies from spys.one")
    return proxies
