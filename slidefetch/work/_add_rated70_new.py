#!/usr/bin/env python3
"""One-off: add 3 new courses to rated70 (download decks + append manifest).

Run from slidefetch root:  PYTHONPATH=. .venv/bin/python work/_add_rated70_new.py [--dry]

Already-present (skipped, verified to have decks): CS6375=folder36, ECON2030=folder65,
OIM3690(=MIS3690 key)=folder43.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # import sibling _dl_rated70
from _dl_rated70 import fetch_course  # noqa: E402

OUT = Path("/home/b-ysonale/slidefetch/rated70")
MANIFEST = OUT / "manifest.csv"

# folder, course_college (EXACT ccr_rmp.db key), course_date(year), within3, slide_link
NEW = [
    ("71", "CSCI3022 - University of Colorado Boulder", 2021, 4,
     "https://home.cs.colorado.edu/~ketelsen/files/courses/csci3022/slides/"),
    ("72", "MACM101 - Simon Fraser University", 2016, 7,
     "https://www2.cs.sfu.ca/CourseCentral/101.MACM/ramesh/lectureslides.html"),
    ("73", "CS6360 - University of Texas at Dallas", 2019, 20,
     "https://utdallas.edu/~muratk/courses/db08.htm"),
]


def existing_folders():
    if not MANIFEST.exists():
        return set()
    with open(MANIFEST, encoding="utf-8") as f:
        return {r["folder"] for r in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    have = existing_folders()
    results = []
    for folder, cc, yr, wi, url in NEW:
        if folder in have:
            print(f"[skip] folder {folder} already in manifest ({cc})", flush=True)
            continue
        dest = OUT / folder
        dest.mkdir(parents=True, exist_ok=True)
        print(f"\n=== folder {folder}: {cc}\n    {url}", flush=True)
        if args.dry:
            continue
        dl, sk = fetch_course(url, dest)
        n = sum(1 for p in dest.iterdir() if p.is_file() and not p.name.endswith(".xlsx"))
        print(f"    downloaded={dl} skipped={sk} -> {n} deck file(s) on disk", flush=True)
        results.append((folder, cc, yr, wi, url, n))

    # Append manifest rows for folders that got decks and are not yet listed
    to_add = [(f, cc, yr, wi, url) for (f, cc, yr, wi, url, n) in results if n > 0 and f not in have]
    if to_add and not args.dry:
        with open(MANIFEST, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for folder, cc, yr, wi, url in to_add:
                w.writerow([folder, cc, yr, wi, url])
        print(f"\nappended {len(to_add)} row(s) to {MANIFEST}", flush=True)
    else:
        print("\nno manifest rows appended", flush=True)

    print("\nSUMMARY:")
    for (f, cc, yr, wi, url, n) in results:
        print(f"  {f}  {cc[:40]:40} decks={n}")


if __name__ == "__main__":
    main()
