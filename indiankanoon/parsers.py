"""HTML parsers for Indian Kanoon browse and search pages."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup

from .config import BASE_URL


def browse_links(soup: BeautifulSoup, prefix: str) -> list[tuple[str, str]]:
    """Return (text, full_url) for every <a> whose href starts with *prefix*."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith(prefix) or href == prefix:
            continue
        full = urljoin(BASE_URL, href)
        if full in seen:
            continue
        seen.add(full)
        out.append((a.get_text(strip=True), full))
    return out


def month_search_links(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """
    Extract month-level search URLs from a year browse page.

    Indian Kanoon now links months as search queries:
        /search/?formInput=doctypes:<court> fromdate:D-M-YYYY todate:D-M-YYYY
    Link text is the month name (e.g. 'January').
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/search/" not in href or "fromdate" not in href:
            continue
        text = a.get_text(strip=True)
        if not text or text.lower() == "entire year":
            continue
        full = urljoin(BASE_URL, href)
        if full in seen:
            continue
        seen.add(full)
        out.append((text, full))
    return out


def search_page_results(soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    """
    Extract (doc_id, title, date_slug) triples from a search results page.

    Each result is an <article class="result"> with:
        <h4 class="result_title"><a href="/docfragment/<id>/...">Title</a></h4>
        <cite>Court Name | DD Mon, YYYY</cite>  (or similar formats)
        <a href="/doc/<id>/">Full Document</a>

    date_slug is like ``"14_Jan_2024"``; falls back to ``"unknown_date"``.
    """
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for article in soup.find_all("article", class_="result"):
        title_el = article.find(class_="result_title")
        title = title_el.get_text(" ", strip=True) if title_el else ""

        doc_a = article.find("a", href=re.compile(r"^/doc/\d+/?$"))
        if not doc_a:
            continue
        m = re.match(r"^/doc/(\d+)/?$", doc_a["href"])
        if not m:
            continue
        doc_id = m.group(1)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        results.append((doc_id, title, _extract_date_slug(article)))
    return results


# Matches dates like: "14 Jan, 2024", "Jan 14, 2024", "14 January 2024", "January 14, 2024"
_DATE_RE = re.compile(
    r"\b(?:(\d{1,2})\s+([A-Za-z]{3,9})[,.]?\s+(\d{4})"   # DD Mon YYYY
    r"|([A-Za-z]{3,9})\s+(\d{1,2})[,.]?\s+(\d{4}))\b"     # Mon DD YYYY
)
_MONTHS = {
    "january": "Jan", "february": "Feb", "march": "Mar",
    "april": "Apr", "may": "May", "june": "Jun",
    "july": "Jul", "august": "Aug", "september": "Sep",
    "october": "Oct", "november": "Nov", "december": "Dec",
    "jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr",
    "jun": "Jun", "jul": "Jul", "aug": "Aug", "sep": "Sep",
    "oct": "Oct", "nov": "Nov", "dec": "Dec",
}


def _extract_date_slug(article) -> str:
    # indiankanoon encodes the date in the result title link:
    # "Case Title vs Other Party on 8 January, 2024"
    # Check title first, then fall back to any child element.
    title_a = article.find(class_="result_title")
    candidates = ([title_a] if title_a else []) + article.find_all(["cite", "span", "div"])
    for el in candidates:
        text = el.get_text(" ", strip=True)
        m = _DATE_RE.search(text)
        if not m:
            continue
        if m.group(1):  # DD Mon YYYY form
            day, mon_raw, yr = m.group(1), m.group(2), m.group(3)
        else:           # Mon DD YYYY form
            mon_raw, day, yr = m.group(4), m.group(5), m.group(6)
        mon = _MONTHS.get(mon_raw.lower())
        if mon:
            return f"{day.zfill(2)}_{mon}_{yr}"
    return "unknown_date"


def next_pagenum(soup: BeautifulSoup) -> int | None:
    """Return the highest pagenum value in pagination links, or None if only one page."""
    nums = [
        int(m.group(1))
        for a in soup.find_all("a", href=True)
        if (m := re.search(r"pagenum=(\d+)", a["href"]))
    ]
    return max(nums) if nums else None


def search_url_for_page(base_url: str, pagenum: int) -> str:
    """Return *base_url* with pagenum set to *pagenum* (omitted when 0)."""
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop("pagenum", None)
    if pagenum > 0:
        params["pagenum"] = [str(pagenum)]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
