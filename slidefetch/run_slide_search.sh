#!/usr/bin/env bash
# Waits for the CCR course crawl (ccr_course_index.py) to finish, then runs the
# slide-link search over every course_college row in ccr_courses.db.
set -u
cd "$(dirname "$0")"

echo "[runner] waiting for ccr_course_index.py to finish..."
while pgrep -f 'ccr_course_index.py' >/dev/null 2>&1; do
    sleep 30
done
echo "[runner] crawl finished at $(date -u +%FT%TZ); starting slide search"

python3.11 -u ccr_slide_search.py \
    --courses-db ccr_courses.db \
    --out-db ccr_slide_links.db \
    --delay 2

echo "[runner] slide search finished at $(date -u +%FT%TZ)"
