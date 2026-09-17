#!/usr/bin/env python3
"""Update ccr_rmp_rated_all.db to the current "Has course ratings" set (course_num_ratings>0
in ccr_rmp.db). Additive: the existing rated courses are a strict subset of the current set,
so we only INSERT the newly-rated courses (+ their professors / ratings / school rows),
preserving the existing rows, their curated `slide_link` values, and the feedback table.

    ./.venv/bin/python work/_update_rated.py --dry
    ./.venv/bin/python work/_update_rated.py
"""
from __future__ import annotations

import argparse
import sqlite3

RATED = "ccr_rmp_rated_all.db"
MAIN = "ccr_rmp.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(RATED)
    con.execute(f"ATTACH DATABASE '{MAIN}' AS src")

    existing = {r[0] for r in con.execute("SELECT course_college FROM courses")}
    rated = [r[0] for r in con.execute(
        "SELECT course_college FROM src.courses WHERE course_num_ratings>0")]
    new = [cc for cc in rated if cc not in existing]
    print(f"rated_all currently: {len(existing)} | main rated: {len(rated)} | to add: {len(new)}")
    if args.dry or not new:
        for cc in new[:20]:
            print("  +", cc)
        con.close()
        return

    main_cols = [r[1] for r in con.execute("PRAGMA src.table_info(courses)")]
    prof_cols = [r[1] for r in con.execute("PRAGMA table_info(course_professors)")]
    rate_cols = [r[1] for r in con.execute("PRAGMA table_info(course_ratings_raw)")]
    sc_cols = [r[1] for r in con.execute("PRAGMA table_info(school_cache)")]
    ph = ",".join("?" * len(new))

    # courses: copy all main columns + slide_link = course_slide_links
    sel = ",".join(f"c.{c}" for c in main_cols)
    con.execute(
        f"INSERT INTO courses ({','.join(main_cols)}, slide_link) "
        f"SELECT {sel}, c.course_slide_links FROM src.courses c "
        f"WHERE c.course_college IN ({ph})", new)
    # professors
    con.execute(
        f"INSERT INTO course_professors ({','.join(prof_cols)}) "
        f"SELECT {','.join(prof_cols)} FROM src.course_professors "
        f"WHERE course_college IN ({ph})", new)
    # ratings (rating_id PK -> OR IGNORE for the rare shared-rating dedup)
    con.execute(
        f"INSERT OR IGNORE INTO course_ratings_raw ({','.join(rate_cols)}) "
        f"SELECT {','.join(rate_cols)} FROM src.course_ratings_raw "
        f"WHERE course_college IN ({ph})", new)
    # school_cache: pull referenced colleges not already cached
    con.execute(
        f"INSERT OR IGNORE INTO school_cache ({','.join(sc_cols)}) "
        f"SELECT {','.join(sc_cols)} FROM src.school_cache WHERE college_name IN "
        f"(SELECT DISTINCT college_name FROM src.courses WHERE course_college IN ({ph}))",
        new)
    con.commit()

    n_courses = con.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    n_prof = con.execute("SELECT COUNT(*) FROM course_professors").fetchone()[0]
    n_rate = con.execute("SELECT COUNT(*) FROM course_ratings_raw").fetchone()[0]
    con.close()
    print(f"DONE. rated_all now: courses={n_courses}, professors={n_prof}, ratings={n_rate}")


if __name__ == "__main__":
    main()
