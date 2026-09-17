#!/usr/bin/env python3
"""Download slides for the 70 rated courses (>=4 RMP ratings within 3 years of course_date)
using slidefetch, saving each course's decks into a folder named 01..N in list-appearance
order.

Order = the order courses appear in ccr_rmp/work/_course_dates.tsv (the pasted list, year
desc), filtered to the qualifying 70. Slide link = ccr_rmp_rated_all.db.courses.slide_link
(cleaned) with fallback to ccr_rmp.db.courses.course_slide_links.

    slidefetch/.venv/bin/python work/_dl_rated70.py --dry      # write manifest only
    slidefetch/.venv/bin/python work/_dl_rated70.py --limit 2  # download first 2
    slidefetch/.venv/bin/python work/_dl_rated70.py            # all 70
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from slidefetch.cli import _gather
from slidefetch.download import download, download_html_slides

CCR = "/home/b-ysonale/ccr_rmp"
DATES = f"{CCR}/work/_course_dates.tsv"
MAIN = f"{CCR}/ccr_rmp.db"
RATED = f"{CCR}/ccr_rmp_rated_all.db"
OUT = Path("/home/b-ysonale/slidefetch/rated70")


def ordered_70():
    # list order + year
    listed = []
    with open(DATES, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            cc, _, yr = line.rstrip("\n").rpartition("\t")
            listed.append((cc.strip(), int(yr.strip())))
    m = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    ra = sqlite3.connect(f"file:{RATED}?mode=ro", uri=True)
    link_clean = {cc: sl for cc, sl in ra.execute("SELECT course_college, slide_link FROM courses")}
    link_src = {cc: sl for cc, sl in m.execute("SELECT course_college, course_slide_links FROM courses")}
    out = []
    for cc, yr in listed:
        rows = m.execute("SELECT date FROM course_ratings_raw WHERE course_college=? AND date IS NOT NULL", (cc,)).fetchall()
        within = sum(1 for (d,) in rows if str(d)[:4].isdigit() and abs(int(str(d)[:4]) - yr) <= 3)
        if within >= 4:
            url = (link_clean.get(cc) or link_src.get(cc) or "").strip()
            out.append((cc, yr, within, url))
    m.close(); ra.close()
    return out


def _is_deck(u):
    return urlparse(u).path.lower().endswith((".pdf", ".ppt", ".pptx"))


def _raw_github(u):
    if "github.com" in u and "/blob/" in u:
        return u.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/", 1)
    return u


def _ext(u):
    p = urlparse(u).path.lower()
    for e in ("pptx", "ppt", "pdf"):
        if p.endswith("." + e):
            return e
    return "pdf"


def fetch_course(url, dest, timeout=45000):
    """Download all decks for one course URL into dest. Returns (#downloaded, #skipped)."""
    if _is_deck(url):
        du = _raw_github(url)
        name = urlparse(du).path.rsplit("/", 1)[-1]
        r = download(du, dest, _ext(du), None, 1, name)
        return (1 if (r.ok and not r.skipped) else 0), (1 if r.skipped else 0)
    try:
        links = _gather(url, timeout, False, follow=True, max_pages=6)
    except Exception as e:  # noqa: BLE001
        print(f"    gather error: {type(e).__name__}: {e}", flush=True)
        return 0, 0
    seen, uniq = set(), []
    for l in links:
        if l.url not in seen:
            seen.add(l.url)
            uniq.append(l)
    dl = sk = 0
    for seq, l in enumerate(uniq, 1):
        try:
            if l.file_type in ("html", "htm"):
                r = download_html_slides(l.url, dest, seq, l.name, timeout_ms=timeout)
            else:
                r = download(l.url, dest, l.file_type, l.number, seq, l.name)
        except Exception as e:  # noqa: BLE001
            print(f"    dl error {l.url[:60]}: {e}", flush=True)
            continue
        if r.skipped:
            sk += 1
        elif r.ok:
            dl += 1
    return dl, sk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    courses = ordered_70()
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["folder", "course_college", "course_date", "within3", "slide_link"])
        for i, (cc, yr, wi, url) in enumerate(courses, 1):
            w.writerow([f"{i:02d}", cc, yr, wi, url])
    print(f"{len(courses)} courses -> manifest at {OUT/'manifest.csv'}", flush=True)
    if args.dry:
        for i, (cc, yr, wi, url) in enumerate(courses, 1):
            print(f"  {i:02d}  {cc[:34]:34} {url[:60]}")
        return

    todo = courses[: args.limit] if args.limit else courses
    grand = 0
    for i, (cc, yr, wi, url) in enumerate(todo, 1):
        dest = OUT / f"{i:02d}"
        if not url:
            print(f"[{i:02d}/{len(todo)}] {cc}: NO LINK", flush=True)
            continue
        dl, sk = fetch_course(url, dest)
        grand += dl
        print(f"[{i:02d}/{len(todo)}] {cc[:34]:34} decks={dl} skipped={sk}  {url[:50]}", flush=True)
    print(f"\nDONE. downloaded {grand} deck files into {OUT}/NN/", flush=True)


if __name__ == "__main__":
    main()
