#!/usr/bin/env python3
"""Build ccr_ratings.db - a SQLite store of CCR ratings for the 73 rated70 courses.

Uses the same authoritative resolve+scrape path as build_ccr_ratings.py
(build_rows). One row per course, keyed by rated70 folder, with a REAL-typed
star + 6 metrics. Idempotent: drops and rebuilds the ccr_ratings table.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import common
from build_ccr_ratings import FIELDS, build_rows

DB = common.ROOT / "ccr_ratings.db"

SCHEMA = """
CREATE TABLE ccr_ratings (
    folder                    TEXT PRIMARY KEY,   -- rated70 folder 01..73
    course_college            TEXT,               -- "<code> - <college>"
    course_code               TEXT,
    college_name              TEXT,
    ccr_url                   TEXT,               -- collegeclassreviews.com course page
    ccr_star_rating           REAL,               -- aggregate course rating (/5)
    ccr_num_reviews           INTEGER,            -- CCR student review count
    ccr_student_satisfaction  REAL,               -- 0-1 crowd metrics below
    ccr_challenge_level       REAL,
    ccr_grade_accessibility   REAL,
    ccr_time_investment       REAL,
    ccr_attendance_importance REAL,
    ccr_recommendation_rate   REAL,
    ccr_source                TEXT,               -- scraped | stored | missing
    scraped_at                TEXT
);
CREATE INDEX idx_ccr_course ON ccr_ratings(course_college);
"""

INT_COLS = {"ccr_num_reviews"}


def main() -> None:
    rows, cov = build_rows(verbose=False)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    cols = FIELDS + ["scraped_at"]
    placeholders = ",".join("?" for _ in cols)
    for r in rows:
        vals = []
        for c in FIELDS:
            v = r.get(c)
            if v is not None and c in INT_COLS:
                v = int(v)
            vals.append(v)
        vals.append(now)
        con.execute(f"INSERT INTO ccr_ratings ({','.join(cols)}) VALUES ({placeholders})", vals)
    con.commit()

    # verification
    n = con.execute("SELECT COUNT(*) FROM ccr_ratings").fetchone()[0]
    n_star = con.execute("SELECT COUNT(*) FROM ccr_ratings WHERE ccr_star_rating IS NOT NULL").fetchone()[0]
    print(f"wrote {DB}")
    print(f"rows={n}  star_present={n_star}  "
          f"coverage: scraped={cov['scraped']} stored={cov['stored']} missing={cov['missing']}")
    print("\ntop 5 by CCR star:")
    for r in con.execute("SELECT folder, course_college, ccr_star_rating, ccr_num_reviews "
                         "FROM ccr_ratings ORDER BY ccr_star_rating DESC LIMIT 5"):
        print(f"  [{r[0]}] {r[1][:44]:44} star={r[2]} n={r[3]}")
    print("bottom 5 by CCR star:")
    for r in con.execute("SELECT folder, course_college, ccr_star_rating, ccr_num_reviews "
                         "FROM ccr_ratings ORDER BY ccr_star_rating ASC LIMIT 5"):
        print(f"  [{r[0]}] {r[1][:44]:44} star={r[2]} n={r[3]}")
    con.close()


if __name__ == "__main__":
    main()
