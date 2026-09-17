#!/usr/bin/env python3
"""Scrape course quality metrics from collegeclassreviews.com and store them in
a SQLite database.

For each course it extracts the six crowdsourced metrics (Student Satisfaction,
Challenge Level, Grade Accessibility, Time Investment, Attendance Importance,
Recommendation Rate), the aggregate star rating shown on the page, and the
number of ratings/reviews.
"""
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone

DB_PATH = "ccr_course_ratings.db"
BASE = "https://collegeclassreviews.com/universities/"

METRICS = [
    ("student_satisfaction", "Student Satisfaction"),
    ("challenge_level", "Challenge Level"),
    ("grade_accessibility", "Grade Accessibility"),
    ("time_investment", "Time Investment"),
    ("attendance_importance", "Attendance Importance"),
    ("recommendation_rate", "Recommendation Rate"),
]

# course_id -> (university slug, course slug) for courses present on the site.
COURSES = {
    "upitt-stat0200":     ("university-of-pittsburgh",            "stat0200"),
    "umich-eecs230":      ("university-of-michigan",              "eecs230"),
    "umich-eecs270":      ("university-of-michigan",              "eecs270"),
    "umich-eecs312":      ("university-of-michigan",              "eecs312"),
    "umich-eecs376":      ("university-of-michigan",              "eecs376"),
    "umich-eecs442":      ("university-of-michigan",              "eecs442"),
    "ucb-cs61a":          ("uc-berkeley",                         "cs61a"),
    "cornell-2110":       ("cornell-university",                  "cs2110"),
    "cornell-cs1110":     ("cornell-university",                  "cs1110"),
    "usc-cs170":          ("university-of-southern-california",   "csci170"),
    "gatech-cse6220-y24": ("georgia-tech",                        "cse6220"),
    "uot-csc411":         ("university-of-toronto",               "csc411"),
    "umn-5102":           ("university-of-minnesota-twin-cities", "stat5102"),
    "cmu-10601":          ("carnegie-mellon-university",          "ml10601"),
    "cmu-15281":          ("carnegie-mellon-university",          "cs15281"),
    "stanford-106A":      ("stanford-university",                 "cs106a"),
    "stanford-cs221":     ("stanford-university",                 "cs221"),
    # courses discovered in slidefetch/downloads and scraper/dataset
    "princeton-cos226":   ("princeton-university",                "cos226"),
    "stanford-cs144":     ("stanford-university",                 "cs144"),
    "stanford-cs143":     ("stanford-university",                 "cs143"),
    "ucalgary-cpsc457":   ("university-of-calgary",               "cpsc457"),
    "cmu-15122":          ("carnegie-mellon-university",          "cs15122"),
    "ucb-cs162":          ("uc-berkeley",                         "cs162"),
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse(html: str) -> dict:
    out = {}
    for col, label in METRICS:
        val = None
        for mt in re.finditer(re.escape(label), html):
            window = html[mt.start(): mt.start() + 400]
            wm = re.search(r'width\\?":\\?"(\d+)%', window)
            if wm:
                val = int(wm.group(1)) / 100.0
                break
        out[col] = val

    star = None
    sm = re.search(r'\\?"rating\\?":\s*([0-9]+(?:\.[0-9]+)?)\s*,\s*\\?"size\\?"', html)
    if sm:
        star = float(sm.group(1))
    else:
        rv = re.search(r'ratingValue\\*":\\*"([0-9]+(?:\.[0-9]+)?)', html)
        if rv:
            star = float(rv.group(1))
    out["star_rating"] = star

    cm = re.search(r'Aggregated from\s+([0-9,]+)\s+student rating', html)
    out["num_reviews"] = int(cm.group(1).replace(",", "")) if cm else None
    return out


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS course_ratings")
    conn.execute(
        """
        CREATE TABLE course_ratings (
            course_id             TEXT PRIMARY KEY,
            university            TEXT,
            course_slug           TEXT,
            url                   TEXT,
            star_rating           REAL,   -- aggregate star rating from the website (/5)
            num_reviews           INTEGER,
            student_satisfaction  REAL,
            challenge_level       REAL,
            grade_accessibility   REAL,
            time_investment       REAL,
            attendance_importance REAL,
            recommendation_rate   REAL,
            scraped_at            TEXT
        )
        """
    )
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for course_id, (uni, slug) in COURSES.items():
        url = f"{BASE}{uni}/courses/{slug}"
        try:
            data = parse(fetch(url))
        except urllib.error.HTTPError as exc:
            print(f"SKIP {course_id}: {exc}")
            continue
        conn.execute(
            """
            INSERT INTO course_ratings (
                course_id, university, course_slug, url, star_rating,
                num_reviews, student_satisfaction, challenge_level,
                grade_accessibility, time_investment, attendance_importance,
                recommendation_rate, scraped_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                course_id, uni, slug, url, data["star_rating"],
                data["num_reviews"], data["student_satisfaction"],
                data["challenge_level"], data["grade_accessibility"],
                data["time_investment"], data["attendance_importance"],
                data["recommendation_rate"], now,
            ),
        )
        print(f"stored {course_id}: stars={data['star_rating']} "
              f"reviews={data['num_reviews']}")

    conn.commit()
    conn.close()
    print(f"\nDatabase written to {DB_PATH}")


if __name__ == "__main__":
    main()
