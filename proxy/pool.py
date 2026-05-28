"""
ProxyPool — asyncio-native proxy manager.

Lifecycle
---------
1. Call ``await pool.start()`` once to do the initial scrape + validate.
2. Use ``async with pool.acquire() as proxy:`` in your worker coroutines.
   The context manager returns the proxy on success or discards it on failure.
3. A background task monitors pool depth and re-scrapes when it falls below
   POOL_LOW_WATERMARK.
4. Call ``await pool.stop()`` to cancel the background task on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator, Optional

from .config import POOL_LOW_WATERMARK, POOL_REFRESH_INTERVAL_S, POOL_MAX_SIZE
from .spys_scraper import scrape_proxies
from .validator import validate_batch


class ProxyPool:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=POOL_MAX_SIZE)
        self._bad: set[str] = set()
        self._refresh_lock = asyncio.Lock()
        self._watcher_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initial populate + launch background watcher."""
        await self._refresh()
        self._watcher_task = asyncio.create_task(self._watcher(), name="proxy-pool-watcher")

    async def stop(self) -> None:
        if self._watcher_task:
            self._watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher_task

    @contextlib.asynccontextmanager
    async def acquire(self) -> AsyncIterator[str]:
        """
        Async context manager that hands out one proxy.

        - On normal exit: proxy is returned to the pool (reusable).
        - On exception exit: proxy is discarded (assumed bad).

        Usage::

            async with pool.acquire() as proxy:
                await do_request(proxy)
        """
        proxy = await self._get()
        success = False
        try:
            yield proxy
            success = True
        finally:
            if success:
                self._return(proxy)
            else:
                self._discard(proxy)

    def size(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self) -> str:
        """Block until a proxy is available, triggering a refresh if low."""
        if self._queue.qsize() <= POOL_LOW_WATERMARK:
            asyncio.create_task(self._maybe_refresh())
        return await self._queue.get()

    def _return(self, proxy: str) -> None:
        try:
            self._queue.put_nowait(proxy)
        except asyncio.QueueFull:
            pass  # pool is full; discard rather than block

    def _discard(self, proxy: str) -> None:
        self._bad.add(proxy)
        print(f"[pool] discarded bad proxy {proxy} (pool size: {self._queue.qsize()})")

    async def _maybe_refresh(self) -> None:
        """Refresh only if not already refreshing."""
        if self._refresh_lock.locked():
            return
        await self._refresh()

    async def _refresh(self) -> None:
        async with self._refresh_lock:
            print("[pool] refreshing proxy list from spys.one …")
            raw = await scrape_proxies()
            # filter out previously confirmed bad proxies
            raw = [p for p in raw if p not in self._bad]
            live = await validate_batch(raw)
            added = 0
            for proxy in live:
                try:
                    self._queue.put_nowait(proxy)
                    added += 1
                except asyncio.QueueFull:
                    break
            print(f"[pool] added {added} live proxies (pool size now: {self._queue.qsize()})")

    async def _watcher(self) -> None:
        """Periodically refresh so the pool never silently drains."""
        while True:
            await asyncio.sleep(POOL_REFRESH_INTERVAL_S)
            if self._queue.qsize() <= POOL_LOW_WATERMARK:
                await self._maybe_refresh()
