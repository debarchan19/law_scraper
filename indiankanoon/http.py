"""Async HTTP helpers: exponential-backoff fetch and jittered sleep."""

from __future__ import annotations

import asyncio
import random

import aiohttp
from bs4 import BeautifulSoup

from .config import (
    BASE_DELAY,
    BACKOFF_FACTOR,
    HTML_HEADERS,
    INITIAL_BACKOFF,
    JITTER_RATIO,
    MAX_BACKOFF,
    MAX_RETRIES,
    PER_REQUEST_TIMEOUT,
)


async def sleep_with_jitter(seconds: float) -> None:
    seconds = min(seconds, MAX_BACKOFF)
    jitter = seconds * JITTER_RATIO
    await asyncio.sleep(max(0.0, random.uniform(seconds - jitter, seconds + jitter)))


async def fetch_html(
    session: aiohttp.ClientSession,
    url: str,
    sem: asyncio.Semaphore,
    proxy_url: str | None = None,
) -> BeautifulSoup | None:
    """GET *url* with exponential backoff. Returns parsed soup or None on permanent failure."""
    backoff = INITIAL_BACKOFF
    async with sem:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(
                    url,
                    headers=HTML_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=PER_REQUEST_TIMEOUT),
                    proxy=proxy_url,
                ) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        await sleep_with_jitter(BASE_DELAY)
                        return BeautifulSoup(text, "html.parser")

                    if resp.status == 404:
                        return None

                    if resp.status in (403, 429) or 500 <= resp.status < 600:
                        retry_after = resp.headers.get("Retry-After")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.isdigit()
                            else backoff
                        )
                        print(
                            f"  [crawl {resp.status}] {url} "
                            f"— retry {attempt}/{MAX_RETRIES} in ~{wait:.1f}s"
                        )
                        await sleep_with_jitter(wait)
                        backoff *= BACKOFF_FACTOR
                        continue

                    print(f"  [crawl {resp.status}] {url} — giving up")
                    return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                print(
                    f"  [crawl error] {url}: {exc} "
                    f"— retry {attempt}/{MAX_RETRIES} in ~{backoff:.1f}s"
                )
                await sleep_with_jitter(backoff)
                backoff *= BACKOFF_FACTOR

    print(f"  exhausted retries (crawl): {url}")
    return None
