#!/usr/bin/env bash
# Rebuild the CCR-vs-RMP correlation datasets + report from source DBs.
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
"$PY" build_ccr_db.py        # -> ccr_ratings.db    (SQLite; scrapes collegeclassreviews.com, 7-day cache)
"$PY" build_ccr_ratings.py   # -> ccr_ratings.csv   (CSV export of the same rows)
"$PY" build_rmp.py           # -> rmp_ratings.csv + rmp_reviews.csv
"$PY" build_merged.py        # -> merged.csv (+ RateMySlides slide scores)
"$PY" correlate.py           # -> correlation_report.md, matrices, pairs, plots/
