#!/usr/bin/env python3
"""Phase B - build the RMP (RateMyProfessors) datasets from rated70.db.

rmp_ratings.csv  : one row per course (73) - per-course quality/difficulty
                   aggregates plus the professor's overall RMP numbers.
rmp_reviews.csv  : one row per individual RMP review (the raw student ratings +
                   free-text comments) for all 73 courses.
"""
from __future__ import annotations

import csv

import common

OUT_RATINGS = common.ROOT / "rmp_ratings.csv"
OUT_REVIEWS = common.ROOT / "rmp_reviews.csv"

RATING_FIELDS = [
    "folder", "course_college", "course_code", "college_name", "course_date", "within3",
    "instructor", "rmp_matched_name", "rmp_status", "rmp_profile_url",
    "rmp_course_num_ratings", "rmp_course_avg_quality", "rmp_course_avg_difficulty",
    "rmp_course_would_take_again_pct",
    "rmp_prof_avg_rating_overall", "rmp_prof_avg_difficulty_overall", "rmp_prof_num_ratings_overall",
    "rmp_num_reviews_in_file",
]
REVIEW_FIELDS = [
    "folder", "course_college", "class", "quality", "difficulty",
    "would_take_again", "grade", "date", "comment", "tags",
]


def main() -> None:
    r70 = common.ro(common.RATED70_DB)

    # per-course would-take-again % + review counts from the ratings table
    wta, review_count = {}, {}
    for row in r70.execute("SELECT folder, would_take_again FROM ratings"):
        f = row["folder"]
        review_count[f] = review_count.get(f, 0) + 1
        w = row["would_take_again"]
        if w is not None and str(w) != "":
            yes, tot = wta.get(f, (0, 0))
            wta[f] = (yes + (1 if int(w) == 1 else 0), tot + 1)

    rating_rows = []
    for c in r70.execute("SELECT * FROM courses ORDER BY folder"):
        f = c["folder"]
        yes_tot = wta.get(f)
        rating_rows.append({
            "folder": f,
            "course_college": c["course_college"],
            "course_code": c["course_code"],
            "college_name": c["college_name"],
            "course_date": c["course_date"],
            "within3": c["within3"],
            "instructor": c["instructor"],
            "rmp_matched_name": c["rmp_matched_name"],
            "rmp_status": c["rmp_status"],
            "rmp_profile_url": c["rmp_profile_url"],
            "rmp_course_num_ratings": c["course_num_ratings"],
            "rmp_course_avg_quality": c["course_avg_quality"],
            "rmp_course_avg_difficulty": c["course_avg_difficulty"],
            "rmp_course_would_take_again_pct": round(100 * yes_tot[0] / yes_tot[1], 1) if yes_tot and yes_tot[1] else None,
            "rmp_prof_avg_rating_overall": c["rmp_avg_rating"],
            "rmp_prof_avg_difficulty_overall": c["rmp_avg_difficulty"],
            "rmp_prof_num_ratings_overall": c["rmp_num_ratings"],
            "rmp_num_reviews_in_file": review_count.get(f, 0),
        })

    with open(OUT_RATINGS, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RATING_FIELDS)
        w.writeheader()
        w.writerows(rating_rows)

    review_rows = []
    for r in r70.execute(
        "SELECT folder, course_college, class, quality, difficulty, would_take_again, "
        "grade, date, comment, tags FROM ratings ORDER BY folder, date"
    ):
        review_rows.append({k: r[k] for k in REVIEW_FIELDS})

    with open(OUT_REVIEWS, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS)
        w.writeheader()
        w.writerows(review_rows)

    print(f"wrote {OUT_RATINGS}  ({len(rating_rows)} courses)")
    print(f"wrote {OUT_REVIEWS}  ({len(review_rows)} reviews)")
    n_q = sum(1 for r in rating_rows if r["rmp_course_avg_quality"] is not None)
    print(f"courses with per-course avg_quality: {n_q}/{len(rating_rows)}")


if __name__ == "__main__":
    main()
