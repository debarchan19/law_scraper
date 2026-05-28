"""Configuration constants for the proxy subsystem."""

# --- spys.one scraper ---
SPYS_ONE_URL = "https://spys.one/en/free-proxy-list/"
# How many proxies to request per page (xx=3 → 500 entries, xx=2 → 300, xx=1 → 150)
SPYS_ONE_ENTRIES_PARAM = "xx=3"
PLAYWRIGHT_TIMEOUT_MS = 60_000
PLAYWRIGHT_WAIT_SELECTOR = "tr.spy1x"

# --- liveness validation ---
VALIDATE_URL = "http://httpbin.org/ip"
VALIDATE_TIMEOUT_S = 8
VALIDATE_CONCURRENCY = 40  # parallel aiohttp checks

# --- proxy pool ---
POOL_LOW_WATERMARK = 15    # trigger background refresh below this
POOL_REFRESH_INTERVAL_S = 300  # fallback periodic refresh (seconds)
POOL_MAX_SIZE = 500

# --- per-request proxy usage ---
REQUEST_TIMEOUT_S = 20
