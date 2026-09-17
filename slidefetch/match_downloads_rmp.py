#!/usr/bin/env python3
"""Match downloaded MIT OCW courses (new_downloads/new) against RMP course ratings.

Folder names look like  ocw.mit.edu_6-042j-mathematics-for-computer-science-fall-2005
-> MIT code 6.042J.  RMP's free-text `course` field stores the same course as
"6042", "6.042", "BIO7012", "ECON1403", "802T", etc.  Dots, dashes and subject-word
prefixes all collapse to the same digit string, so we join on the concatenated
department+number digit key (e.g. 6-042j -> "6042", ECON1403 -> "1403").
"""
import os
import re
import csv
import sqlite3

ROOT = "/home/b-ysonale/slidefetch/new_downloads/new"
DB = "/home/b-ysonale/slidefetch/rmp_course_ratings.db"
PROFS_DB = "/home/b-ysonale/slidefetch/rmp_results.db"
OUT = "/home/b-ysonale/slidefetch/new_downloads_rmp_matches.csv"

SEASON = {"fall", "spring", "summer", "winter", "january", "iap"}

# MIT department number -> RMP `department` values that are consistent with it.
# ("Science"/"Not Specified"/unknown are treated as neutral and never downgrade.)
DEPT_OK = {
    "1": {"Engineering"}, "2": {"Engineering"}, "3": {"Engineering", "Chemistry"},
    "4": {"Architecture"}, "5": {"Chemistry"}, "6": {"Computer Science", "Engineering"},
    "7": {"Biology"}, "8": {"Physics"}, "9": {"Psychology", "Biology"},
    "10": {"Engineering", "Chemistry"}, "11": {"Social Science", "Architecture"},
    "12": {"Geology"}, "14": {"Economics", "Social Science"},
    "15": {"Management", "Business", "Economics"}, "16": {"Engineering"},
    "17": {"Social Science"}, "18": {"Mathematics"}, "20": {"Engineering", "Biology"},
    "21": {"Anthropology", "Social Science", "Art History", "Philosophy", "Linguistics"},
    "22": {"Engineering", "Physics"}, "24": {"Linguistics", "Philosophy"},
}
NEUTRAL_DEPTS = {"", "Science", "Not Specified", "N/A", None}


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def parse_folder(name: str):
    """Return dict with code/compact-key/title for an ocw.mit.edu_ folder, or None."""
    if not name.startswith("ocw.mit.edu_"):
        return None
    rest = name[len("ocw.mit.edu_"):]
    toks = rest.split("-")
    if not toks:
        return None
    t0 = toks[0]
    if "." in t0:                       # dotted form, e.g. 14.126
        dept, num = t0.split(".", 1)
        title_toks = toks[1:]
    else:
        dept = t0
        num = toks[1] if len(toks) > 1 else ""
        title_toks = toks[2:]
    compact = digits(dept) + digits(num)
    tt = list(title_toks)
    while tt and (tt[-1].isdigit() or tt[-1] in SEASON):
        tt.pop()
    title = " ".join(tt).strip()
    return {
        "dir": name,
        "code": f"{dept}.{num}".upper(),
        "dept": dept.lower(),
        "dept_digits": digits(dept),
        "compact": compact,
        "title": title,
    }


def rmp_key(course: str):
    d = digits(course or "")
    return d if len(d) >= 3 else None


def main():
    # --- professor -> RMP department (for the consistency guard) ---------------
    pcon = sqlite3.connect(PROFS_DB)
    prof_dept = {name: (dep or "") for name, dep in pcon.execute(
        "SELECT matched_name, department FROM rmp_matches WHERE legacy_id IS NOT NULL")}

    # --- RMP MIT courses, keyed by concatenated digits ------------------------
    con = sqlite3.connect(DB)
    rmp = con.execute(
        """SELECT prof_name, course, num_ratings, avg_quality, avg_difficulty, profile_url
           FROM course_summary
           WHERE school LIKE '%Massachusetts Institute of Technology%'
             AND course IS NOT NULL"""
    ).fetchall()
    by_key: dict[str, list] = {}
    for prof, course, n, q, d, url in rmp:
        k = rmp_key(course)
        if k:
            by_key.setdefault(k, []).append((prof, course, n or 0, q, d, url))

    # --- Folders --------------------------------------------------------------
    folders = [parse_folder(n) for n in sorted(os.listdir(ROOT))]
    folders = [f for f in folders if f and len(f["compact"]) >= 3]

    rows = []
    for f in folders:
        hits = by_key.get(f["compact"])
        if not hits:
            continue
        # Letter-department course (no digits in dept, e.g. HST/IDS/EC): the bare
        # number digits collide across departments, so only trust a hit when the
        # RMP code literally carries the department letters (e.g. "HST951").
        if not f["dept_digits"]:
            hits = [h for h in hits if f["dept"] in h[1].lower()]
            if not hits:
                continue
        # Confidence: is at least one matched professor's RMP department
        # consistent with the MIT department number?
        ok = DEPT_OK.get(f["dept_digits"], set())
        consistent = any(
            (prof_dept.get(h[0], "") in NEUTRAL_DEPTS) or (prof_dept.get(h[0], "") in ok)
            for h in hits
        )
        confidence = "high" if (consistent or not f["dept_digits"]) else "low"
        tot = sum(h[2] for h in hits)
        wq = (sum((h[3] or 0) * h[2] for h in hits) / tot) if tot else None
        wd = (sum((h[4] or 0) * h[2] for h in hits) / tot) if tot else None
        rows.append({
            "folder": f["dir"],
            "course_code": f["code"],
            "compact_key": f["compact"],
            "course_title": f["title"],
            "rmp_courses": "; ".join(sorted({h[1] for h in hits})),
            "professors": "; ".join(sorted({h[0] for h in hits})),
            "total_ratings": tot,
            "avg_quality": round(wq, 2) if wq is not None else "",
            "avg_difficulty": round(wd, 2) if wd is not None else "",
            "confidence": confidence,
            "profile_urls": "; ".join(sorted({h[5] for h in hits if h[5]})),
        })

    rows.sort(key=lambda r: (r["confidence"] == "high", r["total_ratings"]), reverse=True)

    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                           ["folder", "course_code", "compact_key", "course_title",
                            "rmp_courses", "professors", "total_ratings",
                            "avg_quality", "avg_difficulty", "confidence", "profile_urls"])
        w.writeheader()
        w.writerows(rows)

    # --- Console report -------------------------------------------------------
    high = [r for r in rows if r["confidence"] == "high"]
    low = [r for r in rows if r["confidence"] == "low"]
    print(f"Downloaded MIT OCW folders parsed : {len(folders)}")
    print(f"Distinct RMP MIT course keys      : {len(by_key)}")
    print(f"Folders matched to RMP (high conf): {len(high)}")
    print(f"Low-confidence (dept mismatch)    : {len(low)}\n")
    print(f"{'MIT course':<11} {'ratings':>7}  {'qual':>4} {'diff':>4}  professor(s)  [RMP code(s)]  title")
    print("-" * 100)
    for r in high:
        print(f"{r['course_code']:<11} {r['total_ratings']:>7}  "
              f"{str(r['avg_quality']):>4} {str(r['avg_difficulty']):>4}  "
              f"{r['professors']}  [{r['rmp_courses']}]  {r['course_title'][:38]}")
    if low:
        print("\nLow-confidence (department of matched prof doesn't fit the course number):")
        for r in low:
            print(f"{r['course_code']:<11} {r['total_ratings']:>7}  "
                  f"{str(r['avg_quality']):>4} {str(r['avg_difficulty']):>4}  "
                  f"{r['professors']}  [{r['rmp_courses']}]  {r['course_title'][:38]}")
    print(f"\nCSV written: {OUT}")


if __name__ == "__main__":
    main()
