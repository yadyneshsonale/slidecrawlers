#!/usr/bin/env python3
"""Build a standalone DB for the courses present in downloads/ccr673, then
rename each subfolder to a sequential number 1..n and record the mapping.

New DB: ccr673_index.db, table `courses`:
  number        INTEGER  -- new folder name (1..n)
  course_college TEXT    -- e.g. "2GA3 - McMaster University"
  course_code   TEXT
  college_name  TEXT
  slide_link    TEXT     -- working_url (fallback original_url)
  original_url  TEXT
  working_url   TEXT
  original_folder TEXT   -- folder name before renaming
"""
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
CCR673 = os.path.join(BASE, "downloads", "ccr673")
SRC_DB = os.path.join(BASE, "ccr673_slides.db")
OUT_DB = os.path.join(BASE, "ccr673_index.db")


def main():
    # 1. Collect subfolders currently in the directory (sorted, stable order).
    dirs = sorted(
        d for d in os.listdir(CCR673)
        if os.path.isdir(os.path.join(CCR673, d))
    )
    print(f"Found {len(dirs)} subfolders in {CCR673}")

    # 2. Build lookup: basename(folder) -> row from ccr673_slides.db.
    src = sqlite3.connect(SRC_DB)
    src.row_factory = sqlite3.Row
    by_folder = {}
    for r in src.execute(
        "SELECT course_college, course_code, college_name, "
        "original_url, working_url, folder FROM courses"
    ):
        by_folder[os.path.basename(r["folder"])] = r
    src.close()

    # 3. Create the new DB.
    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)
    out = sqlite3.connect(OUT_DB)
    out.execute(
        """
        CREATE TABLE courses (
            number          INTEGER PRIMARY KEY,
            course_college  TEXT,
            course_code     TEXT,
            college_name    TEXT,
            slide_link      TEXT,
            original_url    TEXT,
            working_url     TEXT,
            original_folder TEXT
        )
        """
    )

    unmatched = []
    # 4. Assign numbers, insert, then rename folders. Two-phase rename to avoid
    #    collisions between old numeric-ish names and new ones.
    plan = []  # (number, old_name, row)
    for i, name in enumerate(dirs, start=1):
        row = by_folder.get(name)
        if row is None:
            unmatched.append(name)
        plan.append((i, name, row))

    for number, name, row in plan:
        if row is not None:
            slide_link = row["working_url"] or row["original_url"]
            out.execute(
                "INSERT INTO courses (number, course_college, course_code, "
                "college_name, slide_link, original_url, working_url, "
                "original_folder) VALUES (?,?,?,?,?,?,?,?)",
                (
                    number,
                    row["course_college"],
                    row["course_code"],
                    row["college_name"],
                    slide_link,
                    row["original_url"],
                    row["working_url"],
                    name,
                ),
            )
        else:
            out.execute(
                "INSERT INTO courses (number, course_college, slide_link, "
                "original_folder) VALUES (?,?,?,?)",
                (number, None, None, name),
            )
    out.commit()

    # 5. Rename folders. Phase A: move all to a temp prefix to avoid clashes.
    for number, name, row in plan:
        os.rename(
            os.path.join(CCR673, name),
            os.path.join(CCR673, f"__tmp_{number}"),
        )
    # Phase B: temp -> final numeric name.
    for number, name, row in plan:
        os.rename(
            os.path.join(CCR673, f"__tmp_{number}"),
            os.path.join(CCR673, str(number)),
        )

    out.close()
    print(f"Created {OUT_DB} with {len(plan)} rows.")
    print(f"Renamed {len(plan)} folders -> 1..{len(plan)}")
    if unmatched:
        print(f"WARNING: {len(unmatched)} folders had no DB match "
              f"(slide_link left NULL):")
        for u in unmatched:
            print("  ", u)


if __name__ == "__main__":
    main()
