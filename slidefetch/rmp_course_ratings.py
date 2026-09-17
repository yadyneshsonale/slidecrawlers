#!/usr/bin/env python3
"""
rmp_course_ratings.py — Collect course-wise quality & difficulty for the
professors confirmed on Rate My Professors.

Reads the confirmed professors (verdict='on_rmp') from rmp_results.db, then for
each one pulls every individual student rating from the RMP GraphQL API and
groups them by course. Each course may have 1..N ratings, so per course we
report the rating count plus the average quality and average difficulty.

Outputs:
  - rmp_course_ratings.db
        prof_ratings    : one row per individual student rating
        course_summary  : one row per (professor, course) aggregate
  - rmp_course_ratings.csv : the raw per-rating table
  - rmp_course_summary.csv : the per-course aggregate (the main deliverable)

Only the Python standard library is used. Resumable: professors already fetched
are skipped unless --no-resume.

Examples:
    python3 rmp_course_ratings.py
    python3 rmp_course_ratings.py --limit 5
    python3 rmp_course_ratings.py --include-weak    # also fetch surname-only hits
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_PROFS_DB = "rmp_results.db"
DEFAULT_OUT_DB = "rmp_course_ratings.db"
DEFAULT_RATINGS_CSV = "rmp_course_ratings.csv"
DEFAULT_SUMMARY_CSV = "rmp_course_summary.csv"

GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
BASE_HEADERS = {
    "Authorization": "Basic dGVzdDp0ZXN0",  # test:test — same token the site ships
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.ratemyprofessors.com",
    "Referer": "https://www.ratemyprofessors.com/",
}

PAGE_SIZE = 20

RATINGS_QUERY = """
query Ratings($id: ID!, $count: Int!, $cursor: String) {
  node(id: $id) {
    ... on Teacher {
      numRatings
      ratings(first: $count, after: $cursor) {
        edges {
          node {
            id
            class
            qualityRating
            clarityRating
            helpfulRating
            difficultyRating
            date
            wouldTakeAgain
            grade
            comment
            ratingTags
            thumbsUpTotal
            thumbsDownTotal
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


def node_id(legacy_id: str) -> str:
    """Encode a numeric RMP professor ID as a Relay node ID."""
    return base64.b64encode(f"Teacher-{legacy_id}".encode()).decode()


def gql(query: str, variables: dict, retries: int = 3) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    last_err = "unknown error"
    for attempt in range(retries):
        req = urllib.request.Request(
            GRAPHQL_URL, data=payload, headers=BASE_HEADERS, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            time.sleep((3 if e.code in (429, 503) else 1) * (attempt + 1))
        except Exception as e:  # noqa: BLE001 - network/JSON errors are retried
            last_err = str(e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_err)


def _wta(value) -> int:
    """Normalize wouldTakeAgain to 1=yes, 0=no, -1=N/A."""
    if value == 1:
        return 1
    if value == 0:
        return 0
    return -1


def parse_rating(node: dict, legacy_id: str, prof_name: str, school: str) -> dict:
    tags = node.get("ratingTags") or ""
    if isinstance(tags, list):
        tags = ", ".join(tags)
    course = (node.get("class") or "").strip() or None
    return {
        "rating_id": node.get("id"),
        "legacy_id": legacy_id,
        "prof_name": prof_name,
        "school": school,
        "course": course,
        "quality": node.get("qualityRating"),
        "clarity": node.get("clarityRating"),
        "helpful": node.get("helpfulRating"),
        "difficulty": node.get("difficultyRating"),
        "date": (node.get("date") or "")[:10] or None,
        "would_take_again": _wta(node.get("wouldTakeAgain")),
        "grade": node.get("grade"),
        "comment": node.get("comment"),
        "tags": tags,
        "thumbs_up": node.get("thumbsUpTotal"),
        "thumbs_down": node.get("thumbsDownTotal"),
    }


def fetch_all_ratings(legacy_id: str, prof_name: str, school: str, delay: float) -> list[dict]:
    """Paginate through every rating for one professor."""
    nid = node_id(legacy_id)
    out: list[dict] = []
    cursor = None
    while True:
        data = gql(RATINGS_QUERY, {"id": nid, "count": PAGE_SIZE, "cursor": cursor})
        node = (data.get("data") or {}).get("node") or {}
        ratings = node.get("ratings")
        if not ratings:
            break
        for edge in ratings.get("edges", []):
            out.append(parse_rating(edge["node"], legacy_id, prof_name, school))
        page = ratings.get("pageInfo", {})
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
        if delay:
            time.sleep(delay)
    return out


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

RATING_COLUMNS = [
    "rating_id", "legacy_id", "prof_name", "school", "course", "quality",
    "clarity", "helpful", "difficulty", "date", "would_take_again", "grade",
    "comment", "tags", "thumbs_up", "thumbs_down",
]


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS prof_ratings (
            rating_id        TEXT PRIMARY KEY,
            legacy_id        TEXT,
            prof_name        TEXT,
            school           TEXT,
            course           TEXT,
            quality          REAL,
            clarity          REAL,
            helpful          REAL,
            difficulty       REAL,
            date             TEXT,
            would_take_again INTEGER,
            grade            TEXT,
            comment          TEXT,
            tags             TEXT,
            thumbs_up        INTEGER,
            thumbs_down      INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_ratings_prof ON prof_ratings(legacy_id);

        CREATE TABLE IF NOT EXISTS fetched_profs (
            legacy_id            TEXT PRIMARY KEY,
            prof_name            TEXT,
            school               TEXT,
            num_ratings_fetched  INTEGER,
            num_courses          INTEGER,
            fetched_at           TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS course_summary (
            legacy_id      TEXT,
            prof_name      TEXT,
            school         TEXT,
            course         TEXT,
            num_ratings    INTEGER,
            avg_quality    REAL,
            avg_difficulty REAL,
            profile_url    TEXT,
            PRIMARY KEY (legacy_id, course)
        );
        """
    )
    conn.commit()
    return conn


def save_ratings(conn: sqlite3.Connection, rows: list[dict]) -> None:
    placeholders = ", ".join(f":{c}" for c in RATING_COLUMNS)
    conn.executemany(
        f"INSERT OR REPLACE INTO prof_ratings ({', '.join(RATING_COLUMNS)}) "
        f"VALUES ({placeholders})",
        rows,
    )


def rebuild_summary(conn: sqlite3.Connection) -> int:
    """Recompute the per-(professor, course) aggregate from prof_ratings."""
    conn.execute("DELETE FROM course_summary")
    conn.execute(
        """
        INSERT INTO course_summary
            (legacy_id, prof_name, school, course, num_ratings,
             avg_quality, avg_difficulty, profile_url)
        SELECT
            legacy_id,
            MAX(prof_name),
            MAX(school),
            COALESCE(course, '(no course listed)'),
            COUNT(*),
            ROUND(AVG(quality), 2),
            ROUND(AVG(difficulty), 2),
            'https://www.ratemyprofessors.com/professor/' || legacy_id
        FROM prof_ratings
        GROUP BY legacy_id, COALESCE(course, '(no course listed)')
        """
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM course_summary").fetchone()[0]


def export_csv(conn: sqlite3.Connection, query: str, columns: list[str], path: str) -> int:
    rows = conn.execute(query).fetchall()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Professor source
# ---------------------------------------------------------------------------

def load_profs(profs_db: str, include_weak: bool) -> list[tuple]:
    """Return [(legacy_id, prof_name, school)] for professors to fetch."""
    conn = sqlite3.connect(profs_db)
    verdicts = "('on_rmp', 'review')" if include_weak else "('on_rmp')"
    rows = conn.execute(
        f"""SELECT legacy_id, matched_name, school
            FROM rmp_matches
            WHERE legacy_id IS NOT NULL AND verdict IN {verdicts}
            ORDER BY num_ratings DESC"""
    ).fetchall()
    conn.close()
    # De-duplicate on legacy_id (a person could appear under two query spellings).
    seen, out = set(), []
    for legacy_id, name, school in rows:
        if legacy_id in seen:
            continue
        seen.add(legacy_id)
        out.append((legacy_id, name, school))
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--profs-db", default=DEFAULT_PROFS_DB, help=f"DB with confirmed professors (default: {DEFAULT_PROFS_DB})")
    p.add_argument("--out-db", default=DEFAULT_OUT_DB, help=f"Output DB (default: {DEFAULT_OUT_DB})")
    p.add_argument("--ratings-csv", default=DEFAULT_RATINGS_CSV, help=f"Raw ratings CSV (default: {DEFAULT_RATINGS_CSV})")
    p.add_argument("--summary-csv", default=DEFAULT_SUMMARY_CSV, help=f"Per-course summary CSV (default: {DEFAULT_SUMMARY_CSV})")
    p.add_argument("--workers", type=int, default=4, help="Concurrent professors fetched (default: 4)")
    p.add_argument("--delay", type=float, default=0.3, help="Delay between rating pages, seconds (default: 0.3)")
    p.add_argument("--limit", type=int, help="Only fetch the first N professors")
    p.add_argument("--include-weak", action="store_true", help="Also fetch surname-only (verdict=review) matches")
    p.add_argument("--no-resume", action="store_true", help="Re-fetch everyone, ignoring fetched_profs")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    profs = load_profs(args.profs_db, args.include_weak)
    if not profs:
        print("No professors with a legacy_id found to fetch.", file=sys.stderr)
        return 1

    conn = open_db(args.out_db)
    done: set[str] = set()
    if not args.no_resume:
        done = {r[0] for r in conn.execute("SELECT legacy_id FROM fetched_profs")}

    todo = [p for p in profs if p[0] not in done]
    if args.limit:
        todo = todo[: args.limit]

    print(f"Confirmed professors: {len(profs)} | already fetched: {len(done)} | "
          f"to fetch now: {len(todo)}")
    if not todo:
        n = rebuild_summary(conn)
        export_csv(conn, f"SELECT {', '.join(RATING_COLUMNS)} FROM prof_ratings "
                   "ORDER BY prof_name, course, date DESC", RATING_COLUMNS, args.ratings_csv)
        export_csv(conn, "SELECT prof_name, school, course, num_ratings, avg_quality, "
                   "avg_difficulty, legacy_id, profile_url FROM course_summary "
                   "ORDER BY prof_name, num_ratings DESC, course",
                   ["prof_name", "school", "course", "num_ratings", "avg_quality",
                    "avg_difficulty", "legacy_id", "profile_url"], args.summary_csv)
        print(f"Nothing to fetch. course_summary has {n} rows.")
        return 0

    total_ratings = 0
    errors = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = {
            ex.submit(fetch_all_ratings, lid, name, school, args.delay): (lid, name, school)
            for lid, name, school in todo
        }
        for fut in as_completed(futures):
            lid, name, school = futures[fut]
            try:
                ratings = fut.result()
            except Exception as e:  # noqa: BLE001
                errors += 1
                completed += 1
                print(f"  [{completed}/{len(todo)}] ERROR {name} ({lid}) -> {e}")
                continue

            if ratings:
                save_ratings(conn, ratings)
            n_courses = len({r["course"] for r in ratings if r["course"]})
            conn.execute(
                "INSERT OR REPLACE INTO fetched_profs "
                "(legacy_id, prof_name, school, num_ratings_fetched, num_courses) "
                "VALUES (?, ?, ?, ?, ?)",
                (lid, name, school, len(ratings), n_courses),
            )
            conn.commit()
            total_ratings += len(ratings)
            completed += 1
            print(f"  [{completed}/{len(todo)}] {name:<28} {len(ratings):>3} ratings "
                  f"across {n_courses} course(s)")

    n_summary = rebuild_summary(conn)
    n_raw = export_csv(
        conn,
        f"SELECT {', '.join(RATING_COLUMNS)} FROM prof_ratings "
        "ORDER BY prof_name, course, date DESC",
        RATING_COLUMNS, args.ratings_csv,
    )
    export_csv(
        conn,
        "SELECT prof_name, school, course, num_ratings, avg_quality, avg_difficulty, "
        "legacy_id, profile_url FROM course_summary "
        "ORDER BY prof_name, num_ratings DESC, course",
        ["prof_name", "school", "course", "num_ratings", "avg_quality",
         "avg_difficulty", "legacy_id", "profile_url"],
        args.summary_csv,
    )

    total_profs = conn.execute("SELECT COUNT(*) FROM fetched_profs").fetchone()[0]
    conn.close()

    print("\n" + "=" * 64)
    print(f"Fetched this run : {len(todo)} profs ({total_ratings} ratings, errors {errors})")
    print(f"Course rows      : {n_summary}  (per professor+course)")
    print(f"Raw rating rows  : {n_raw}")
    print(f"Professors stored: {total_profs}")
    print(f"DB               : {args.out_db}")
    print(f"Ratings CSV      : {args.ratings_csv}")
    print(f"Summary CSV      : {args.summary_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
