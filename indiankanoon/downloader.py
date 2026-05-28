"""PDF download worker with exponential backoff and atomic writes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import aiofiles
import aiohttp

from .config import (
    BACKOFF_FACTOR,
    BASE_DELAY,
    INITIAL_BACKOFF,
    MAX_RETRIES,
    PDF_HEADERS,
    PER_REQUEST_TIMEOUT,
)
from .http import sleep_with_jitter
from .models import Job
from .state import FailureLog

if TYPE_CHECKING:
    from proxy import ProxyPool

# Sentinel value pushed to the queue to signal workers to stop
END: object = object()


async def download_pdf(
    session: aiohttp.ClientSession,
    job: Job,
    out_root: Path,
    sem: asyncio.Semaphore,
    failures: FailureLog,
    counters: dict,
    proxy_url: Optional[str] = None,
) -> bool:
    """Download one PDF.  Returns True on success, False if all retries exhausted."""
    target = job.output_path(out_root)
    if target.exists() and target.stat().st_size > 0:
        counters["skipped"] += 1
        await failures.clear(job.doc_id)
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    backoff = INITIAL_BACKOFF
    last_reason = "unknown"

    async with sem:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(
                    job.url,
                    headers=PDF_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=PER_REQUEST_TIMEOUT),
                    allow_redirects=True,
                    proxy=proxy_url,
                ) as resp:
                    if resp.status == 200:
                        content_type = (resp.headers.get("Content-Type") or "").lower()
                        is_pdf = "pdf" in content_type
                        sniffed = False
                        async with aiofiles.open(tmp, "wb") as f:
                            async for chunk in resp.content.iter_chunked(64 * 1024):
                                if not sniffed:
                                    if not is_pdf and chunk[:5] == b"%PDF-":
                                        is_pdf = True
                                    sniffed = True
                                await f.write(chunk)

                        if not is_pdf:
                            _rm(tmp)
                            last_reason = f"non-pdf content-type '{content_type}'"
                            print(
                                f"  [{job.doc_id}] {last_reason} "
                                f"— retry {attempt}/{MAX_RETRIES} in ~{backoff:.1f}s"
                            )
                            await sleep_with_jitter(backoff)
                            backoff *= BACKOFF_FACTOR
                            continue

                        tmp.replace(target)
                        counters["downloaded"] += 1
                        await failures.clear(job.doc_id)
                        await sleep_with_jitter(BASE_DELAY)
                        return True

                    if resp.status == 404:
                        last_reason = "404 not found"
                        print(f"  [{job.doc_id}] 404 — giving up")
                        break

                    if resp.status in (403, 429) or 500 <= resp.status < 600:
                        retry_after = resp.headers.get("Retry-After")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.isdigit()
                            else backoff
                        )
                        last_reason = f"http {resp.status}"
                        print(
                            f"  [{job.doc_id}] {resp.status} "
                            f"— retry {attempt}/{MAX_RETRIES} in ~{wait:.1f}s"
                        )
                        await sleep_with_jitter(wait)
                        backoff *= BACKOFF_FACTOR
                        continue

                    last_reason = f"http {resp.status}"
                    print(f"  [{job.doc_id}] {resp.status} — giving up")
                    break

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_reason = f"{type(exc).__name__}: {exc}"
                print(
                    f"  [{job.doc_id}] {last_reason} "
                    f"— retry {attempt}/{MAX_RETRIES} in ~{backoff:.1f}s"
                )
                await sleep_with_jitter(backoff)
                backoff *= BACKOFF_FACTOR

    _rm(tmp)
    counters["failed"] += 1
    await failures.record(job.doc_id, last_reason)
    return False


async def download_worker(
    session: aiohttp.ClientSession,
    queue: asyncio.Queue,
    out_root: Path,
    sem: asyncio.Semaphore,
    failures: FailureLog,
    counters: dict,
    total_seen: list[int],
    print_every: int = 50,
    proxy_pool: "Optional[ProxyPool]" = None,
) -> None:
    while True:
        job = await queue.get()
        try:
            if job is END:
                return
            if proxy_pool is not None:
                # Acquire a proxy for this job; discard it if the download fails.
                proxy = await proxy_pool._get()
                proxy_url = f"http://{proxy}"
                ok = await download_pdf(session, job, out_root, sem, failures, counters, proxy_url)
                if ok:
                    proxy_pool._return(proxy)
                else:
                    proxy_pool._discard(proxy)
            else:
                await download_pdf(session, job, out_root, sem, failures, counters)
            done = counters["downloaded"] + counters["skipped"] + counters["failed"]
            if done % print_every == 0 and done > 0:
                _print_progress(counters, total_seen[0])
        finally:
            queue.task_done()


def _print_progress(counters: dict, seen: int) -> None:
    done = counters["downloaded"] + counters["skipped"] + counters["failed"]
    print(
        f"  progress: {done}/{seen}  "
        f"downloaded={counters['downloaded']}  "
        f"skipped={counters['skipped']}  "
        f"failed={counters['failed']}"
    )


def _rm(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
