"""proxy — free-proxy sourcing, validation, and pooling for async scrapers."""

from .pool import ProxyPool
from .spys_scraper import scrape_proxies
from .validator import is_alive, is_valid_format, validate_batch

__all__ = [
    "ProxyPool",
    "scrape_proxies",
    "is_valid_format",
    "is_alive",
    "validate_batch",
]
