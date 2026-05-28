"""Domain model: Job and path helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import PDF_URL_TEMPLATE

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(text: str) -> str:
    """Return a filesystem-safe version of *text*."""
    text = text.strip().replace(" ", "_")
    return _SLUG_RE.sub("_", text).strip("_") or "unknown"


@dataclass(frozen=True)
class Job:
    """A single judgment to be downloaded."""

    court_slug: str
    year: str
    month: str    # human-readable, e.g. "January"
    date: str     # day-level slug, e.g. "14_Jan_2024"; "unknown_date" if not found
    doc_id: str

    @property
    def url(self) -> str:
        return PDF_URL_TEMPLATE.format(doc_id=self.doc_id)

    def output_path(self, root: Path) -> Path:
        # Folder:   root/court/year/month/date/
        # Filename: court_year_month_date_docid.pdf
        c = slugify(self.court_slug)
        y = slugify(self.year)
        m = slugify(self.month)
        d = slugify(self.date)
        filename = f"{c}_{y}_{m}_{d}_{self.doc_id}.pdf"
        return root / c / y / m / d / filename
