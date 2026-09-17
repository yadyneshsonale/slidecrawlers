#!/usr/bin/env python3
"""Phase C - merge CCR + RMP per-course tables and fold in RateMySlides scores.

Produces merged.csv: one row per rated70 course (73), carrying the CCR course
ratings, the RMP per-course/professor ratings, and the slide-quality scores from
RateMySlides/output_trapi/summary.json (NaN for courses whose slides weren't scored).
"""
from __future__ import annotations

import json

import pandas as pd

import common

OUT = common.ROOT / "merged.csv"

CCR_COLS = [
    "folder", "course_college", "course_code", "college_name",
    "ccr_star_rating", "ccr_num_reviews", "ccr_student_satisfaction",
    "ccr_challenge_level", "ccr_grade_accessibility", "ccr_time_investment",
    "ccr_attendance_importance", "ccr_recommendation_rate", "ccr_source",
]
RMP_COLS = [
    "folder", "course_date", "within3", "instructor",
    "rmp_course_num_ratings", "rmp_course_avg_quality", "rmp_course_avg_difficulty",
    "rmp_course_would_take_again_pct", "rmp_prof_avg_rating_overall",
    "rmp_prof_avg_difficulty_overall", "rmp_prof_num_ratings_overall",
]
SLIDE_L1 = ["visual_documentation", "abbreviation", "symbol", "visual_appeal", "prerequisite"]
SLIDE_L2 = ["progression", "long_term_recall"]


def load_slides() -> pd.DataFrame:
    data = json.loads(common.SLIDE_SUMMARY.read_text(encoding="utf-8"))
    recs = []
    for d in data:
        rec = {
            "folder": str(d["course_dir"]).zfill(2),
            "slide_status": d.get("status"),
            "slide_num_decks": d.get("num_decks"),
            "slide_final": d.get("final"),
            "slide_layer1_course": d.get("layer1_course"),
            "slide_layer2": d.get("layer2"),
        }
        for k in SLIDE_L1:
            rec[f"slide_{k}"] = (d.get("layer1_metrics") or {}).get(k)
        for k in SLIDE_L2:
            rec[f"slide_{k}"] = (d.get("layer2_metrics") or {}).get(k)
        recs.append(rec)
    return pd.DataFrame(recs)


def main() -> None:
    ccr = pd.read_csv(common.ROOT / "ccr_ratings.csv", dtype={"folder": str})[CCR_COLS]
    rmp = pd.read_csv(common.ROOT / "rmp_ratings.csv", dtype={"folder": str})[RMP_COLS]
    slides = load_slides()

    merged = ccr.merge(rmp, on="folder", how="left").merge(slides, on="folder", how="left")
    merged = merged.sort_values("folder").reset_index(drop=True)
    merged.to_csv(OUT, index=False)

    print(f"wrote {OUT}  ({len(merged)} rows, {merged.shape[1]} cols)")
    print(f"CCR star present : {merged.ccr_star_rating.notna().sum()}/73")
    print(f"RMP quality present: {merged.rmp_course_avg_quality.notna().sum()}/73")
    print(f"slide final present: {merged.slide_final.notna().sum()}/73")
    missing_slides = merged.loc[merged.slide_final.isna(), "folder"].tolist()
    print(f"folders without slide score: {missing_slides}")


if __name__ == "__main__":
    main()
