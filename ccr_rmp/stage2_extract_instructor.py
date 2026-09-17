#!/usr/bin/env python3
"""Stage 2 — extract the instructor from each fetched slide (Qwen2.5-VL).

Uses the "smarter pick": scan the deck's page text for instructor cues and send
only the title page + top cue pages to the vision model (HTML uses a focused text
window). Strict JSON out; `null` when no instructor is present.

    ./.venv/bin/python stage2_extract_instructor.py --example 6   # preview, no write
    ./.venv/bin/python stage2_extract_instructor.py               # all pending
    ./.venv/bin/python stage2_extract_instructor.py --refresh     # redo everything
"""

from __future__ import annotations

import argparse
from collections import Counter

from src import common, instructor


def _spread(rows: list[dict], n: int) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r.get("slide_kind") or "?", []).append(r)
    picked: list[dict] = []
    while len(picked) < n and any(buckets.values()):
        for kind in list(buckets):
            if buckets[kind]:
                picked.append(buckets[kind].pop(0))
                if len(picked) >= n:
                    break
    return picked


def _show(course: dict, res: dict) -> None:
    dbg = res.get("debug", {})
    print("\n" + common.rule(f"{course['course_code']}  ·  {course['college_name']}"))
    print(f"  slide      {course.get('slide_kind')}  ({course.get('slide_status')})")
    if dbg.get("mode") == "image":
        print(f"  cue pages  {dbg.get('cue_pages')}   → sent pages {dbg.get('pages_sent')}")
        for p in dbg.get("images", []):
            print(f"               {p}")
    elif dbg.get("mode") == "text":
        print(f"  text sent  {dbg.get('focus_chars')} chars (focused on cue)")
    if dbg.get("raw"):
        print(f"  raw reply  {dbg['raw'].strip()[:160]}")
    if dbg.get("error"):
        print(f"  error      {dbg['error']}")
    conf = res.get("instructor_confidence")
    conf_s = f"{conf:.2f}" if isinstance(conf, float) else "·"
    print(f"  → INSTRUCTOR  {res.get('instructor') or 'NULL'}   "
          f"(conf {conf_s}, status {res.get('instructor_status')})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--example", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    cfg = common.load_config(args.config)
    conn = common.open_db(cfg["db_path"])

    if args.example:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM courses WHERE slide_status='ok' ORDER BY row_index"
        ).fetchall()]
        sample = _spread(rows, args.example)
        print(common.rule("STAGE 2 — instructor preview"))
        print(f"  showing {len(sample)} fetched courses (no DB write)")
        for course in sample:
            _show(course, instructor.extract(course, cfg))
        print("\n(preview only — drop --example to extract every pending course)")
        return 0

    sql = "SELECT * FROM courses WHERE slide_status='ok'"
    if not args.refresh:
        sql += " AND instructor_status IS NULL"
    sql += " ORDER BY row_index"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    if args.limit:
        rows = rows[: args.limit]

    print(common.rule("STAGE 2 — instructor extraction"))
    print(f"  courses to process  {len(rows)}")
    statuses: Counter = Counter()
    for i, course in enumerate(rows, start=1):
        res = instructor.run_one(conn, cfg, course)
        statuses[res["instructor_status"]] += 1
        print(f"  [{i:>3}/{len(rows)}] {course['course_code']:<10} "
              f"{(res.get('instructor') or 'NULL'):<28} {res['instructor_status']}")
    print("\n" + common.rule("SUMMARY"))
    print(f"  by status   {dict(statuses)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
