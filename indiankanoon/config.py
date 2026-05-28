"""All constants and configuration for the Indian Kanoon crawler."""

BASE_URL = "https://indiankanoon.org"
BROWSE_URL = f"{BASE_URL}/browse/"
PDF_URL_TEMPLATE = f"{BASE_URL}/doc/{{doc_id}}/?type=pdf"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

HTML_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

PDF_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/pdf,*/*;q=0.8",
}

# Exponential backoff
INITIAL_BACKOFF: float = 2.0
BACKOFF_FACTOR: float = 2.0
MAX_BACKOFF: float = 300.0
JITTER_RATIO: float = 0.25
MAX_RETRIES: int = 6
PER_REQUEST_TIMEOUT: int = 60

# Polite pause after each successful request
BASE_DELAY: float = 0.5
