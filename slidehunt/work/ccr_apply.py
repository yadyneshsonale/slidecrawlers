#!/usr/bin/env python3
"""Apply the CCR matches from work/ccr_links.csv:
  1. write data/<dir>/ccr_url.txt  (just the link)
  2. DB: insert link rows (links, kind='ccr')
  3. DB: ensure a ccr_ratings row exists carrying the ccr_url
        (INSERT OR IGNORE so the existing rich rows are never clobbered)

Maps each data dir to its course_id via slide_decks.file_path.
"""
from __future__ import annotations

import csv
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DB = os.path.join(ROOT, "slidehunt.db")
LINKS_CSV = os.path.join(ROOT, "work", "ccr_links.csv")


def load_links():
    with open(LINKS_CSV, newline="", encoding="utf-8") as fh:
        return [(r["dir"], r["ccr_url"]) for r in csv.DictReader(fh)]


def dir_to_course_id(conn):
    """Map a data dir name -> course_id using slide_decks.file_path."""
    mapping: dict[str, str] = {}
    for course_id, fp in conn.execute(
        "SELECT course_id, file_path FROM slide_decks WHERE file_path IS NOT NULL"
    ):
        parent = os.path.basename(os.path.dirname(fp))
        mapping.setdefault(parent, course_id)
    return mapping


def main():
    rows = load_links()
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    d2c = dir_to_course_id(conn)

    files_written = 0
    links_added = 0
    ratings_added = 0
    unmapped = []

    for d, url in rows:
        # 1) ccr_url.txt in the course folder
        folder = os.path.join(DATA, d)
        if os.path.isdir(folder):
            with open(os.path.join(folder, "ccr_url.txt"), "w", encoding="utf-8") as fh:
                fh.write(url + "\n")
            files_written += 1

        # 2) + 3) database
        course_id = d2c.get(d)
        if not course_id:
            unmapped.append(d)
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO links (course_id, kind, url) VALUES (?, 'ccr', ?)",
            (course_id, url),
        )
        links_added += cur.rowcount
        cur = conn.execute(
            "INSERT OR IGNORE INTO ccr_ratings (course_id, ccr_url) VALUES (?, ?)",
            (course_id, url),
        )
        ratings_added += cur.rowcount

    conn.commit()

    print(f"ccr_url.txt files written : {files_written}/{len(rows)}")
    print(f"links rows added (kind=ccr): {links_added}")
    print(f"ccr_ratings rows added     : {ratings_added}")
    print(f"ccr_ratings total now      : "
          f"{conn.execute('SELECT COUNT(*) FROM ccr_ratings').fetchone()[0]}")
    if unmapped:
        print(f"\nNOT mapped to a course_id ({len(unmapped)}) "
              f"- ccr_url.txt still written:")
        for d in unmapped:
            print("   ", d)
    conn.close()


if __name__ == "__main__":
    main()
