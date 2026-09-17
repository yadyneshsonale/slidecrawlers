#!/usr/bin/env python3
"""Add course_date to course_professors (from work/_course_dates.tsv) and analyze how many
courses have >=4 ratings within 3 years of their course_date.

Course_date = the year from the provided list, matched on course_college; NULL otherwise.
Rating year = year of course_ratings_raw.date (ISO YYYY-MM-DD). "Within 3 years" =
abs(rating_year - course_date) <= 3.
"""
from __future__ import annotations

import sqlite3

DB = "ccr_rmp.db"
DATES = "work/_course_dates.tsv"


def load_dates():
    out = {}
    with open(DATES, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            cc, _, yr = line.rpartition("\t")
            cc = cc.strip()
            try:
                out[cc] = int(yr.strip())
            except ValueError:
                pass
    return out


def main():
    dates = load_dates()
    con = sqlite3.connect(DB)

    # 1. add the column (idempotent)
    cols = [r[1] for r in con.execute("PRAGMA table_info(course_professors)")]
    if "course_date" not in cols:
        con.execute("ALTER TABLE course_professors ADD COLUMN course_date INTEGER")
        print("added column course_date to course_professors")
    else:
        print("course_date column already exists")
    # reset to NULL first so a re-run is clean, then set from the list
    con.execute("UPDATE course_professors SET course_date=NULL")
    updated = 0
    for cc, yr in dates.items():
        cur = con.execute(
            "UPDATE course_professors SET course_date=? WHERE course_college=?", (yr, cc))
        updated += cur.rowcount
    con.commit()

    prof_courses = {r[0] for r in con.execute("SELECT DISTINCT course_college FROM course_professors")}
    matched = [cc for cc in dates if cc in prof_courses]
    unmatched = [cc for cc in dates if cc not in prof_courses]
    print(f"list entries={len(dates)}; course_professors rows set={updated}; "
          f"distinct courses matched={len(matched)}; list courses NOT in course_professors={len(unmatched)}")
    if unmatched:
        print("  not in course_professors:", ", ".join(unmatched[:12]) + (" ..." if len(unmatched) > 12 else ""))

    # 2. analysis: ratings within 3 years of course_date, >=4
    cdate = {cc: yr for cc, yr in con.execute(
        "SELECT DISTINCT course_college, course_date FROM course_professors WHERE course_date IS NOT NULL")}
    qualifying, within_counts = [], {}
    have_ratings = 0
    ge4_overall = 0
    for cc, yr in cdate.items():
        rows = con.execute(
            "SELECT date FROM course_ratings_raw WHERE course_college=? AND date IS NOT NULL", (cc,)).fetchall()
        if rows:
            have_ratings += 1
        total = len(rows)
        within = 0
        for (d,) in rows:
            try:
                ry = int(str(d)[:4])
            except ValueError:
                continue
            if abs(ry - yr) <= 3:
                within += 1
        within_counts[cc] = (within, total)
        if total >= 4:
            ge4_overall += 1
        if within >= 4:
            qualifying.append((cc, yr, within, total))
    con.close()

    print(f"\n=== ANALYSIS (courses with a course_date = {len(cdate)}) ===")
    print(f"courses that have any ratings         : {have_ratings}")
    print(f"courses with >=4 ratings (any date)   : {ge4_overall}")
    print(f"courses with >=4 ratings WITHIN 3 yrs : {len(qualifying)}   <-- answer")
    print("\nsample qualifying (course, course_date, within3, total):")
    for cc, yr, w, t in sorted(qualifying, key=lambda x: -x[2])[:15]:
        print(f"  {cc[:36]:36} date={yr} within3={w} total={t}")


if __name__ == "__main__":
    main()
