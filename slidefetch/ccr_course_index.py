#!/usr/bin/env python3
"""Index every *rated* course on collegeclassreviews.com (CCR) into SQLite.

For each of CCR's ~320 universities, the paginated listing at
``/universities/<slug>/courses`` is walked to find courses that carry aggregated
rating data. Each such course's detail page is then read to get its exact number
of student ratings, and only courses with **more than one rating** are stored.

Every row holds the CCR link, the number of ratings, and a single
human-readable string combining the course name with its college name
(e.g. ``"CS61A - University of California, Berkeley"``).

Usage:
    python ccr_course_index.py              # full crawl (resumes if interrupted)
    python ccr_course_index.py --limit 5    # only the first 5 universities
    python ccr_course_index.py --refresh    # re-scrape universities already done
"""
from __future__ import annotations

import argparse
import html as htmllib
import http.client
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from ccr_course_ratings import parse as parse_course_page

BASE = "https://collegeclassreviews.com"
UNIVERSITIES_URL = f"{BASE}/universities"
DB_PATH = "ccr_courses.db"

# Parses each university object out of the streamed Next.js JSON on /universities.
_UNI_RE = re.compile(
    r'\\"name\\":\\"((?:[^"\\]|\\.)*?)\\",'
    r'\\"short_name\\":\\"(?:[^"\\]|\\.)*?\\",'
    r'\\"slug\\":\\"([a-z0-9-]+)\\"'
)
# Parses each course object out of the streamed JSON on a course-listing page.
_COURSE_RE = re.compile(
    r'\\"code\\":\\"((?:[^"\\]|\\.)*?)\\".*?'
    r'\\"department\\":(null|\\"(?:[^"\\]|\\.)*?\\").*?'
    r'\\"slug\\":\\"([a-z0-9-]+)\\",\\"universities\\":\{\\"slug\\":\\"[a-z0-9-]+\\"\},'
    r'\\"totalReviews\\":(\d+),\\"avgRating\\":[0-9.]+,'
    r'\\"avgDifficulty\\":[0-9.]+,\\"avgHours\\":[0-9.]+,'
    r'\\"snippet\\":(?:[^,]*),\\"snippetSource\\":\\"((?:[^"\\]|\\.)*?)\\"',
    re.S,
)


def _unescape(s: str) -> str:
    """Undo JSON-string and HTML-entity escaping found in the page source."""
    s = s.replace("\\u0026", "&").replace('\\"', '"').replace("\\\\", "\\")
    return htmllib.unescape(s)


def fetch(url: str, *, retries: int = 5, backoff: float = 3.0) -> str:
    """GET a URL as text, retrying transient network/server errors.

    Rate-limit (429) and server (5xx) responses are retried with exponential
    backoff. 404/410 are raised immediately. Persistent failure raises so the
    caller can avoid recording bad/empty data.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                raise
            last = exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            last = exc
        time.sleep(backoff * (2 ** attempt))
    assert last is not None
    raise last


def parse_universities(html: str) -> list[tuple[str, str]]:
    """Return de-duplicated ``(slug, college_name)`` pairs in document order."""
    seen: dict[str, str] = {}
    for name, slug in _UNI_RE.findall(html):
        seen.setdefault(slug, _unescape(name))
    return list(seen.items())


def parse_rated_candidates(html: str) -> list[tuple[str, str, str, int]]:
    """Return ``(course_slug, code, department, total_reviews)`` for courses on a
    listing page that carry aggregated rating data (an "insight" snippet) or have
    native reviews. Courses with no rating signal are skipped.
    """
    out: list[tuple[str, str, str, int]] = []
    seen: set[str] = set()
    for code, dept, course_slug, total, snippet_src in _COURSE_RE.findall(html):
        if course_slug in seen:
            continue
        total_reviews = int(total)
        has_ratings = snippet_src != "$undefined" or total_reviews > 0
        if not has_ratings:
            continue
        seen.add(course_slug)
        department = "" if dept == "null" else _unescape(dept.strip('\\"'))
        out.append((course_slug, _unescape(code), department, total_reviews))
    return out


def iter_rated_candidates(slug: str, delay: float):
    """Yield rating-bearing course candidates for a university across all pages.

    Page 1 is the bare ``/courses`` URL; later pages use ``?page=N``. A page with
    no course links at all terminates the walk.
    """
    base = f"{BASE}/universities/{slug}/courses"
    page = 1
    while True:
        url = base if page == 1 else f"{base}?page={page}"
        try:
            html = fetch(url)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410) and page > 1:
                break
            raise
        # Only an empty page (no course links) ends the walk; a failed fetch
        # propagates so the university is retried rather than recorded empty.
        if f"/universities/{slug}/courses/" not in html:
            break
        yield from parse_rated_candidates(html)
        page += 1
        time.sleep(delay)


def rating_count(slug: str, course_slug: str, fallback: int) -> tuple[int, float | None]:
    """Read a course detail page; return ``(num_ratings, star_rating)``.

    Falls back to the listing's native review count if the page exposes no
    aggregated total.
    """
    url = f"{BASE}/universities/{slug}/courses/{course_slug}"
    try:
        data = parse_course_page(fetch(url))
    except urllib.error.HTTPError:
        return fallback, None
    num = data.get("num_reviews")
    if not num:
        num = fallback
    return int(num or 0), data.get("star_rating")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS courses (
            ccr_link        TEXT PRIMARY KEY,
            university_slug TEXT,
            college_name    TEXT,
            course_code     TEXT,
            department      TEXT,
            num_ratings     INTEGER,
            star_rating     REAL,
            course_college  TEXT,   -- "<course name> - <college name>"
            scraped_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS scraped_universities (
            slug         TEXT PRIMARY KEY,
            college_name TEXT,
            course_count INTEGER,   -- courses kept (>1 rating)
            scraped_at   TEXT
        );
        """
    )
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB_PATH, help="SQLite output path")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N universities")
    ap.add_argument("--delay", type=float, default=0.25,
                    help="seconds to wait between requests")
    ap.add_argument("--refresh", action="store_true",
                    help="re-scrape universities already recorded as done")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    init_db(conn)

    universities = parse_universities(fetch(UNIVERSITIES_URL))
    print(f"discovered {len(universities)} universities")
    if args.limit is not None:
        universities = universities[: args.limit]

    done = {row[0] for row in conn.execute("SELECT slug FROM scraped_universities")}

    total_kept = 0
    for idx, (slug, college) in enumerate(universities, 1):
        if not args.refresh and slug in done:
            print(f"[{idx}/{len(universities)}] {slug}: already done, skipping")
            continue

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            candidates = list(iter_rated_candidates(slug, args.delay))
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            print(f"[{idx}/{len(universities)}] {slug}: FAILED ({exc}); "
                  f"will retry on next run")
            continue
        kept = 0
        for course_slug, code, department, total in candidates:
            num, star = rating_count(slug, course_slug, total)
            time.sleep(args.delay)
            if num <= 1:
                continue
            link = f"{BASE}/universities/{slug}/courses/{course_slug}"
            conn.execute(
                """
                INSERT OR REPLACE INTO courses
                    (ccr_link, university_slug, college_name, course_code,
                     department, num_ratings, star_rating, course_college,
                     scraped_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (link, slug, college, code, department, num, star,
                 f"{code} - {college}", now),
            )
            kept += 1

        conn.execute(
            """
            INSERT OR REPLACE INTO scraped_universities
                (slug, college_name, course_count, scraped_at)
            VALUES (?,?,?,?)
            """,
            (slug, college, kept, now),
        )
        conn.commit()
        total_kept += kept
        print(f"[{idx}/{len(universities)}] {slug} ({college}): "
              f"{kept} rated courses kept (of {len(candidates)} candidates)")

    grand = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    conn.close()
    print(f"\nrun kept {total_kept} courses; database now holds {grand} total "
          f"-> {args.db}")


if __name__ == "__main__":
    main()
