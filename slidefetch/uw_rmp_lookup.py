#!/usr/bin/env python3
"""uw_rmp_lookup.py — Rate My Professors lookup for UW catalog instructors.

Reads the instructors collected by ``uw_catalog_crawler.py`` (the ``instructors``
table of ``uw_catalog.db``) and, for every distinct instructor, queries the Rate
My Professors GraphQL API **restricted to the University of Washington** to pull
that professor's quality (avg rating), difficulty, number of ratings, "would take
again" %, department and profile link.

UW offering pages list instructors by surname only (e.g. "(Jaques)"), so the RMP
search is scoped to UW's school id — within one school a surname is usually
unique. When several UW professors share a surname the Computer Science
department (and any given name) is preferred, falling back to the most-rated hit
flagged ``ambiguous``.

Results go to a *separate* ``uw_rmp.db`` (so a running crawl writing
``uw_catalog.db`` is never blocked) and a joined ``uw_rmp_by_course.csv``. The
lookup is resumable. Standard library only.

Examples:
    ./.venv/bin/python uw_rmp_lookup.py                 # look up every instructor
    ./.venv/bin/python uw_rmp_lookup.py --limit 20      # quick sample
    ./.venv/bin/python uw_rmp_lookup.py --filter '^B'   # surnames starting with B
    ./.venv/bin/python uw_rmp_lookup.py --refresh       # re-query everyone
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DEFAULT_CATALOG_DB = "uw_catalog.db"
DEFAULT_OUT_DB = "uw_rmp.db"
DEFAULT_CSV = "uw_rmp_by_course.csv"

GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
HEADERS = {
    "Authorization": "Basic dGVzdDp0ZXN0",  # test:test — the token the site ships
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.ratemyprofessors.com",
    "Referer": "https://www.ratemyprofessors.com/",
}

# RMP school ids (base64 GraphQL node ids). CSE lives at the Seattle campus.
UW_CAMPUSES = {
    "seattle": ("U2Nob29sLTE1MzA=", "University of Washington (Seattle)"),
    "bothell": ("U2Nob29sLTQ0NjY=", "University of Washington (Bothell)"),
    "tacoma": ("U2Nob29sLTQ3NDQ=", "University of Washington (Tacoma)"),
}

TEACHER_QUERY = """
query TeacherSearch($q: TeacherSearchQuery!, $first: Int!) {
  newSearch {
    teachers(query: $q, first: $first) {
      edges {
        node {
          legacyId
          firstName
          lastName
          avgRating
          avgDifficulty
          numRatings
          wouldTakeAgainPercent
          department
          school { name city state }
        }
      }
    }
  }
}
"""

PROFILE_URL = "https://www.ratemyprofessors.com/professor/{}"


# --------------------------------------------------------------------------- #
# RMP GraphQL search
# --------------------------------------------------------------------------- #
def gql_search(text: str, school_id: str, first: int = 20, retries: int = 3) -> list[dict]:
    """Search RMP for ``text`` within one school; return teacher nodes."""
    payload = json.dumps(
        {"query": TEACHER_QUERY,
         "variables": {"q": {"text": text, "schoolID": school_id}, "first": first}}
    ).encode()
    last_err = "unknown error"
    for attempt in range(retries):
        req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode())
            teachers = ((data.get("data") or {}).get("newSearch") or {}).get("teachers")
            if teachers is None:
                last_err = "teachers=None"
                time.sleep(1.5 * (attempt + 1))
                continue
            return [edge["node"] for edge in teachers.get("edges", [])]
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            time.sleep((3 if e.code in (429, 503) else 1) * (attempt + 1))
        except Exception as e:  # noqa: BLE001 — network/JSON errors are retried
            last_err = str(e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_err)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def _tokens(name: str) -> list[str]:
    return [t for t in re.sub(r"[.\-']", " ", (name or "").lower()).split() if t]


def pick_match(query: str, nodes: list[dict]) -> tuple[dict | None, int, str]:
    """Choose the best UW professor for ``query``.

    Returns ``(node | None, num_surname_candidates, match_type)`` where
    ``match_type`` is one of ``unique`` / ``given`` / ``cs-dept`` / ``ambiguous``
    / ``not_found``.
    """
    toks = _tokens(query)
    if not toks:
        return None, 0, "not_found"
    surname = toks[-1]
    given = toks[0] if len(toks) > 1 else None

    exact = [n for n in nodes if (n.get("lastName") or "").lower() == surname]
    if not exact:
        return None, 0, "not_found"
    if len(exact) == 1:
        return exact[0], 1, "unique"

    pool = exact
    # Narrow by given name / initial when the catalog supplied one.
    if given:
        if len(given) == 1:
            narrowed = [n for n in exact if (n.get("firstName") or "").lower().startswith(given)]
        else:
            narrowed = [n for n in exact if (n.get("firstName") or "").lower() == given]
        if narrowed:
            pool = narrowed
            if len(pool) == 1:
                return pool[0], len(exact), "given"

    # Prefer the Computer Science department (these are all CSE courses).
    cs = [n for n in pool if "computer" in (n.get("department") or "").lower()]
    if len(cs) == 1:
        return cs[0], len(exact), "cs-dept"
    if len(cs) >= 2:  # several CS namesakes — pick most-rated, flag for review
        best = max(cs, key=lambda n: n.get("numRatings") or 0)
        return best, len(exact), "ambiguous"

    # Multiple namesakes and none in Computer Science: the CSE instructor almost
    # certainly has no RMP page, so any non-CS namesake would be a false match.
    return None, len(exact), "no_cs_match"


def lookup_instructor(name: str, school_id: str, campus_label: str, delay: float) -> dict:
    """Search + disambiguate one instructor; return a result row dict."""
    if delay:
        time.sleep(delay)
    row: dict = {"name": name, "campus": campus_label}
    try:
        nodes = gql_search(name, school_id)
    except Exception as err:  # noqa: BLE001
        row.update(status="error", error=str(err), match_type="error")
        return row

    node, n_cand, match_type = pick_match(name, nodes)
    row["num_uw_candidates"] = n_cand
    row["match_type"] = match_type
    if node is None:
        row.update(status="not_found", error="")
        return row

    legacy = node.get("legacyId")
    row.update(
        status="found",
        error="",
        legacy_id=legacy,
        matched_name=f"{node.get('firstName', '')} {node.get('lastName', '')}".strip(),
        department=node.get("department"),
        school=(node.get("school") or {}).get("name"),
        avg_quality=node.get("avgRating"),
        avg_difficulty=node.get("avgDifficulty"),
        num_ratings=node.get("numRatings"),
        would_take_again=node.get("wouldTakeAgainPercent"),
        profile_url=PROFILE_URL.format(legacy) if legacy else None,
    )
    return row


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def open_out_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rmp_professors (
            name              TEXT PRIMARY KEY,   -- instructor name as in uw_catalog
            legacy_id         INTEGER,
            matched_name      TEXT,
            department        TEXT,
            school            TEXT,
            campus            TEXT,
            avg_quality       REAL,               -- RMP avgRating (1-5)
            avg_difficulty    REAL,               -- RMP avgDifficulty (1-5)
            num_ratings       INTEGER,
            would_take_again  REAL,               -- percent
            profile_url       TEXT,
            num_uw_candidates INTEGER,            -- # surname hits at UW
            match_type        TEXT,               -- unique|given|cs-dept|ambiguous|not_found|error
            status            TEXT NOT NULL,       -- found|not_found|error
            error             TEXT,
            updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_rmp_status ON rmp_professors(status);
        """
    )
    conn.commit()
    return conn


def upsert_professor(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO rmp_professors
            (name, legacy_id, matched_name, department, school, campus,
             avg_quality, avg_difficulty, num_ratings, would_take_again,
             profile_url, num_uw_candidates, match_type, status, error, updated_at)
        VALUES
            (:name, :legacy_id, :matched_name, :department, :school, :campus,
             :avg_quality, :avg_difficulty, :num_ratings, :would_take_again,
             :profile_url, :num_uw_candidates, :match_type, :status, :error,
             datetime('now'))
        ON CONFLICT(name) DO UPDATE SET
            legacy_id=excluded.legacy_id, matched_name=excluded.matched_name,
            department=excluded.department, school=excluded.school,
            campus=excluded.campus, avg_quality=excluded.avg_quality,
            avg_difficulty=excluded.avg_difficulty, num_ratings=excluded.num_ratings,
            would_take_again=excluded.would_take_again, profile_url=excluded.profile_url,
            num_uw_candidates=excluded.num_uw_candidates, match_type=excluded.match_type,
            status=excluded.status, error=excluded.error, updated_at=datetime('now')
        """,
        {
            "name": row.get("name"),
            "legacy_id": row.get("legacy_id"),
            "matched_name": row.get("matched_name"),
            "department": row.get("department"),
            "school": row.get("school"),
            "campus": row.get("campus"),
            "avg_quality": row.get("avg_quality"),
            "avg_difficulty": row.get("avg_difficulty"),
            "num_ratings": row.get("num_ratings"),
            "would_take_again": row.get("would_take_again"),
            "profile_url": row.get("profile_url"),
            "num_uw_candidates": row.get("num_uw_candidates"),
            "match_type": row.get("match_type"),
            "status": row.get("status"),
            "error": row.get("error"),
        },
    )


def read_instructors(catalog_db: str | Path) -> list[str]:
    """Distinct instructor names from the catalog DB (read-only, crawl-safe)."""
    uri = f"file:{Path(catalog_db).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT DISTINCT name FROM instructors WHERE name IS NOT NULL "
            "AND TRIM(name) <> '' ORDER BY name"
        )]
    finally:
        conn.close()
    return names


def already_done(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM rmp_professors WHERE status IN ('found', 'not_found')"
    )}


# --------------------------------------------------------------------------- #
# Per-course CSV export (join catalog instructors x rmp results)
# --------------------------------------------------------------------------- #
def export_by_course(out_db: str | Path, catalog_db: str | Path, csv_path: str | Path) -> int:
    conn = sqlite3.connect(out_db, timeout=30)
    try:
        conn.execute("ATTACH DATABASE ? AS cat", (str(Path(catalog_db).resolve()),))
        rows = conn.execute(
            """
            SELECT c.code, c.title, c.category, i.term, i.name AS instructor,
                   r.matched_name, r.department, r.avg_quality, r.avg_difficulty,
                   r.num_ratings, r.would_take_again, r.match_type, r.profile_url
            FROM cat.instructors i
            JOIN cat.courses c ON c.code = i.course_code
            LEFT JOIN main.rmp_professors r ON r.name = i.name
            ORDER BY c.code, i.term, i.name
            """
        ).fetchall()
    finally:
        conn.close()

    header = [
        "course_code", "course_title", "category", "term", "instructor",
        "rmp_name", "department", "quality", "difficulty", "num_ratings",
        "would_take_again_pct", "match_type", "profile_url",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return len(rows)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="uw_rmp_lookup", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog-db", default=DEFAULT_CATALOG_DB,
                   help=f"crawler DB to read instructors from (default: {DEFAULT_CATALOG_DB})")
    p.add_argument("--out-db", default=DEFAULT_OUT_DB,
                   help=f"RMP results DB (default: {DEFAULT_OUT_DB})")
    p.add_argument("--csv", default=DEFAULT_CSV,
                   help=f"per-course CSV export (default: {DEFAULT_CSV})")
    p.add_argument("--campus", choices=sorted(UW_CAMPUSES), default="seattle",
                   help="UW campus to search on RMP (default: seattle)")
    p.add_argument("--filter", default=None, metavar="REGEX",
                   help="only look up instructor names matching this regex (case-insensitive)")
    p.add_argument("--limit", type=int, default=0, help="cap number of instructors (0 = all)")
    p.add_argument("--workers", type=int, default=4, help="concurrent requests (default: 4)")
    p.add_argument("--delay", type=float, default=0.3,
                   help="per-request delay in seconds (default: 0.3)")
    p.add_argument("--refresh", action="store_true",
                   help="re-query instructors already looked up")
    p.add_argument("--no-resume", action="store_true",
                   help="alias for --refresh (ignore cached results)")
    p.add_argument("--export-only", action="store_true",
                   help="skip lookups; just rebuild the per-course CSV")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    school_id, campus_label = UW_CAMPUSES[args.campus]

    if not Path(args.catalog_db).exists():
        print(f"ERROR: catalog DB not found: {args.catalog_db}", file=sys.stderr)
        return 1

    conn = open_out_db(args.out_db)

    if args.export_only:
        n = export_by_course(args.out_db, args.catalog_db, args.csv)
        conn.close()
        print(f"wrote {n} course-instructor rows -> {args.csv}")
        return 0

    names = read_instructors(args.catalog_db)
    if args.filter:
        pat = re.compile(args.filter, re.IGNORECASE)
        names = [n for n in names if pat.search(n)]

    done = set() if (args.refresh or args.no_resume) else already_done(conn)
    todo = [n for n in names if n not in done]
    if args.limit:
        todo = todo[: args.limit]

    print(f"{len(names)} distinct instructor(s); {len(done)} cached; "
          f"{len(todo)} to look up on {campus_label}")

    found = notfound = errors = 0
    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(lookup_instructor, name, school_id, campus_label, args.delay): name
                for name in todo
            }
            for i, fut in enumerate(as_completed(futures), start=1):
                name = futures[fut]
                try:
                    row = fut.result()
                except Exception as err:  # noqa: BLE001
                    row = {"name": name, "campus": campus_label, "status": "error",
                           "error": str(err), "match_type": "error"}
                upsert_professor(conn, row)
                if i % 20 == 0:
                    conn.commit()
                status = row.get("status")
                if status == "found":
                    found += 1
                    q, d, nr = row.get("avg_quality"), row.get("avg_difficulty"), row.get("num_ratings")
                    flag = "  [!ambiguous]" if row.get("match_type") == "ambiguous" else ""
                    print(f"  [{i}/{len(todo)}] {name:22} -> {row.get('matched_name','?'):24} "
                          f"q={q} d={d} n={nr}{flag}")
                elif status == "not_found":
                    notfound += 1
                    print(f"  [{i}/{len(todo)}] {name:22} -> not found at UW")
                else:
                    errors += 1
                    print(f"  [{i}/{len(todo)}] {name:22} -> ERROR {row.get('error')}")
        conn.commit()

    # Totals across the whole DB (not just this run).
    tot_found = conn.execute("SELECT COUNT(*) FROM rmp_professors WHERE status='found'").fetchone()[0]
    tot_amb = conn.execute("SELECT COUNT(*) FROM rmp_professors WHERE match_type='ambiguous'").fetchone()[0]
    conn.close()

    n_csv = export_by_course(args.out_db, args.catalog_db, args.csv)
    print(f"\nthis run: found {found}  not-found {notfound}  errors {errors}")
    print(f"DB total: {tot_found} matched professor(s) ({tot_amb} ambiguous) in {args.out_db}")
    print(f"per-course rows: {n_csv} -> {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
