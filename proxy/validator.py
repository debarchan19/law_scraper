"""Proxy validation: format check and async liveness testing via aiohttp."""

from __future__ import annotations

import asyncio
import re

import aiohttp

from .config import VALIDATE_URL, VALIDATE_TIMEOUT_S, VALIDATE_CONCURRENCY

_IP_PORT_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}$")


def is_valid_format(proxy: str) -> bool:
    """Return True if *proxy* matches ``IP:PORT`` exactly."""
    return bool(_IP_PORT_RE.match(proxy.strip()))


async def is_alive(proxy: str, session: aiohttp.ClientSession) -> bool:
    """
    Attempt a GET through *proxy*.  Returns True if we get any HTTP response
    within VALIDATE_TIMEOUT_S seconds.
    """
    proxy_url = f"http://{proxy}"
    try:
        async with session.get(
            VALIDATE_URL,
            proxy=proxy_url,
            timeout=aiohttp.ClientTimeout(total=VALIDATE_TIMEOUT_S),
            ssl=False,
        ) as resp:
            return resp.status < 500
    except Exception:
        return False


async def validate_batch(proxies: list[str]) -> list[str]:
    """
    Format-filter then liveness-check *proxies* in parallel.

    Returns the subset that passed both checks, preserving relative order.
    """
    # 1. format filter (CPU-only, instant)
    candidates = [p for p in proxies if is_valid_format(p)]
    print(f"[validator] {len(candidates)}/{len(proxies)} passed format check")

    # 2. liveness check (I/O-bound, run concurrently)
    sem = asyncio.Semaphore(VALIDATE_CONCURRENCY)
    results: list[tuple[str, bool]] = []

    async def _check(proxy: str) -> tuple[str, bool]:
        async with sem:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                alive = await is_alive(proxy, session)
            return proxy, alive

    tasks = [asyncio.create_task(_check(p)) for p in candidates]
    for coro in asyncio.as_completed(tasks):
        results.append(await coro)

    live = [p for p, ok in results if ok]
    print(f"[validator] {len(live)}/{len(candidates)} passed liveness check")
    return live
