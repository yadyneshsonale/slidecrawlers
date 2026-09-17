#!/usr/bin/env python3
"""Stage 1 — fetch each course's slide link into an artifact.

For every course with a link we download it and produce EITHER rendered page
images (PDF / PPT) for the vision model OR extracted visible text (HTML / GitHub
page) for the text model. Results land in the `slide_*` columns.

    ./.venv/bin/python stage1_fetch_slide.py --example 6   # preview, no DB write
    ./.venv/bin/python stage1_fetch_slide.py               # fetch all pending
    ./.venv/bin/python stage1_fetch_slide.py --refresh     # re-fetch everything
"""

from __future__ import annotations

import argparse
from collections import Counter
from urllib.parse import urlparse

from src import common, fetch


def _guess(url: str | None) -> str:
    if not url:
        return "(none)"
    low = url.lower()
    host = urlparse(url).netloc.lower()
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith((".ppt", ".pptx")):
        return "ppt"
    if "github.io" in host or "github.com" in host:
        return "gh_page"
    return "html"


def _spread(rows: list[dict], n: int) -> list[dict]:
    """Pick up to n rows covering as many link kinds as possible."""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(_guess(r["course_slide_links"]), []).append(r)
    picked: list[dict] = []
    while len(picked) < n and any(buckets.values()):
        for kind in list(buckets):
            if buckets[kind]:
                picked.append(buckets[kind].pop(0))
                if len(picked) >= n:
                    break
    return picked


def _pending(conn, refresh: bool, limit: int) -> list[dict]:
    sql = "SELECT * FROM courses WHERE course_slide_links IS NOT NULL"
    if not refresh:
        sql += " AND slide_status IS NULL"
    sql += " ORDER BY row_index"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    return rows[:limit] if limit else rows


def run_one(conn, cfg: dict, course: dict) -> dict:
    """Fetch one course's slide and persist the slide_* columns."""
    res = fetch.fetch_slide(course, cfg)
    common.update_course(
        conn, course["course_college"],
        slide_url_used=res["slide_url_used"],
        slide_kind=res["slide_kind"],
        slide_artifact=res["slide_artifact"],
        slide_status=res["slide_status"],
        stage="fetched",
    )
    return res


def _show(course: dict, res: dict) -> None:
    print("\n" + common.rule(f"{course['course_code']}  ·  {course['college_name']}"))
    print(f"  link       {course['course_slide_links']}")
    print(f"  → kind     {res['slide_kind']}    status: {res['slide_status']}")
    if res["slide_url_used"] and res["slide_url_used"] != course["course_slide_links"]:
        print(f"  → used     {res['slide_url_used']}")
    ex = res["example"]
    if ex["mode"] == "image":
        print(f"  → images   {len(ex['images'])} page(s)")
        for p in ex["images"]:
            print(f"               {p}")
    elif ex["mode"] == "text":
        print(f"  → text     {ex['chars']} chars extracted; first lines:")
        for ln in ex["snippet"].splitlines()[:8]:
            print(f"               {ln[:78]}")
    elif ex["mode"] == "error":
        print(f"  → error    {ex['error']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--example", type=int, default=0,
                    help="fetch a spread of N courses and show them; no DB write")
    ap.add_argument("--limit", type=int, default=0, help="process only the first N")
    ap.add_argument("--refresh", action="store_true", help="re-fetch already-fetched courses")
    args = ap.parse_args(argv)

    cfg = common.load_config(args.config)
    conn = common.open_db(cfg["db_path"])

    # ----- preview mode ---------------------------------------------------- #
    if args.example:
        allrows = [dict(r) for r in conn.execute(
            "SELECT * FROM courses WHERE course_slide_links IS NOT NULL ORDER BY row_index"
        ).fetchall()]
        sample = _spread(allrows, args.example)
        print(common.rule("STAGE 1 — fetch preview"))
        print(f"  showing {len(sample)} courses (spread across link kinds); no DB write")
        for course in sample:
            res = fetch.fetch_slide(course, cfg)
            _show(course, res)
        print("\n(preview only — drop --example to fetch every pending course)")
        return 0

    # ----- write mode ------------------------------------------------------ #
    rows = _pending(conn, args.refresh, args.limit)
    print(common.rule("STAGE 1 — fetch"))
    print(f"  courses to fetch    {len(rows)}")
    kinds: Counter = Counter()
    statuses: Counter = Counter()
    for i, course in enumerate(rows, start=1):
        res = run_one(conn, cfg, course)
        kinds[res["slide_kind"]] += 1
        statuses[res["slide_status"]] += 1
        print(f"  [{i:>3}/{len(rows)}] {course['course_code']:<10} "
              f"{res['slide_kind'] or '-':<6} {res['slide_status']}")

    print("\n" + common.rule("SUMMARY"))
    print(f"  by kind     {dict(kinds)}")
    print(f"  by status   {dict(statuses)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
