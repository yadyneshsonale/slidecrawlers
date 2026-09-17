#!/usr/bin/env python3
"""Scrape professor names + university names from RateMyProfessors into SQLite.

RMP exposes a public GraphQL endpoint. A global teacher listing is capped at
10,000 rows, so full coverage is obtained by walking schools one at a time:
every school's GraphQL id is simply base64("School-<legacyId>"), and an
empty-text teacher search scoped to a schoolID returns that school's whole
roster (paginated). Non-existent school ids return resultCount == 0.

The scraper is polite (rate-limited), retries transient errors, and is fully
resumable: each school's status is recorded, so re-running skips finished ones.

Usage:
    python scrape.py                     # resume, walk schools from id 1 upward
    python scrape.py --start 1 --end 40000
    python scrape.py --sleep 0.6 --page-size 1000
    python scrape.py --stats             # print DB summary and exit

Be considerate: RMP's Terms of Service restrict automated access. Keep the
request rate low and only collect the public name/university data you need.
"""

from __future__ import annotations

import argparse
import base64
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
HEADERS = {
    "Authorization": "Basic dGVzdDp0ZXN0",  # public "test:test" token the site ships
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.ratemyprofessors.com",
    "Referer": "https://www.ratemyprofessors.com/",
}

TEACHERS_QUERY = """
query($q: TeacherSearchQuery!, $first: Int!, $after: String) {
  newSearch {
    teachers(query: $q, first: $first, after: $after) {
      resultCount
      edges {
        node {
          id legacyId firstName lastName department numRatings
          school { legacyId name city state }
          courseCodes { courseName courseCount }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

DEFAULT_DB = Path(__file__).with_name("rmp_professors.db")
# RMP's relay/offset cursor refuses to page past ~10k rows for one query.
OFFSET_CAP = 10000


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def school_gid(legacy_id: int) -> str:
    """RMP GraphQL id for a school, e.g. 1306 -> base64('School-1306')."""
    return base64.b64encode(f"School-{legacy_id}".encode()).decode()


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def gql(session: requests.Session, variables: dict, retries: int = 4) -> dict:
    last_err = "unknown error"
    for attempt in range(retries):
        try:
            r = session.post(
                GRAPHQL_URL, headers=HEADERS,
                json={"query": TEACHERS_QUERY, "variables": variables}, timeout=30,
            )
            if r.status_code in (429, 502, 503, 504):
                last_err = f"HTTP {r.status_code}"
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — network/JSON errors are retried
            last_err = str(e)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GraphQL request failed after {retries} tries: {last_err}")


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schools (
            legacy_id      INTEGER PRIMARY KEY,
            school_id      TEXT,
            name           TEXT,
            city           TEXT,
            state          TEXT,
            num_professors INTEGER,
            status         TEXT,          -- 'done' | 'empty' | 'error'
            scraped_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS professors (
            id               TEXT PRIMARY KEY,   -- GraphQL global id
            legacy_id        INTEGER,
            first_name       TEXT,
            last_name        TEXT,
            full_name        TEXT,
            department       TEXT,
            num_ratings      INTEGER,
            school_legacy_id INTEGER,
            school_name      TEXT,
            school_city      TEXT,
            school_state     TEXT,
            scraped_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS professor_courses (
            professor_id        TEXT,
            professor_legacy_id INTEGER,
            course_name         TEXT,
            course_count        INTEGER,
            scraped_at          TEXT,
            PRIMARY KEY (professor_id, course_name)
        );

        CREATE INDEX IF NOT EXISTS idx_prof_school ON professors(school_legacy_id);
        CREATE INDEX IF NOT EXISTS idx_prof_last   ON professors(last_name);
        CREATE INDEX IF NOT EXISTS idx_prof_name   ON professors(full_name);
        CREATE INDEX IF NOT EXISTS idx_course_prof ON professor_courses(professor_id);
        CREATE INDEX IF NOT EXISTS idx_course_name ON professor_courses(course_name);
        """
    )
    conn.commit()
    return conn


def done_schools(conn: sqlite3.Connection) -> set[int]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT legacy_id FROM schools WHERE status IN ('done','empty')"
        )
    }


def upsert_professors(conn: sqlite3.Connection, nodes: list[dict]) -> None:
    ts = now()
    rows = []
    course_rows = []
    for n in nodes:
        sch = n.get("school") or {}
        first = n.get("firstName") or ""
        last = n.get("lastName") or ""
        for cc in (n.get("courseCodes") or []):
            cname = cc.get("courseName")
            if cname:
                course_rows.append(
                    (n.get("id"), n.get("legacyId"), cname,
                     cc.get("courseCount"), ts)
                )
        rows.append((
            n.get("id"),
            n.get("legacyId"),
            first,
            last,
            f"{first} {last}".strip(),
            n.get("department"),
            n.get("numRatings"),
            sch.get("legacyId"),
            sch.get("name"),
            sch.get("city"),
            sch.get("state"),
            ts,
        ))
    conn.executemany(
        """INSERT INTO professors
             (id, legacy_id, first_name, last_name, full_name, department,
              num_ratings, school_legacy_id, school_name, school_city,
              school_state, scraped_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             first_name=excluded.first_name, last_name=excluded.last_name,
             full_name=excluded.full_name, department=excluded.department,
             num_ratings=excluded.num_ratings,
             school_legacy_id=excluded.school_legacy_id,
             school_name=excluded.school_name, school_city=excluded.school_city,
             school_state=excluded.school_state, scraped_at=excluded.scraped_at""",
        rows,
    )
    if course_rows:
        conn.executemany(
            """INSERT INTO professor_courses
                 (professor_id, professor_legacy_id, course_name, course_count, scraped_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(professor_id, course_name) DO UPDATE SET
                 course_count=excluded.course_count, scraped_at=excluded.scraped_at""",
            course_rows,
        )


def record_school(conn: sqlite3.Connection, legacy_id: int, gid: str,
                  first_node: dict | None, count: int, status: str) -> None:
    sch = (first_node or {}).get("school") or {}
    conn.execute(
        """INSERT INTO schools
             (legacy_id, school_id, name, city, state, num_professors, status, scraped_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(legacy_id) DO UPDATE SET
             school_id=excluded.school_id, name=excluded.name, city=excluded.city,
             state=excluded.state, num_professors=excluded.num_professors,
             status=excluded.status, scraped_at=excluded.scraped_at""",
        (legacy_id, gid, sch.get("name"), sch.get("city"), sch.get("state"),
         count, status, now()),
    )


# --------------------------------------------------------------------------- #
# Scrape one school
# --------------------------------------------------------------------------- #
def scrape_school(session: requests.Session, conn: sqlite3.Connection,
                  legacy_id: int, page_size: int, sleep: float) -> tuple[int, str]:
    """Fetch every professor for one school. Returns (count, status)."""
    gid = school_gid(legacy_id)
    cursor = None
    fetched = 0
    result_count = 0
    first_node: dict | None = None

    while True:
        data = gql(session, {
            "q": {"text": "", "schoolID": gid},
            "first": page_size,
            "after": cursor,
        })
        teachers = (((data.get("data") or {}).get("newSearch") or {}).get("teachers"))
        if not teachers:
            status = "empty" if fetched == 0 else "done"
            record_school(conn, legacy_id, gid, first_node, fetched, status)
            conn.commit()
            return fetched, status

        result_count = teachers.get("resultCount") or 0
        edges = teachers.get("edges") or []
        nodes = [e["node"] for e in edges if e.get("node")]
        if nodes and first_node is None:
            first_node = nodes[0]
        if nodes:
            upsert_professors(conn, nodes)
        fetched += len(nodes)

        page = teachers.get("pageInfo") or {}
        cursor = page.get("endCursor")
        has_next = page.get("hasNextPage")
        if not has_next or not nodes or fetched >= OFFSET_CAP:
            break
        time.sleep(sleep)

    status = "empty" if fetched == 0 else "done"
    record_school(conn, legacy_id, gid, first_node, fetched, status)
    conn.commit()
    return fetched, status


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def print_stats(conn: sqlite3.Connection) -> None:
    profs = conn.execute("SELECT COUNT(*) FROM professors").fetchone()[0]
    schools = conn.execute(
        "SELECT COUNT(*) FROM schools WHERE status='done'"
    ).fetchone()[0]
    empty = conn.execute(
        "SELECT COUNT(*) FROM schools WHERE status='empty'"
    ).fetchone()[0]
    uni = conn.execute(
        "SELECT COUNT(DISTINCT school_legacy_id) FROM professors"
    ).fetchone()[0]
    print(f"professors: {profs:,}")
    print(f"universities with professors: {uni:,}")
    print(f"schools scraped (with profs): {schools:,}   empty: {empty:,}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--start", type=int, default=1, help="first school legacy id")
    ap.add_argument("--end", type=int, default=None,
                    help="last school legacy id (inclusive); default: run until "
                         "--stop-after consecutive empty schools")
    ap.add_argument("--stop-after", type=int, default=500,
                    help="stop after this many consecutive empty schools "
                         "(only when --end is not set)")
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="seconds to wait between requests")
    ap.add_argument("--stats", action="store_true", help="print DB summary and exit")
    args = ap.parse_args()

    conn = open_db(args.db)
    if args.stats:
        print_stats(conn)
        return 0

    already = done_schools(conn)
    print(f"DB: {args.db}  (already have {len(already):,} schools)", flush=True)

    legacy_id = args.start
    consecutive_empty = 0
    total_new = 0
    try:
        while True:
            if args.end is not None and legacy_id > args.end:
                break
            if args.end is None and consecutive_empty >= args.stop_after:
                print(f"Stopping: {consecutive_empty} consecutive empty schools.",
                      flush=True)
                break

            if legacy_id in already:
                legacy_id += 1
                continue

            try:
                count, status = scrape_school(
                    session=SESSION, conn=conn, legacy_id=legacy_id,
                    page_size=args.page_size, sleep=args.sleep,
                )
            except Exception as e:  # noqa: BLE001 — log, mark, keep going
                print(f"school {legacy_id}: ERROR {e}", flush=True)
                conn.execute(
                    """INSERT INTO schools (legacy_id, school_id, status, scraped_at)
                       VALUES (?,?, 'error', ?)
                       ON CONFLICT(legacy_id) DO UPDATE SET
                         status='error', scraped_at=excluded.scraped_at""",
                    (legacy_id, school_gid(legacy_id), now()),
                )
                conn.commit()
                consecutive_empty = 0
                legacy_id += 1
                time.sleep(args.sleep)
                continue

            if status == "empty":
                consecutive_empty += 1
            else:
                consecutive_empty = 0
                total_new += count
                name = conn.execute(
                    "SELECT name FROM schools WHERE legacy_id=?", (legacy_id,)
                ).fetchone()[0]
                print(f"school {legacy_id}: +{count} profs  {name}", flush=True)

            legacy_id += 1
            time.sleep(args.sleep)
    except KeyboardInterrupt:
        print("\nInterrupted — progress saved, safe to resume.", flush=True)

    print(f"\nDone. Added ~{total_new:,} professor rows this run.", flush=True)
    print_stats(conn)
    return 0


SESSION = requests.Session()

if __name__ == "__main__":
    sys.exit(main())
