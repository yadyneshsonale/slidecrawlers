#!/usr/bin/env python3
"""Stage 0 — convert dataset_ccr_new.xlsx into ccr_rmp.db (`courses` table).

Source of truth is the workbook ONLY. Empty padding rows are skipped, the college
is parsed out of `course_college`, and `course_code` is cleaned. Idempotent:
re-running refreshes the base columns without clobbering downstream stage results.

    ./.venv/bin/python stage0_excel_to_db.py --example 5
    ./.venv/bin/python stage0_excel_to_db.py --rebuild
"""

from __future__ import annotations

import argparse
from collections import Counter
from urllib.parse import urlparse

from src import common


def _rough_kind(url: str | None) -> str:
    """Coarse link classification for the summary (Stage 1 owns the real logic)."""
    if not url:
        return "(none)"
    low = url.lower()
    host = urlparse(url).netloc.lower()
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith((".ppt", ".pptx")):
        return "ppt"
    if "github.com" in host and "/blob/" in low:
        return "gh_blob"
    if "github.io" in host or "github.com" in host:
        return "gh_page"
    return "html"


def upsert_base(conn, course: dict) -> bool:
    """Insert a course (base columns) or refresh them on conflict. Returns True if new."""
    key = course["course_college"] or course["course_slide_links"] or f"row{course['row_index']}"
    cur = conn.execute("SELECT 1 FROM courses WHERE course_college = ?", (key,))
    exists = cur.fetchone() is not None
    if exists:
        conn.execute(
            """UPDATE courses SET row_index=?, course_code=?, college_name=?,
                   num_ratings=?, course_slide_links=?, updated_at=?
               WHERE course_college=?""",
            (course["row_index"], course["course_code"], course["college_name"],
             course["num_ratings"], course["course_slide_links"], common.now(), key),
        )
    else:
        conn.execute(
            """INSERT INTO courses
                   (course_college, row_index, course_code, college_name,
                    num_ratings, course_slide_links, stage, updated_at)
               VALUES (?,?,?,?,?,?, 'loaded', ?)""",
            (key, course["row_index"], course["course_code"], course["college_name"],
             course["num_ratings"], course["course_slide_links"], common.now()),
        )
    return not exists


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--example", type=int, default=0,
                    help="preview the first N parsed rows and exit without writing")
    ap.add_argument("--limit", type=int, default=0, help="only load the first N courses")
    ap.add_argument("--rebuild", action="store_true", help="drop the courses table first")
    args = ap.parse_args(argv)

    cfg = common.load_config(args.config)
    courses = common.load_courses_from_xlsx(cfg["input_xlsx"])

    # De-duplicate on the natural key, keeping the first occurrence.
    seen, deduped = set(), []
    for c in courses:
        key = c["course_college"] or c["course_slide_links"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    dupes = len(courses) - len(deduped)
    courses = deduped
    if args.limit:
        courses = courses[: args.limit]

    kinds = Counter(_rough_kind(c["course_slide_links"]) for c in courses)
    colleges = {c["college_name"] for c in courses if c["college_name"]}

    print(common.rule("SOURCE"))
    print(f"  workbook            {cfg['input_xlsx']}")
    print(f"  populated courses   {len(courses)}   (duplicates dropped: {dupes})")
    print(f"  distinct colleges   {len(colleges)}")
    print(f"  link kinds          {dict(kinds)}")

    # ----- preview mode: show the parsed rows, write nothing --------------- #
    if args.example:
        sample = courses[: args.example]
        print("\n" + common.rule(f"EXAMPLE — first {len(sample)} parsed rows"))
        common.print_table(
            [
                {
                    "code": c["course_code"],
                    "college": c["college_name"],
                    "n": c["num_ratings"],
                    "kind": _rough_kind(c["course_slide_links"]),
                    "link": c["course_slide_links"],
                }
                for c in sample
            ],
            cols=[("code", 10), ("college", 24), ("n", 5), ("kind", 7), ("link", 34)],
        )
        print("\n" + common.rule("EXAMPLE — one full parsed record"))
        print(common.fmt_kv(sample[0]))
        print("\n(preview only — no database written; drop --example to build the DB)")
        return 0

    # ----- write mode ------------------------------------------------------ #
    if args.rebuild:
        conn = common.open_db(cfg["db_path"])
        conn.executescript("DROP TABLE IF EXISTS courses;")
        conn.commit()
        conn.close()
    conn = common.open_db(cfg["db_path"])
    new = 0
    for c in courses:
        new += upsert_base(conn, c)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    conn.close()

    print("\n" + common.rule("WRITE"))
    print(f"  database            {cfg['db_path']}")
    print(f"  inserted (new)      {new}")
    print(f"  refreshed (existing){len(courses) - new}")
    print(f"  courses in DB       {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
