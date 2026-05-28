"""CLI entry point: argument parsing, async driver, periodic flush."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

import aiohttp
import uvloop

from .crawler import crawl_producer
from .downloader import END, download_worker, _print_progress
from .state import Checkpoint, FailureLog, JsonStore

try:
    from proxy import ProxyPool as _ProxyPool
except ImportError:
    _ProxyPool = None  # type: ignore[assignment,misc]


async def _periodic_flush(stores: list[JsonStore], stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
        for s in stores:
            await s.flush()


async def run(args: argparse.Namespace) -> int:
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    checkpoint = Checkpoint(out_root / "checkpoint.json")
    failures = FailureLog(out_root / "failures.json")
    counters: dict = {"downloaded": 0, "skipped": 0, "failed": 0}
    total_seen: list[int] = [0]

    proxy_pool = None
    if getattr(args, "use_proxies", False):
        if _ProxyPool is None:
            print("[warn] proxy package not found — running without proxies")
        else:
            print("[proxy] initialising pool from spys.one …")
            proxy_pool = _ProxyPool()
            await proxy_pool.start()

    crawl_sem = asyncio.Semaphore(args.crawl_concurrency)
    download_sem = asyncio.Semaphore(args.download_concurrency)
    queue: asyncio.Queue = asyncio.Queue(maxsize=args.download_concurrency * 8)
    stop = asyncio.Event()

    def _handle_signal() -> None:
        if not stop.is_set():
            print("\nInterrupt — draining in-flight work...")
            stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    connector = aiohttp.TCPConnector(
        limit=(args.crawl_concurrency + args.download_concurrency) * 2,
        ttl_dns_cache=300,
    )

    async with aiohttp.ClientSession(connector=connector) as session:
        flush_task = asyncio.create_task(_periodic_flush([checkpoint, failures], stop))

        # Wrap queue.put to count enqueued jobs
        _orig_put = queue.put

        async def _counting_put(item):
            if item is not END:
                total_seen[0] += 1
            await _orig_put(item)

        queue.put = _counting_put  # type: ignore[assignment]

        producer = asyncio.create_task(crawl_producer(
            session, queue, crawl_sem, checkpoint, stop,
            courts_filter=set(args.courts or []),
            years_filter=set(args.years or []),
        ))

        workers = [
            asyncio.create_task(download_worker(
                session, queue, out_root, download_sem,
                failures, counters, total_seen,
                proxy_pool=proxy_pool,
            ))
            for _ in range(args.download_concurrency)
        ]

        try:
            await producer
        finally:
            for _ in workers:
                await queue.put(END)
            await asyncio.gather(*workers, return_exceptions=True)

        stop.set()
        await flush_task
        await checkpoint.flush()
        await failures.flush()

    if proxy_pool is not None:
        await proxy_pool.stop()

    print()
    _print_progress(counters, total_seen[0])
    print(f"Checkpoint : {checkpoint.path}")
    print(f"Failure log: {failures.path}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Async crawler + PDF downloader for Indian Kanoon.\n"
            "Walks Court → Year → Month and downloads every judgment as a PDF.\n\n"
            "Output: pdfs/<court_slug>/<year>/<month>/<doc_id>.pdf"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--out", default="pdfs",
        help="Root directory for downloaded PDFs (default: pdfs)",
    )
    p.add_argument(
        "--crawl-concurrency", type=int, default=4,
        help="Max parallel browse/search page fetches (default: 4)",
    )
    p.add_argument(
        "--download-concurrency", type=int, default=8,
        help="Max parallel PDF downloads (default: 8)",
    )
    p.add_argument(
        "--courts", nargs="*", default=[],
        help="Court slugs to restrict to (e.g. supremecourt delhi). Empty = all.",
    )
    p.add_argument(
        "--years", nargs="*", type=int, default=[],
        help="Years to restrict to (e.g. 2023 2024). Empty = all.",
    )
    p.add_argument(
        "--use-proxies", action="store_true", default=False,
        help="Route PDF downloads through free proxies fetched from spys.one.",
    )
    return p.parse_args()


def main() -> int:
    uvloop.install()
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
