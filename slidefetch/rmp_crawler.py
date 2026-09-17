#!/usr/bin/env python3
"""
rmp_crawler.py — Check course instructors against Rate My Professors.

Reads instructor names (default: the `instructor_name` column of
slidefetch/course_instructors.db), expands multi-instructor / titled cells into
individual people, then queries the Rate My Professors GraphQL search API to
determine which instructors actually have an RMP page.

Because RMP search is fuzzy (a query for "Tuomas Sandholm" returns unrelated
"Tuomas ..." first-name matches), every candidate returned by the API is
verified: the queried surname must appear in the candidate's name and the given
name (or its initial) must match before an instructor is counted as "present".

Results are cached in rmp_results.db and exported to rmp_results.csv so the crawl
can be resumed. Only the Python standard library is used.

Examples:
    python3 rmp_crawler.py                       # crawl all instructors in the DB
    python3 rmp_crawler.py --limit 20            # quick sample
    python3 rmp_crawler.py --name "Jure Leskovec"
    python3 rmp_crawler.py --names-file names.txt
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DB = "course_instructors.db"
DEFAULT_OUT_DB = "rmp_results.db"
DEFAULT_CSV = "rmp_results.csv"

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

SEARCH_QUERY = """
query InstructorSearch($q: TeacherSearchQuery!, $first: Int!) {
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

# ---------------------------------------------------------------------------
# Name normalization / instructor-cell expansion
# ---------------------------------------------------------------------------

TITLE_RE = re.compile(r"^\s*(prof|professor|dr|mr|mrs|ms|miss|sir|mx|rev)\.?\s+", re.I)
PAREN_RE = re.compile(r"\([^)]*\)")
# Separators between co-instructors in a single cell.
SPLIT_RE = re.compile(r"\s*(?:&|\||/|;|\+|\band\b)\s*", re.I)

# Tokens that are honorific suffixes rather than part of a name.
SUFFIX_TOKENS = {
    "jr", "sr", "ii", "iii", "iv", "md", "phd", "dphil", "msc",
    "do", "esq", "emeritus", "emerita",
}
# Words that signal a cell is not a personal name (committees, depts, etc.).
STOPWORDS = {
    "faculty", "department", "staff", "contributors", "libraries", "lecturers",
    "technical", "group", "services", "no", "tba", "tbd", "various", "team",
    "instructor", "instructors", "and", "others", "et", "al",
}

_TOKEN_RE = re.compile(r"[A-Za-z\u00C0-\u017F][A-Za-z\u00C0-\u017F.'\-]*")


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def clean_person(piece: str) -> str | None:
    """Clean a single name fragment into 'First [Middle] Last' or return None."""
    s = PAREN_RE.sub(" ", piece or "")
    s = s.replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip(" ,.-\t")

    # Strip leading honorifics, possibly stacked (e.g. "Prof. Dr.").
    while True:
        new = TITLE_RE.sub("", s)
        if new == s:
            break
        s = new
    s = s.strip(" ,.-")
    if not s:
        return None

    tokens = s.split()
    # Drop trailing honorific suffixes.
    while tokens and tokens[-1].strip(".,").lower() in SUFFIX_TOKENS:
        tokens.pop()
    if not tokens:
        return None

    # Reject obvious non-person fragments.
    if any(t.strip(".,").lower() in STOPWORDS for t in tokens):
        return None
    if any(ch.isdigit() for ch in s):
        return None
    if not (2 <= len(tokens) <= 5):
        return None
    # Every token must look like a name part (allows initials, hyphens, accents).
    if not all(_TOKEN_RE.fullmatch(t) for t in tokens):
        return None

    return " ".join(tokens)


def expand_instructors(cell: str | None) -> list[str]:
    """Expand a raw instructor_name cell into a list of cleaned person names."""
    if not cell or not cell.strip():
        return []
    people: list[str] = []
    for piece in SPLIT_RE.split(cell):
        # Commas usually separate people too, but may also fence a suffix.
        for sub in piece.split(","):
            person = clean_person(sub)
            if person:
                people.append(person)
    return people


# Map a course-URL host fragment to (keyword-in-RMP-school-name, display name).
# Used to verify that an RMP hit is at the instructor's actual university
# rather than a same-named professor elsewhere.
DOMAIN_SCHOOLS = [
    ("mit.edu",      "massachusetts institute of technology", "Massachusetts Institute of Technology"),
    ("stanford.edu", "stanford",              "Stanford University"),
    ("cornell.edu",  "cornell",               "Cornell University"),
    ("princeton.edu", "princeton",            "Princeton University"),
    ("cmu",          "carnegie mellon",       "Carnegie Mellon University"),
    ("dlsyscourse",  "carnegie mellon",       "Carnegie Mellon University"),
    ("berkeley.edu", "berkeley",              "University of California Berkeley"),
    ("cs61a.org",    "berkeley",              "University of California Berkeley"),
    ("toronto",      "toronto",               "University of Toronto"),
    ("duke.edu",     "duke",                  "Duke University"),
    ("northwestern", "northwestern",          "Northwestern University"),
    ("stonybrook",   "stony brook",           "Stony Brook University"),
    ("illinois",     "illinois",              "University of Illinois"),
    ("uwaterloo",    "waterloo",              "University of Waterloo"),
    ("ucla.edu",     "los angeles",           "UCLA"),
    ("wpi.edu",      "worcester polytechnic", "Worcester Polytechnic Institute"),
    ("iitb",         "bombay",                "IIT Bombay"),
]


def derive_school(course_url: str | None) -> tuple[str, str] | None:
    """Return (keyword, display_name) for a course URL, or None if unknown."""
    if not course_url:
        return None
    host = course_url.split("_", 1)[0].lower()
    for fragment, keyword, display in DOMAIN_SCHOOLS:
        if fragment in host:
            return keyword, display
    return None


def name_tokens(text: str) -> list[str]:
    """Lowercase, de-accented tokens with hyphens/periods treated as breaks."""
    text = strip_accents(text or "").lower()
    text = re.sub(r"[.\-']", " ", text)
    return [t for t in text.split() if t]


# Match-quality ranking: higher is a stronger, more trustworthy match.
QUALITY_RANK = {"strong": 3, "initial": 2, "surname": 1}


def match_quality(query: str, first: str, last: str) -> str | None:
    """Return 'strong' | 'initial' | 'surname' | None for a candidate name."""
    q = name_tokens(query)
    if not q:
        return None
    cand = set(name_tokens(f"{first} {last}"))
    if not cand:
        return None

    surname, given = q[-1], q[0]
    if surname not in cand:
        return None  # surname must appear — RMP fuzz-matches first names only
    if given in cand:
        return "strong"
    for tok in cand:
        if (len(given) == 1 and tok.startswith(given)) or (
            len(tok) == 1 and given.startswith(tok)
        ):
            return "initial"
    return "surname"


def school_matches(school_name: str, expected_keywords: set[str]):
    """True/False if the expected school is known, else None (no constraint)."""
    if not expected_keywords:
        return None
    s = (school_name or "").lower()
    return any(kw in s for kw in expected_keywords)


# ---------------------------------------------------------------------------
# RMP GraphQL search
# ---------------------------------------------------------------------------

def gql_search(text: str, first: int = 40, retries: int = 3) -> list[dict]:
    """Search RMP for `text`; return the list of teacher nodes (may be empty)."""
    payload = json.dumps(
        {"query": SEARCH_QUERY, "variables": {"q": {"text": text}, "first": first}}
    ).encode()
    last_err = "unknown error"
    for attempt in range(retries):
        req = urllib.request.Request(
            GRAPHQL_URL, data=payload, headers=BASE_HEADERS, method="POST"
        )
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
        except Exception as e:  # noqa: BLE001 - network/JSON errors are retried
            last_err = str(e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_err)


def best_match(query: str, nodes: list[dict], expected_schools: set[str]) -> dict | None:
    """Pick the best teacher node for `query`, preferring the expected school."""
    best, best_key = None, None
    for node in nodes:
        quality = match_quality(query, node.get("firstName") or "", node.get("lastName") or "")
        if quality is None:
            continue
        school_name = (node.get("school") or {}).get("name") or ""
        school_ok = school_matches(school_name, expected_schools)
        # Rank: right school first, then name quality, then popularity.
        key = (1 if school_ok else 0, QUALITY_RANK[quality], node.get("numRatings") or 0)
        if best_key is None or key > best_key:
            best, best_key = node, key
            best["_match_quality"] = quality
            best["_school_match"] = school_ok
    return best


def check_one(query: str, expected_schools: set[str], delay: float) -> dict:
    """Search + verify one instructor; returns a result row dict."""
    if delay:
        time.sleep(delay)
    nodes = gql_search(query)
    match = best_match(query, nodes, expected_schools)
    if match is None:
        return {"query_name": query, "present": 0, "verdict": "not_found",
                "match_quality": None, "school_match": None}

    quality = match["_match_quality"]
    school_ok = match["_school_match"]          # True / False / None (unknown)
    name_ok = QUALITY_RANK[quality] >= 2        # strong or initial
    if not name_ok:
        verdict = "review"                      # surname-only: low confidence
    elif school_ok is False:
        verdict = "name_collision"              # right name, wrong university
    else:
        verdict = "on_rmp"                      # name ok + school matches/unknown

    school = match.get("school") or {}
    legacy = match.get("legacyId")
    return {
        "query_name": query,
        "present": 1 if verdict == "on_rmp" else 0,
        "verdict": verdict,
        "match_quality": quality,
        "school_match": (1 if school_ok else 0) if school_ok is not None else None,
        "matched_name": f"{match.get('firstName','')} {match.get('lastName','')}".strip(),
        "school": school.get("name"),
        "department": match.get("department"),
        "avg_rating": match.get("avgRating"),
        "num_ratings": match.get("numRatings"),
        "would_take_again": match.get("wouldTakeAgainPercent"),
        "avg_difficulty": match.get("avgDifficulty"),
        "legacy_id": str(legacy) if legacy is not None else None,
        "profile_url": f"https://www.ratemyprofessors.com/professor/{legacy}" if legacy else None,
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

RESULT_COLUMNS = [
    "query_name", "present", "verdict", "match_quality", "matched_name",
    "school", "school_expected", "school_match", "department", "avg_rating",
    "num_ratings", "would_take_again", "avg_difficulty", "legacy_id",
    "profile_url", "source_courses",
]


def open_results_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rmp_matches (
            query_name       TEXT PRIMARY KEY,
            present          INTEGER,
            verdict          TEXT,
            match_quality    TEXT,
            matched_name     TEXT,
            school           TEXT,
            school_expected  TEXT,
            school_match     INTEGER,
            department       TEXT,
            avg_rating       REAL,
            num_ratings      INTEGER,
            would_take_again REAL,
            avg_difficulty   REAL,
            legacy_id        TEXT,
            profile_url      TEXT,
            source_courses   TEXT,
            error            TEXT,
            checked_at       TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return conn


def save_row(conn: sqlite3.Connection, row: dict) -> None:
    cols = RESULT_COLUMNS + ["error"]
    placeholders = ", ".join(f":{c}" for c in cols)
    full = {c: row.get(c) for c in cols}
    conn.execute(
        f"INSERT OR REPLACE INTO rmp_matches ({', '.join(cols)}) VALUES ({placeholders})",
        full,
    )


def export_csv(conn: sqlite3.Connection, path: str) -> int:
    rows = conn.execute(
        f"SELECT {', '.join(RESULT_COLUMNS)} FROM rmp_matches "
        "ORDER BY present DESC, num_ratings DESC, query_name"
    ).fetchall()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(RESULT_COLUMNS)
        writer.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Input sources
# ---------------------------------------------------------------------------

def load_people(args) -> "OrderedDict[str, dict]":
    """Return ordered {person: {courses:set, schools:set, school_names:set}}."""
    people: "OrderedDict[str, dict]" = OrderedDict()

    def add(person: str, course: str | None, school: tuple | None) -> None:
        info = people.setdefault(
            person, {"courses": set(), "schools": set(), "school_names": set()}
        )
        if course:
            info["courses"].add(course)
        if school:
            info["schools"].add(school[0])
            info["school_names"].add(school[1])

    if args.name:
        for person in args.name:
            for cleaned in expand_instructors(person) or [person.strip()]:
                add(cleaned, None, None)
        return people

    if args.names_file:
        with open(args.names_file, encoding="utf-8") as f:
            for line in f:
                for cleaned in expand_instructors(line.strip()):
                    add(cleaned, None, None)
        return people

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT course_name, course_url, instructor_name FROM courses "
        "WHERE instructor_name IS NOT NULL AND TRIM(instructor_name) <> ''"
    ).fetchall()
    conn.close()
    for course_name, course_url, cell in rows:
        school = derive_school(course_url)
        for person in expand_instructors(cell):
            add(person, course_name, school)
    return people


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_argument_group("input source (default: --db)")
    src.add_argument("--db", default=DEFAULT_DB, help=f"SQLite DB with a courses.instructor_name column (default: {DEFAULT_DB})")
    src.add_argument("--names-file", help="Text file with one raw instructor cell per line")
    src.add_argument("--name", action="append", help="Check a single name (repeatable)")

    out = p.add_argument_group("output")
    out.add_argument("--out-db", default=DEFAULT_OUT_DB, help=f"Results cache DB (default: {DEFAULT_OUT_DB})")
    out.add_argument("--csv", default=DEFAULT_CSV, help=f"CSV export path (default: {DEFAULT_CSV})")

    run = p.add_argument_group("crawl behaviour")
    run.add_argument("--workers", type=int, default=3, help="Concurrent requests (default: 3)")
    run.add_argument("--delay", type=float, default=0.5, help="Per-request delay in seconds (default: 0.5)")
    run.add_argument("--limit", type=int, help="Only check the first N unique people")
    run.add_argument("--no-resume", action="store_true", help="Re-check everyone, ignoring the cache")
    run.add_argument("--no-school-check", action="store_true",
                     help="Match on name only; do not require the RMP school to match the course's university")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    people = load_people(args)
    if not people:
        print("No instructor names found to check.", file=sys.stderr)
        return 1

    conn = open_results_db(args.out_db)
    done: set[str] = set()
    if not args.no_resume:
        done = {r[0] for r in conn.execute("SELECT query_name FROM rmp_matches")}

    todo = [name for name in people if name not in done]
    if args.limit:
        todo = todo[: args.limit]

    print(
        f"Unique instructors: {len(people)} | already cached: {len(done)} | "
        f"to check now: {len(todo)}"
    )
    if not todo:
        n = export_csv(conn, args.csv)
        print(f"Nothing to do. CSV has {n} rows -> {args.csv}")
        return 0

    found = 0
    collisions = 0
    errors = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = {}
        for name in todo:
            expected = set() if args.no_school_check else people[name]["schools"]
            futures[ex.submit(check_one, name, expected, args.delay)] = name
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                row = fut.result()
            except Exception as e:  # noqa: BLE001
                row = {"query_name": name, "present": 0, "verdict": "error", "error": str(e)}
                errors += 1
            info = people.get(name, {"courses": set(), "school_names": set()})
            row["source_courses"] = "; ".join(sorted(info["courses"])[:3]) or None
            row["school_expected"] = "; ".join(sorted(info["school_names"])) or None
            save_row(conn, row)
            completed += 1
            if completed % 25 == 0:
                conn.commit()

            verdict = row.get("verdict")
            if verdict == "on_rmp":
                found += 1
                print(f"  [{completed}/{len(todo)}] ON RMP  {name:<30} -> "
                      f"{row.get('matched_name')} @ {row.get('school')} "
                      f"(rating={row.get('avg_rating')}, n={row.get('num_ratings')}, {row.get('match_quality')})")
            elif verdict == "name_collision":
                collisions += 1
                print(f"  [{completed}/{len(todo)}] diff?   {name:<30} -> name match at "
                      f"{row.get('school')} (expected {row.get('school_expected')})")
            elif row.get("error"):
                print(f"  [{completed}/{len(todo)}] ERROR   {name:<30} -> {row['error']}")
    conn.commit()

    n = export_csv(conn, args.csv)

    # Final summary over the whole cache.
    by_verdict = dict(conn.execute("SELECT verdict, COUNT(*) FROM rmp_matches GROUP BY verdict"))
    total_cached = conn.execute("SELECT COUNT(*) FROM rmp_matches").fetchone()[0]
    present_cached = by_verdict.get("on_rmp", 0)
    conn.close()

    print("\n" + "=" * 64)
    print(f"Checked this run : {len(todo)}  "
          f"(on RMP {found}, name-collisions {collisions}, errors {errors})")
    print(f"Cache verdicts   : on_rmp={by_verdict.get('on_rmp', 0)}  "
          f"name_collision={by_verdict.get('name_collision', 0)}  "
          f"review={by_verdict.get('review', 0)}  not_found={by_verdict.get('not_found', 0)}")
    print(f"On RMP (school-verified): {present_cached} / {total_cached}")
    print(f"Results DB       : {args.out_db}")
    print(f"CSV ({n} rows)   : {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
