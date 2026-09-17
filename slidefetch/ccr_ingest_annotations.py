#!/usr/bin/env python3
"""Fold human annotations (``new_annotations.tsv``) into the gold files.

``new_annotations.tsv`` columns: ``category``  (good|noslides), ``course_college``,
``url``, ``reason``. This upserts them into ``correct.txt`` (good links) and
``wrong.txt`` (no-usable-slides links), letting the new annotation SUPERSEDE any
existing verdict:

  * ``good``      -> written to correct.txt (link column); removed from wrong.txt.
  * ``noslides``  -> written to wrong.txt (link_1 column); removed from correct.txt.

``course_code`` and ``num_ratings`` are recovered authoritatively from
``ccr_courses.db`` (keyed on ``course_college``), never parsed from the paste.
Existing entries not present in the annotations are kept untouched and in order.
Both gold files are backed up first.
"""
from __future__ import annotations

import csv
import shutil
import sqlite3
import sys

COURSES_DB = "ccr_courses.db"
ANNOT = "new_annotations.tsv"
CORRECT = "correct.txt"
WRONG = "wrong.txt"
CORRECT_HEADER = "course_college\tcourse_code\tnum_ratings\tlink"


def load_annotations(con: sqlite3.Connection):
    good: dict[str, list[str]] = {}
    bad: dict[str, list[str]] = {}
    unknown: list[str] = []
    with open(ANNOT, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            cc = row["course_college"].strip()
            url = row["url"].strip()
            rec = con.execute(
                "SELECT course_code, num_ratings FROM courses WHERE course_college=?",
                (cc,),
            ).fetchone()
            if rec is None:
                unknown.append(cc)
                continue
            code, nr = rec[0] or "", rec[1] or 0
            entry = [cc, code, str(nr), url]
            (good if row["category"] == "good" else bad)[cc] = entry
    return good, bad, unknown


def read_rows(path: str, skip_header: bool):
    """Return (header_or_None, ordered_keys, {key: full_columns_list})."""
    header = None
    keys: list[str] = []
    rows: dict[str, list[str]] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    except FileNotFoundError:
        lines = []
    if skip_header and lines:
        header = lines[0]
        lines = lines[1:]
    for ln in lines:
        parts = ln.split("\t")
        key = parts[0].strip()
        if not key:
            continue
        if key not in rows:
            keys.append(key)
        rows[key] = parts
    return header, keys, rows


def write_rows(path: str, header: str | None, keys: list[str], rows: dict[str, list[str]]):
    out: list[str] = []
    if header is not None:
        out.append(header)
    for k in keys:
        out.append("\t".join(rows[k]))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def main() -> None:
    con = sqlite3.connect(COURSES_DB)
    good, bad, unknown = load_annotations(con)
    if unknown:
        print("ABORT: these course_college values are not in ccr_courses.db:")
        for u in unknown:
            print("  ", u)
        sys.exit(1)

    shutil.copy2(CORRECT, CORRECT + ".bak_before_213")
    shutil.copy2(WRONG, WRONG + ".bak_before_213")

    c_header, c_keys, c_rows = read_rows(CORRECT, skip_header=True)
    w_header, w_keys, w_rows = read_rows(WRONG, skip_header=False)
    if c_header is None:
        c_header = CORRECT_HEADER

    stats = {"good_new": 0, "good_upd": 0, "good_from_wrong": 0,
             "bad_new": 0, "bad_upd": 0, "bad_from_correct": 0}

    # GOOD -> correct.txt (supersede); drop from wrong.txt.
    for cc, entry in good.items():
        if cc in c_rows:
            stats["good_upd"] += 1
        else:
            c_keys.append(cc)
            stats["good_new"] += 1
        c_rows[cc] = entry
        if cc in w_rows:
            w_keys.remove(cc)
            del w_rows[cc]
            stats["good_from_wrong"] += 1

    # NOSLIDES -> wrong.txt (supersede, link_1 only); drop from correct.txt.
    for cc, entry in bad.items():
        if cc in w_rows:
            stats["bad_upd"] += 1
        else:
            w_keys.append(cc)
            stats["bad_new"] += 1
        w_rows[cc] = entry  # [cc, code, nr, url]
        if cc in c_rows:
            c_keys.remove(cc)
            del c_rows[cc]
            stats["bad_from_correct"] += 1

    write_rows(CORRECT, c_header, c_keys, c_rows)
    write_rows(WRONG, None, w_keys, w_rows)

    print(f"good annotations: {len(good)}  noslides annotations: {len(bad)}")
    print(f"  correct.txt: +{stats['good_new']} new, {stats['good_upd']} updated, "
          f"{stats['bad_from_correct']} removed (flipped to wrong)")
    print(f"  wrong.txt:   +{stats['bad_new']} new, {stats['bad_upd']} updated, "
          f"{stats['good_from_wrong']} removed (flipped to correct)")
    print(f"  correct.txt now {len(c_keys)} rows; wrong.txt now {len(w_keys)} rows")


if __name__ == "__main__":
    main()
