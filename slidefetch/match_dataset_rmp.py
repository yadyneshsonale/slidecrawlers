#!/usr/bin/env python3
"""Match scraper/dataset course folders against RMP course ratings (school-aware).

dataset layout is  <university>/<course>/ , e.g.  stanford/cs221 , cmu/10-701 ,
mit/6.036 .  Each university maps to an RMP school, and the course folder name
usually embeds a course code (cs221, 10-701, 6.036, cos_126).  RMP's `course`
field is free text (CS221, COS126, CSC411, 6042...).  We normalise both to
UPPERCASE alphanumeric and match within the same school.
"""
import os
import re
import csv
import sqlite3

DATASET = "/home/b-ysonale/scraper/dataset"
DB = "/home/b-ysonale/slidefetch/rmp_course_ratings.db"
OUT = "/home/b-ysonale/slidefetch/dataset_rmp_matches.csv"

# dataset university folder -> keyword that must appear in the RMP school name.
UNI_SCHOOL = {
    "mit": "massachusetts institute of technology",
    "stanford": "stanford",
    "uc_berkeley": "california berkeley",
    "princeton": "princeton",
    "harvard": "harvard",
    "university_of_toronto": "toronto",
    "university_of_utah": "utah",
    "university_of_waterloo": "waterloo",
    "uiuc": "illinois at urbana",
    "cornell": "cornell",
}

SEASON = {"fall", "spring", "summer", "winter", "autumn"}
CODE_LETTER = re.compile(r"([a-z]{2,5})\s?(\d{2,5}[a-z]?)")
CODE_NUMERIC = re.compile(r"(\d{1,2})[.\-]([a-z]?\d{2,4}[a-z]?)")


def norm(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", code.lower()).upper()


def extract_codes(folder: str) -> set[str]:
    """Pull candidate course codes out of a course-folder name."""
    text = folder.lower().replace("_", " ")
    codes: set[str] = set()
    for m in CODE_LETTER.finditer(text):
        if m.group(1) in SEASON:
            continue
        codes.add(norm(m.group(1) + m.group(2)))
    for m in CODE_NUMERIC.finditer(text):
        codes.add(norm(m.group(1) + m.group(2)))
    # keep codes that have a digit and >=3 chars
    return {c for c in codes if len(c) >= 3 and any(ch.isdigit() for ch in c)}


def main():
    con = sqlite3.connect(DB)
    rmp = con.execute(
        """SELECT school, prof_name, course, num_ratings, avg_quality, avg_difficulty, profile_url
           FROM course_summary WHERE course IS NOT NULL"""
    ).fetchall()
    # rmp_by_school[school] = { code_norm : [(prof, raw_course, n, q, d, url), ...] }
    rmp_by_school: dict[str, dict[str, list]] = {}
    for school, prof, course, n, q, d, url in rmp:
        k = norm(course)
        if len(k) < 3:
            continue
        rmp_by_school.setdefault(school, {}).setdefault(k, []).append(
            (prof, course, n or 0, q, d, url))

    def school_for(uni: str):
        kw = UNI_SCHOOL.get(uni)
        if not kw:
            return None
        for s in rmp_by_school:
            if kw in s.lower():
                return s
        return None

    rows, per_uni = [], {}
    for uni in sorted(os.listdir(DATASET)):
        upath = os.path.join(DATASET, uni)
        if not os.path.isdir(upath):
            continue
        courses = [c for c in sorted(os.listdir(upath))
                   if os.path.isdir(os.path.join(upath, c))]
        school = school_for(uni)
        n_code = n_match = 0
        for c in courses:
            codes = extract_codes(c)
            if codes:
                n_code += 1
            if not school:
                continue
            table = rmp_by_school.get(school, {})
            hits = [(code, h) for code in codes for h in table.get(code, [])]
            if not hits:
                continue
            n_match += 1
            tot = sum(h[1][2] for h in hits)
            wq = sum((h[1][3] or 0) * h[1][2] for h in hits) / tot if tot else None
            wd = sum((h[1][4] or 0) * h[1][2] for h in hits) / tot if tot else None
            rows.append({
                "university": uni,
                "course_folder": c,
                "matched_code": "; ".join(sorted({h[0] for h in hits})),
                "rmp_school": school,
                "rmp_courses": "; ".join(sorted({h[1][1] for h in hits})),
                "professors": "; ".join(sorted({h[1][0] for h in hits})),
                "total_ratings": tot,
                "avg_quality": round(wq, 2) if wq is not None else "",
                "avg_difficulty": round(wd, 2) if wd is not None else "",
                "profile_urls": "; ".join(sorted({h[1][5] for h in hits if h[1][5]})),
            })
        per_uni[uni] = (len(courses), n_code, n_match, school)

    rows.sort(key=lambda r: r["total_ratings"], reverse=True)
    fields = ["university", "course_folder", "matched_code", "rmp_school", "rmp_courses",
              "professors", "total_ratings", "avg_quality", "avg_difficulty", "profile_urls"]
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"{'university':<42} {'courses':>7} {'w/code':>6} {'matched':>7}  RMP school")
    print("-" * 100)
    for uni in sorted(per_uni):
        tot, nc, nm, sch = per_uni[uni]
        tag = sch if sch else ("(no RMP data)" if uni in UNI_SCHOOL else "(unmapped)")
        print(f"{uni:<42} {tot:>7} {nc:>6} {nm:>7}  {tag}")

    print(f"\n=== {len(rows)} course folder(s) matched to RMP ratings ===")
    print(f"{'university':<16} {'folder':<22} {'code':<8} {'rate':>4} {'qual':>4} {'diff':>4}  prof  [RMP]")
    print("-" * 100)
    for r in rows:
        print(f"{r['university']:<16} {r['course_folder'][:22]:<22} {r['matched_code']:<8} "
              f"{r['total_ratings']:>4} {str(r['avg_quality']):>4} {str(r['avg_difficulty']):>4}  "
              f"{r['professors']}  [{r['rmp_courses']}]")
    print(f"\nCSV written: {OUT}")


if __name__ == "__main__":
    main()
