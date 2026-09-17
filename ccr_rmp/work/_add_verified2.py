#!/usr/bin/env python3
"""One-off: import NEW confirmed courses from slidefetch/ccr_verified.db into ccr_rmp.db
and run stages 1-4 (fetch slide -> extract instructor -> find RMP -> course ratings).

Filter: verdict='confirmed' AND correct_url starts with http AND course_college NOT already
in `courses`. correct_url is used as course_slide_links. row_index = MAX(row_index)+n so the
new rows sort after existing ones. Non-destructive (upsert_base only touches base cols).

    ./.venv/bin/python work/_add_verified2.py --dry     # preview the NEW set, no writes
    ./.venv/bin/python work/_add_verified2.py           # add + run all 4 stages
    ./.venv/bin/python work/_add_verified2.py --limit 5 # first 5 only (smoke test)
"""
from __future__ import annotations

import argparse
import sqlite3

from src import codes, common, instructor as s2
import stage0_excel_to_db as s0
import stage1_fetch_slide as s1
import stage3_rmp_find as s3
import stage4_rmp_ratings as s4

VERIFIED = "/home/b-ysonale/slidefetch/ccr_verified.db"


def fetch_course(conn, cc):
    cur = conn.execute("SELECT * FROM courses WHERE course_college=?", (cc,))
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


def load_new(conn):
    existing = {r[0] for r in conn.execute("SELECT course_college FROM courses")}
    maxidx = conn.execute("SELECT MAX(row_index) FROM courses").fetchone()[0] or 0
    v = sqlite3.connect(f"file:{VERIFIED}?mode=ro", uri=True)
    rows = v.execute(
        "SELECT course_college, course_code, num_ratings, correct_url "
        "FROM verified_labels WHERE verdict='confirmed'"
    ).fetchall()
    v.close()
    new, n = [], 0
    for cc, code, nr, url in rows:
        if not (url or "").startswith("http") or cc in existing:
            continue
        n += 1
        new.append({
            "course_college": cc,
            "row_index": maxidx + n,
            "course_code": code or codes.clean_code(None, cc),
            "college_name": codes.split_course_college(cc)[1],
            "num_ratings": nr,
            "course_slide_links": url,
        })
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cfg = common.load_config()
    conn = common.open_db(cfg["db_path"])
    new = load_new(conn)
    if args.limit:
        new = new[: args.limit]
    print(f"NEW confirmed courses to add: {len(new)}", flush=True)
    if args.dry:
        for c in new[:25]:
            print(f"  {c['course_college']}  ->  {c['course_slide_links'][:70]}")
        return

    added = sum(1 for c in new if s0.upsert_base(conn, c))
    conn.commit()
    print(f"upserted {added} new base rows (row_index {new[0]['row_index'] if new else '-'}..)", flush=True)

    stages = [("slide", s1.run_one), ("instr", s2.run_one),
              ("rmp", s3.run_one), ("rate", s4.run_one)]
    tally = {"slide_ok": 0, "instr_found": 0, "rmp_matched": 0}
    for i, c in enumerate(new, 1):
        cc = c["course_college"]
        for name, fn in stages:
            course = fetch_course(conn, cc)
            if course is None:
                break
            try:
                fn(conn, cfg, course)
            except Exception as e:  # noqa: BLE001
                print(f"[{i}/{len(new)}] {cc} stage {name} ERROR: {type(e).__name__}: {e}", flush=True)
        row = fetch_course(conn, cc) or {}
        if (row.get("slide_status") or "") in ("ok", "downloaded", "fetched"):
            tally["slide_ok"] += 1
        if row.get("instructor"):
            tally["instr_found"] += 1
        if row.get("rmp_matched_name"):
            tally["rmp_matched"] += 1
        if i % 10 == 0 or i == len(new):
            print(f"[{i}/{len(new)}] {tally}", flush=True)
    print(f"ALL DONE. {tally}", flush=True)


if __name__ == "__main__":
    main()
