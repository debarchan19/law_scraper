"""Persistent async-safe state: checkpoint and failure log."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiofiles


class JsonStore:
    """Base class: async-safe JSON file with atomic writes."""

    def __init__(self, path: Path, default):
        self.path = path
        self._data = default
        self._lock = asyncio.Lock()
        self._dirty = False
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    async def flush(self) -> None:
        async with self._lock:
            if not self._dirty:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self._data, indent=2, ensure_ascii=False))
            tmp.replace(self.path)
            self._dirty = False


class Checkpoint(JsonStore):
    """Set of month-search-URLs that have been fully crawled."""

    def __init__(self, path: Path):
        super().__init__(path, default=[])
        self._set: set[str] = set(self._data) if isinstance(self._data, list) else set()

    def has(self, url: str) -> bool:
        return url in self._set

    async def add(self, url: str) -> None:
        async with self._lock:
            if url not in self._set:
                self._set.add(url)
                self._data = sorted(self._set)
                self._dirty = True


class FailureLog(JsonStore):
    """Map of doc_id -> last failure reason for downloads that exhausted retries."""

    def __init__(self, path: Path):
        super().__init__(path, default={})
        if not isinstance(self._data, dict):
            self._data = {}

    async def record(self, doc_id: str, reason: str) -> None:
        async with self._lock:
            self._data[doc_id] = reason
            self._dirty = True

    async def clear(self, doc_id: str) -> None:
        async with self._lock:
            if self._data.pop(doc_id, None) is not None:
                self._dirty = True
