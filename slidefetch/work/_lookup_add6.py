#!/usr/bin/env python3
"""One-off: look up 6 candidate courses in the ccr_rmp DBs to decide rated70 adds."""
import sqlite3

MAIN = "/home/b-ysonale/ccr_rmp/ccr_rmp.db"
RATED = "/home/b-ysonale/ccr_rmp/ccr_rmp_rated_all.db"

# (code fragment, college fragment)
TARGETS = [
    ("OIM3690", "Babson"),
    ("CSCI3022", "Colorado Boulder"),
    ("MACM101", "Simon Fraser"),
    ("CS6360", "Texas at Dallas"),
    ("CS6375", "Texas at Dallas"),
    ("ECON2030", "Auburn"),
]

m = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
m.row_factory = sqlite3.Row
try:
    ra = sqlite3.connect(f"file:{RATED}?mode=ro", uri=True)
    ra.row_factory = sqlite3.Row
    ra_links = {cc: sl for cc, sl in ra.execute("SELECT course_college, slide_link FROM courses")}
except Exception as e:  # noqa: BLE001
    print(f"[rated_all open error] {e}")
    ra_links = {}


def norm(s):
    return "".join(ch for ch in s.upper() if ch.isalnum())


for code, college in TARGETS:
    print("=" * 78)
    print(f"TARGET: {code} / {college}")
    ncode = norm(code)
    # find candidate course rows by normalized code + college fragment
    rows = m.execute(
        "SELECT course_college, course_code, college_name, num_ratings, course_slide_links "
        "FROM courses WHERE college_name LIKE ?",
        (f"%{college}%",),
    ).fetchall()
    hits = [r for r in rows if norm(r["course_code"]) == ncode or ncode in norm(r["course_code"])
            or ncode in norm(r["course_college"])]
    if not hits:
        # broaden: any college, code match
        rows2 = m.execute("SELECT course_college, course_code, college_name, num_ratings, course_slide_links FROM courses").fetchall()
        hits = [r for r in rows2 if norm(r["course_code"]) == ncode]
    if not hits:
        print("  NO MATCH in ccr_rmp.db")
        continue
    for r in hits:
        cc = r["course_college"]
        yrs = m.execute(
            "SELECT date FROM course_ratings_raw WHERE course_college=? AND date IS NOT NULL",
            (cc,),
        ).fetchall()
        years = sorted(int(str(d[0])[:4]) for d in yrs if str(d[0])[:4].isdigit())
        from collections import Counter
        yc = Counter(years)
        print(f"  course_college : {cc!r}")
        print(f"  course_code    : {r['course_code']!r}  college={r['college_name']!r}")
        print(f"  num_ratings    : {r['num_ratings']}")
        print(f"  slide (main)   : {r['course_slide_links']!r}")
        print(f"  slide (rated)  : {ra_links.get(cc)!r}")
        print(f"  rating years   : {dict(sorted(yc.items()))}  (total {len(years)})")
        # best year = maximize within-3 window
        if years:
            best_y, best_w = None, -1
            for y in range(min(years), max(years) + 1):
                w = sum(1 for yy in years if abs(yy - y) <= 3)
                if w > best_w:
                    best_y, best_w = y, w
            print(f"  best window    : year={best_y} within3={best_w}")
m.close()
