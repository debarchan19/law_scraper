#!/usr/bin/env bash
# smoke.sh — drive the law_scraper CLI and verify output structure.
# Usage: bash .claude/skills/run-law-scraper/smoke.sh [OUT_DIR]
# Run from repo root. Requires: .venv already set up (uv venv + uv pip install).

set -euo pipefail

OUT="${1:-/tmp/ik_smoke_$$}"
PYTHON=".venv/bin/python"
TIMEOUT="${SMOKE_TIMEOUT:-25}"   # seconds before SIGINT

echo "=== law_scraper smoke test ==="
echo "Output dir : $OUT"
echo "Crawl court: supremecourt, year 2024 (January only)"
echo ""

rm -rf "$OUT"

# ── Launch the crawler in background, let it run for TIMEOUT seconds ─────────
"$PYTHON" download_pdfs.py \
  --courts supremecourt \
  --years 2024 \
  --crawl-concurrency 2 \
  --download-concurrency 3 \
  --out "$OUT" &
CRAWLER_PID=$!

# Send SIGINT after TIMEOUT — the crawler drains in-flight work then exits cleanly
(sleep "$TIMEOUT" && kill -INT "$CRAWLER_PID" 2>/dev/null) &
KILLER_PID=$!

wait "$CRAWLER_PID"
CRAWL_EXIT=$?
kill "$KILLER_PID" 2>/dev/null || true

# ── Verify output structure ───────────────────────────────────────────────────
PDF_COUNT=$(find "$OUT" -name "*.pdf" | wc -l | tr -d ' ')

if [ "$PDF_COUNT" -eq 0 ]; then
  echo "FAIL: no PDFs downloaded" >&2
  exit 1
fi

# Every PDF must match the naming convention: court_year_month_date_docid.pdf
# grep -v exits 1 when nothing fails (= all paths matched) — suppress that with || true
BAD=$(find "$OUT" -name "*.pdf" \
  | { grep -v '[A-Za-z0-9_]*/[0-9]\{4\}/[A-Za-z]*/[0-9][0-9]_[A-Za-z][a-z]*_[0-9]\{4\}/' || true; } \
  | wc -l | tr -d ' ')

# Check checkpoint file was written
if [ ! -f "$OUT/checkpoint.json" ]; then
  echo "FAIL: no checkpoint.json" >&2
  exit 1
fi

echo ""
echo "=== Results ==="
echo "PDFs downloaded : $PDF_COUNT"
echo "Misnamed files  : $BAD"
echo "Sample paths:"
find "$OUT" -name "*.pdf" | head -5

if [ "$BAD" -gt 0 ]; then
  echo "FAIL: $BAD files don't match court/year/month/date/ structure" >&2
  exit 1
fi

echo ""
echo "PASS"
