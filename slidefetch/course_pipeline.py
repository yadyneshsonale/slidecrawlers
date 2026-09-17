#!/usr/bin/env python3
"""End-to-end course pipeline.

For one course page URL (or a search term) it performs the whole manual
workflow automatically and writes a single row to ``course_pipeline.db``:

    1. Resolve the university from the URL host (school_map).
    2. Extract the course code (cse127) from the URL.
    3. (optional) Download the slide decks via slidefetch.
    4. Find the instructor (CLI arg, course page, or course_instructors.db).
    5. Look up the instructor on RateMyProfessors, disambiguated by school.
    6. Look up the course on CollegeClassReviews, with discipline-aware
       abbreviation fallback; record real ratings or "not found".

Anything genuinely ambiguous (unknown school, RMP name collision, CCR with no
verified slug) is written to a ``review_queue`` table instead of being guessed.

No LLM is used: every step is a deterministic lookup or an existing API call.

Usage:
    python course_pipeline.py "https://cseweb.ucsd.edu/classes/wi21/cse127-a/"
    python course_pipeline.py "<url>" --instructor "Nadia Heninger"
    python course_pipeline.py --search "cs 127 course slides"
    python course_pipeline.py "<url>" --slides        # also download decks
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

import ccr_lookup
from rmp_crawler import check_one, expand_instructors
from school_map import extract_course_code, resolve_school

try:  # slidefetch is the local package; used for slug + university filter
    from slidefetch import urls as sf_urls
    from slidefetch.search import web_search
except Exception:  # pragma: no cover - keep pipeline usable without the package
    sf_urls = None
    web_search = None

DB_PATH = "course_pipeline.db"
INSTRUCTOR_DB = "course_instructors.db"
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0 Safari/537.36"}


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def open_db(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS course_pipeline (
            course_key                TEXT PRIMARY KEY,
            query                     TEXT,
            course_url                TEXT,
            host                      TEXT,
            school_canonical          TEXT,
            school_status             TEXT,
            course_code               TEXT,
            disciplines               TEXT,
            slides_dir                TEXT,
            slides_count              INTEGER,
            instructor                TEXT,
            instructor_source         TEXT,
            rmp_status                TEXT,
            rmp_matched_name          TEXT,
            rmp_school                TEXT,
            rmp_profile_url           TEXT,
            rmp_avg_rating            REAL,
            rmp_avg_difficulty        REAL,
            rmp_num_ratings           INTEGER,
            rmp_would_take_again      REAL,
            ccr_status                TEXT,
            ccr_url                   TEXT,
            ccr_code                  TEXT,
            ccr_star_rating           REAL,
            ccr_num_reviews           INTEGER,
            ccr_student_satisfaction  REAL,
            ccr_challenge_level       REAL,
            ccr_grade_accessibility   REAL,
            ccr_time_investment       REAL,
            ccr_attendance_importance REAL,
            ccr_recommendation_rate   REAL,
            needs_review              INTEGER,
            review_reasons            TEXT,
            created_at                TEXT,
            updated_at                TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            course_key  TEXT,
            stage       TEXT,
            reason      TEXT,
            details     TEXT,
            resolved    INTEGER DEFAULT 0,
            created_at  TEXT
        )
        """
    )
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# Instructor discovery
# --------------------------------------------------------------------------- #
def _http_get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


_INSTRUCTOR_LABEL = re.compile(
    r"(?:instructor|professor|lecturer|taught\s+by|teacher)s?\s*[:\-]?\s*(.+)",
    re.I,
)


def scrape_instructor(url: str) -> str | None:
    """Best-effort: pull an instructor name off the course page text."""
    try:
        html = _http_get(url)
    except (urllib.error.URLError, OSError):
        return None
    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    for line in text.splitlines():
        m = _INSTRUCTOR_LABEL.match(line.strip())
        if not m:
            continue
        people = expand_instructors(m.group(1)[:120])
        if people:
            return people[0]
    return None


def instructor_from_db(course_url: str, code_raw: str | None) -> str | None:
    """Look for a previously-extracted instructor in course_instructors.db."""
    if not Path(INSTRUCTOR_DB).exists():
        return None
    host = ""
    try:
        host = urllib.parse.urlsplit(course_url).netloc.lower()
    except Exception:
        host = ""
    try:
        conn = sqlite3.connect(INSTRUCTOR_DB)
        rows = conn.execute(
            "SELECT course_name, course_url, instructor_name FROM courses "
            "WHERE instructor_name IS NOT NULL AND instructor_name != ''"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    code = (code_raw or "").lower()
    for name, c_url, instr in rows:
        blob = f"{name} {c_url}".lower()
        if code and code in blob and (not host or host.split('.')[0] in blob):
            people = expand_instructors(instr)
            if people:
                return people[0]
    return None


# --------------------------------------------------------------------------- #
# Slides (optional, delegates to the slidefetch CLI)
# --------------------------------------------------------------------------- #
def download_slides(url: str) -> tuple[str | None, int]:
    """Run slidefetch on the URL; return (output_dir, file_count)."""
    if sf_urls is None:
        return None, 0
    slug = sf_urls.page_slug(url)
    out_dir = Path("downloads") / "new" / slug
    try:
        subprocess.run(
            [sys.executable, "-m", "slidefetch.cli", url],
            check=False, timeout=900,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"[slides] slidefetch failed: {exc}")
    count = sum(1 for _ in out_dir.glob("*")) if out_dir.is_dir() else 0
    return (str(out_dir) if out_dir.is_dir() else None), count


# --------------------------------------------------------------------------- #
# Search-mode helper: pick a university course page for a query
# --------------------------------------------------------------------------- #
def first_course_url(query: str) -> str | None:
    if web_search is None:
        print("[search] slidefetch.search unavailable")
        return None
    hits = web_search(query, limit=10, cache_dir="downloads/.search_cache")
    for hit in hits:
        ok = True
        if sf_urls is not None:
            ok, _ = sf_urls.is_university(hit.url)
        if ok:
            return hit.url
    return None


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def run(url: str, *, query: str | None = None, instructor: str | None = None,
        do_slides: bool = False, delay: float = 0.5,
        conn: sqlite3.Connection | None = None) -> dict:
    own_conn = conn is None
    conn = conn or open_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    review: list[tuple[str, str, str]] = []   # (stage, reason, details)

    school = resolve_school(url)
    code = extract_course_code(url)
    host = ""
    if sf_urls is not None:
        host = sf_urls.host_of(url)

    school_status = "resolved" if school else "unknown"
    if not school:
        review.append(("school", "unknown_host",
                       f"No school mapping for host '{host or url}'. "
                       f"Add it to school_map.SCHOOLS."))
    code_raw = code.raw if code else None
    disciplines = ",".join(sorted(code.disciplines)) if code else ""

    ccr_slug = school.ccr_slug if school else None
    rmp_keyword = school.rmp_keyword if school else ""
    course_key = f"{(ccr_slug or host or 'unknown')}:{code_raw or 'unknown'}"

    if code is None:
        review.append(("course", "no_code", f"Could not parse a course code from {url}"))

    # ---- slides ---------------------------------------------------------- #
    slides_dir, slides_count = (None, 0)
    if do_slides:
        slides_dir, slides_count = download_slides(url)

    # ---- instructor ------------------------------------------------------ #
    instr_source = "arg"
    if not instructor:
        instructor = scrape_instructor(url)
        instr_source = "page" if instructor else instr_source
    if not instructor:
        instructor = instructor_from_db(url, code_raw)
        instr_source = "db" if instructor else instr_source
    if not instructor:
        instr_source = None
        review.append(("instructor", "not_found",
                       "No instructor from arg/page/db; pass --instructor."))

    # ---- RMP ------------------------------------------------------------- #
    rmp: dict = {}
    if instructor:
        expected = {rmp_keyword} if rmp_keyword else set()
        try:
            rmp = check_one(instructor, expected, delay)
        except Exception as exc:  # noqa: BLE001 - network errors shouldn't abort
            rmp = {"verdict": "error", "error": str(exc)}
            review.append(("rmp", "error", str(exc)))
        verdict = rmp.get("verdict")
        if verdict in {"name_collision", "review"}:
            review.append(("rmp", verdict,
                           f"{instructor} -> matched '{rmp.get('matched_name')}' "
                           f"at '{rmp.get('school')}' (expected {rmp_keyword!r})."))
        elif verdict == "not_found":
            review.append(("rmp", "not_found", f"No RMP profile for {instructor}."))

    # ---- CCR ------------------------------------------------------------- #
    ccr = ccr_lookup.CCRResult(status="skipped")
    if code is not None:
        ccr = ccr_lookup.lookup(ccr_slug, code)
        if ccr.status in {"no_school", "error"}:
            review.append(("ccr", ccr.status,
                           ccr.error or f"No verified CCR slug for {school_status} school."))
    m = ccr.metrics

    needs_review = 1 if review else 0
    row = {
        "course_key": course_key, "query": query, "course_url": url, "host": host,
        "school_canonical": school.canonical if school else None,
        "school_status": school_status,
        "course_code": code_raw, "disciplines": disciplines,
        "slides_dir": slides_dir, "slides_count": slides_count,
        "instructor": instructor, "instructor_source": instr_source,
        "rmp_status": rmp.get("verdict"), "rmp_matched_name": rmp.get("matched_name"),
        "rmp_school": rmp.get("school"), "rmp_profile_url": rmp.get("profile_url"),
        "rmp_avg_rating": rmp.get("avg_rating"),
        "rmp_avg_difficulty": rmp.get("avg_difficulty"),
        "rmp_num_ratings": rmp.get("num_ratings"),
        "rmp_would_take_again": rmp.get("would_take_again"),
        "ccr_status": ccr.status, "ccr_url": ccr.url, "ccr_code": ccr.code_used,
        "ccr_star_rating": m.get("star_rating"), "ccr_num_reviews": m.get("num_reviews"),
        "ccr_student_satisfaction": m.get("student_satisfaction"),
        "ccr_challenge_level": m.get("challenge_level"),
        "ccr_grade_accessibility": m.get("grade_accessibility"),
        "ccr_time_investment": m.get("time_investment"),
        "ccr_attendance_importance": m.get("attendance_importance"),
        "ccr_recommendation_rate": m.get("recommendation_rate"),
        "needs_review": needs_review,
        "review_reasons": "; ".join(f"{s}:{r}" for s, r, _ in review) or None,
        "created_at": now, "updated_at": now,
    }

    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO course_pipeline ({cols}) VALUES ({', '.join('?' for _ in row)}) "
        f"ON CONFLICT(course_key) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in row if c not in ("course_key", "created_at")),
        list(row.values()),
    )
    conn.execute("DELETE FROM review_queue WHERE course_key=? AND resolved=0", (course_key,))
    for stage, reason, details in review:
        conn.execute(
            "INSERT INTO review_queue (course_key, stage, reason, details, created_at) "
            "VALUES (?,?,?,?,?)",
            (course_key, stage, reason, details, now),
        )
    conn.commit()
    if own_conn:
        conn.close()
    return row


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_summary(row: dict) -> None:
    print(f"\n=== {row['course_key']} ===")
    print(f"  school : {row['school_canonical']} ({row['school_status']})")
    print(f"  code   : {row['course_code']}  [{row['disciplines']}]")
    if row["slides_dir"]:
        print(f"  slides : {row['slides_count']} files -> {row['slides_dir']}")
    print(f"  instr  : {row['instructor']} ({row['instructor_source']})")
    print(f"  RMP    : {row['rmp_status']} | {row['rmp_matched_name']} @ {row['rmp_school']} "
          f"| rating={row['rmp_avg_rating']} diff={row['rmp_avg_difficulty']} "
          f"n={row['rmp_num_ratings']}")
    print(f"           {row['rmp_profile_url']}")
    print(f"  CCR    : {row['ccr_status']} | code={row['ccr_code']} "
          f"stars={row['ccr_star_rating']} reviews={row['ccr_num_reviews']}")
    if row["ccr_url"]:
        print(f"           {row['ccr_url']}")
    if row["needs_review"]:
        print(f"  REVIEW : {row['review_reasons']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="End-to-end course rating pipeline.")
    ap.add_argument("url", nargs="?", help="course page URL")
    ap.add_argument("--search", help="search term; uses the first university result")
    ap.add_argument("--instructor", help="instructor name (skips auto-detection)")
    ap.add_argument("--slides", action="store_true", help="also download slide decks")
    ap.add_argument("--delay", type=float, default=0.5, help="RMP request delay (s)")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args(argv)

    url = args.url
    query = args.search or args.url
    if not url and args.search:
        url = first_course_url(args.search)
        if not url:
            print("No university course page found for that search.")
            return 2
        print(f"[search] using: {url}")
    if not url:
        ap.error("provide a URL or --search")

    conn = open_db(args.db)
    row = run(url, query=query, instructor=args.instructor,
              do_slides=args.slides, delay=args.delay, conn=conn)
    conn.close()
    _print_summary(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
