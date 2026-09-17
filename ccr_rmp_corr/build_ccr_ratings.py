#!/usr/bin/env python3
"""Phase A - build the CCR (collegeclassreviews.com) ratings dataset.

For each of the 73 rated70 courses:
  1. resolve its CCR page URL via ccr_courses.db (exact course_college; else a
     normalized course_code + college_name fallback),
  2. fresh-scrape that page for star_rating + review count + the 6 crowd metrics,
  3. fall back to ccr_courses.db's stored star_rating if the scrape yields none.

Writes ccr_ratings.csv (one row per course) and prints coverage + a scraped-vs-
stored star agreement check.
"""
from __future__ import annotations

import csv
import urllib.error

import common
from ccr_scrape import METRICS, fetch, parse_course

OUT = common.ROOT / "ccr_ratings.csv"
FIELDS = [
    "folder", "course_college", "course_code", "college_name", "ccr_url",
    "ccr_star_rating", "ccr_num_reviews",
    "ccr_student_satisfaction", "ccr_challenge_level", "ccr_grade_accessibility",
    "ccr_time_investment", "ccr_attendance_importance", "ccr_recommendation_rate",
    "ccr_source",
]


def build_ccr_index(ccr):
    by_cc, by_codecoll = {}, {}
    for row in ccr.execute(
        "SELECT course_college, course_code, college_name, ccr_link, star_rating FROM courses"
    ):
        by_cc[row["course_college"]] = row
        by_codecoll[(common.norm(row["course_code"]), common.norm(row["college_name"]))] = row
    return by_cc, by_codecoll


def build_rows(verbose: bool = True):
    """Resolve + scrape CCR ratings for all 73 rated70 courses.

    Returns (rows, coverage): rows is a list of dicts keyed by FIELDS; coverage
    summarises scraped/stored/missing counts and the star-agreement samples.
    Shared by both the CSV writer (main) and the SQLite builder (build_ccr_db).
    """
    rows = common.load_manifest()
    rmp = common.ro(common.CCR_RMP_DB)
    ccr = common.ro(common.CCR_COURSES_DB)
    by_cc, by_codecoll = build_ccr_index(ccr)

    out_rows = []
    n_scraped = n_stored = n_missing = 0
    agree = []  # (scraped, stored) for star agreement check
    for m in sorted(rows, key=lambda r: r["folder"]):
        cc = m["course_college"]
        info = rmp.execute(
            "SELECT course_code, college_name FROM courses WHERE course_college=?", (cc,)
        ).fetchone()
        code = info["course_code"] if info else ""
        college = info["college_name"] if info else cc.split(" - ", 1)[-1]
        hit = by_cc.get(cc) or by_codecoll.get((common.norm(code), common.norm(college)))

        rec = {f: None for f in FIELDS}
        rec.update(folder=m["folder"], course_college=cc, course_code=code, college_name=college)

        if not hit:
            rec["ccr_source"] = "missing"
            n_missing += 1
            out_rows.append(rec)
            if verbose:
                print(f"[{m['folder']}] {cc}: NO CCR row")
            continue

        rec["ccr_url"] = hit["ccr_link"]
        stored_star = hit["star_rating"]
        parsed = None
        try:
            html = fetch(hit["ccr_link"], common.CACHE)
            parsed = parse_course(html)
        except (urllib.error.URLError, OSError) as exc:
            if verbose:
                print(f"[{m['folder']}] {cc}: fetch failed ({exc})")

        if parsed and parsed.get("star_rating") is not None:
            rec["ccr_star_rating"] = parsed["star_rating"]
            rec["ccr_num_reviews"] = parsed["num_reviews"]
            for col, _ in METRICS:
                rec[f"ccr_{col}"] = parsed[col]
            rec["ccr_source"] = "scraped"
            n_scraped += 1
            if stored_star is not None:
                agree.append((parsed["star_rating"], stored_star))
        elif stored_star is not None:
            rec["ccr_star_rating"] = stored_star
            rec["ccr_source"] = "stored"
            n_stored += 1
        else:
            rec["ccr_source"] = "missing"
            n_missing += 1
        out_rows.append(rec)
        if verbose:
            print(f"[{m['folder']}] {cc}: star={rec['ccr_star_rating']} src={rec['ccr_source']}")

    coverage = {"scraped": n_scraped, "stored": n_stored, "missing": n_missing, "agree": agree}
    return out_rows, coverage


def main() -> None:
    out_rows, cov = build_rows()
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out_rows)

    print(f"\nwrote {OUT}  ({len(out_rows)} rows)")
    print(f"coverage: scraped={cov['scraped']} stored={cov['stored']} missing={cov['missing']}")
    agree = cov["agree"]
    if agree:
        diffs = [abs(a - b) for a, b in agree]
        big = [(a, b) for a, b in agree if abs(a - b) > 0.15]
        print(f"scraped-vs-stored star: n={len(agree)} max|diff|={max(diffs):.2f} "
              f"mean|diff|={sum(diffs) / len(diffs):.3f} disagreements>0.15={len(big)}")
        for a, b in big[:10]:
            print(f"  scraped={a} stored={b}")


if __name__ == "__main__":
    main()
