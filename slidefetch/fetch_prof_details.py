"""
Fetch per-professor detail + all student ratings from RMP's GraphQL API.
Reads professor IDs from mit_professors.db, stores results in new tables:
  - professor_details  (rating distribution + extra info)
  - professor_ratings  (individual student reviews)
"""

import base64
import json
import re
import sqlite3
import time
import sys
import argparse

import urllib.request
import urllib.error

DB_PATH = "mit_professors.db"
GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
BASE_HEADERS = {
    "Authorization": "Basic dGVzdDp0ZXN0",  # test:test — same as browser
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.ratemyprofessors.com",
    "Referer": "https://www.ratemyprofessors.com/",
}
DELAY_SECONDS = 1.5   # polite delay between requests
RATINGS_PAGE_SIZE = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch RMP professor metadata and ratings into SQLite tables."
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help=f"SQLite DB path with professors table (default: {DB_PATH})",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS professor_details (
            professor_id        INTEGER PRIMARY KEY REFERENCES professors(id),
            rmp_numeric_id      TEXT,
            first_name          TEXT,
            last_name           TEXT,
            avg_rating          REAL,
            avg_difficulty      REAL,
            would_take_again_pct REAL,
            num_ratings         INTEGER,
            department          TEXT,
            school_name         TEXT,
            school_city         TEXT,
            school_state        TEXT,
            dist_awesome        INTEGER,
            dist_great          INTEGER,
            dist_good           INTEGER,
            dist_ok             INTEGER,
            dist_awful          INTEGER,
            fetched_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS professor_ratings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            professor_id        INTEGER REFERENCES professors(id),
            rmp_rating_id       TEXT UNIQUE,
            quality             REAL,
            difficulty          REAL,
            course              TEXT,
            date                TEXT,
            for_credit          TEXT,
            attendance          TEXT,
            would_take_again    INTEGER,  -- 1=yes, 0=no, -1=N/A
            grade               TEXT,
            textbook            TEXT,
            comment             TEXT,
            tags                TEXT,     -- JSON array string
            thumbs_up           INTEGER,
            thumbs_down         INTEGER
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------

def rmp_node_id(numeric_id: str) -> str:
    """Encode a numeric RMP professor ID as a Relay node ID."""
    return base64.b64encode(f"Teacher-{numeric_id}".encode()).decode()


QUERY_TEACHER = """
query TeacherDetailQuery($id: ID!) {
  node(id: $id) {
    ... on Teacher {
      id
      firstName
      lastName
      avgRating
      avgDifficulty
      wouldTakeAgainPercent
      numRatings
      department
      school {
        name
        city
        state
      }
      ratingsDistribution {
        r1
        r2
        r3
        r4
        r5
      }
    }
  }
}
"""

QUERY_RATINGS = """
query TeacherRatingsQuery($id: ID!, $count: Int!, $cursor: String) {
  node(id: $id) {
    ... on Teacher {
      ratings(first: $count, after: $cursor) {
        edges {
          node {
            id
            qualityRating
            difficultyRatingRounded
            class
            date
            wouldTakeAgain
            grade
            attendanceMandatory
            textbookUse
            comment
            flagStatus
            ratingTags
            thumbsUpTotal
            thumbsDownTotal
          }
          cursor
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
"""


def gql_post(query: str, variables: dict) -> dict:
    """Execute a GraphQL query and return the parsed JSON response."""
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=BASE_HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:200]}")


# ---------------------------------------------------------------------------
# Fetch + parse per professor
# ---------------------------------------------------------------------------

def fetch_teacher_details(node_id: str) -> dict | None:
    data = gql_post(QUERY_TEACHER, {"id": node_id})
    node = data.get("data", {}).get("node")
    if not node:
        return None
    dist = node.get("ratingsDistribution") or {}
    school = node.get("school") or {}
    return {
        "rmp_numeric_id": node.get("id"),
        "first_name": node.get("firstName"),
        "last_name": node.get("lastName"),
        "avg_rating": node.get("avgRating"),
        "avg_difficulty": node.get("avgDifficulty"),
        "would_take_again_pct": node.get("wouldTakeAgainPercent"),
        "num_ratings": node.get("numRatings"),
        "department": node.get("department"),
        "school_name": school.get("name"),
        "school_city": school.get("city"),
        "school_state": school.get("state"),
        "dist_awesome": dist.get("r5"),
        "dist_great": dist.get("r4"),
        "dist_good": dist.get("r3"),
        "dist_ok": dist.get("r2"),
        "dist_awful": dist.get("r1"),
    }


def fetch_all_ratings(node_id: str) -> list[dict]:
    """Paginate through all student ratings for a professor."""
    all_ratings = []
    cursor = None
    while True:
        variables = {"id": node_id, "count": RATINGS_PAGE_SIZE, "cursor": cursor}
        data = gql_post(QUERY_RATINGS, variables)
        ratings_conn = (
            data.get("data", {})
                .get("node", {})
                .get("ratings", {})
        )
        if not ratings_conn:
            break
        edges = ratings_conn.get("edges", [])
        for edge in edges:
            n = edge["node"]
            # Parse tags: may be a comma-separated string or list
            tags = n.get("ratingTags") or ""
            if isinstance(tags, list):
                tags = json.dumps(tags)
            # wouldTakeAgain: 1=yes, 0=no, -1=N/A
            wta_raw = n.get("wouldTakeAgain")
            if wta_raw == 1:
                wta = 1
            elif wta_raw == 0:
                wta = 0
            else:
                wta = -1
            all_ratings.append({
                "rmp_rating_id": n.get("id"),
                "quality": n.get("qualityRating"),
                "difficulty": n.get("difficultyRatingRounded"),
                "course": n.get("class"),
                "date": n.get("date"),
                "for_credit": None,          # not in GraphQL response
                "attendance": n.get("attendanceMandatory"),
                "would_take_again": wta,
                "grade": n.get("grade"),
                "textbook": n.get("textbookUse"),
                "comment": n.get("comment"),
                "tags": tags,
                "thumbs_up": n.get("thumbsUpTotal"),
                "thumbs_down": n.get("thumbsDownTotal"),
            })
        page_info = ratings_conn.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        time.sleep(DELAY_SECONDS)
    return all_ratings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    conn = sqlite3.connect(args.db)
    create_tables(conn)

    # Find professors not yet fetched
    already_done = {
        r[0] for r in conn.execute("SELECT professor_id FROM professor_details").fetchall()
    }
    professors = conn.execute(
        "SELECT id, name, rmp_link FROM professors ORDER BY id"
    ).fetchall()

    todo = [(pid, name, link) for pid, name, link in professors if pid not in already_done]
    total = len(todo)
    print(f"Professors to fetch: {total} (already done: {len(already_done)})")

    for idx, (prof_id, name, rmp_link) in enumerate(todo, 1):
        # Extract numeric ID from URL
        m = re.search(r"/professor/(\d+)", rmp_link or "")
        if not m:
            print(f"  [{idx}/{total}] SKIP {name} — no numeric ID in link")
            continue
        numeric_id = m.group(1)
        node_id = rmp_node_id(numeric_id)
        print(f"  [{idx}/{total}] {name} (id={numeric_id})", end=" ... ", flush=True)

        try:
            details = fetch_teacher_details(node_id)
            time.sleep(DELAY_SECONDS)
            if not details:
                print("no data")
                continue

            details["professor_id"] = prof_id
            conn.execute("""
                INSERT OR REPLACE INTO professor_details
                (professor_id, rmp_numeric_id, first_name, last_name,
                 avg_rating, avg_difficulty, would_take_again_pct, num_ratings,
                 department, school_name, school_city, school_state,
                 dist_awesome, dist_great, dist_good, dist_ok, dist_awful)
                VALUES
                (:professor_id, :rmp_numeric_id, :first_name, :last_name,
                 :avg_rating, :avg_difficulty, :would_take_again_pct, :num_ratings,
                 :department, :school_name, :school_city, :school_state,
                 :dist_awesome, :dist_great, :dist_good, :dist_ok, :dist_awful)
            """, details)

            ratings = fetch_all_ratings(node_id)
            for r in ratings:
                r["professor_id"] = prof_id
                conn.execute("""
                    INSERT OR IGNORE INTO professor_ratings
                    (professor_id, rmp_rating_id, quality, difficulty, course, date,
                     for_credit, attendance, would_take_again, grade, textbook,
                     comment, tags, thumbs_up, thumbs_down)
                    VALUES
                    (:professor_id, :rmp_rating_id, :quality, :difficulty, :course, :date,
                     :for_credit, :attendance, :would_take_again, :grade, :textbook,
                     :comment, :tags, :thumbs_up, :thumbs_down)
                """, r)

            conn.commit()
            print(f"ok ({len(ratings)} ratings)")

        except Exception as e:
            print(f"ERROR: {e}")
            conn.rollback()
            time.sleep(DELAY_SECONDS * 2)  # back off on error

    conn.close()
    print("\nDone.")
    # Final summary
    conn2 = sqlite3.connect(args.db)
    nd = conn2.execute("SELECT COUNT(*) FROM professor_details").fetchone()[0]
    nr = conn2.execute("SELECT COUNT(*) FROM professor_ratings").fetchone()[0]
    conn2.close()
    print(f"professor_details rows : {nd}")
    print(f"professor_ratings rows : {nr}")


if __name__ == "__main__":
    main()
