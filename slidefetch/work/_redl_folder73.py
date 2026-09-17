#!/usr/bin/env python3
"""One-off: re-download rated70 folder 73 (CS6360) with www.cs.wisc.edu -> pages.cs.wisc.edu.

The db08.htm page links the Ramakrishnan textbook slides; most use the dead
www.cs.wisc.edu host (only pages.cs.wisc.edu serves them). Rewrite + re-download in order.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dl_rated70 import _ext, _raw_github, download, download_html_slides  # noqa: E402
from slidefetch.cli import _gather  # noqa: E402

DEST = Path("/home/b-ysonale/slidefetch/rated70/73")
URL = "https://utdallas.edu/~muratk/courses/db08.htm"


def rewrite(u: str) -> str:
    return u.replace("www.cs.wisc.edu/%7Edbbook", "pages.cs.wisc.edu/%7Edbbook") \
            .replace("www.cs.wisc.edu/~dbbook", "pages.cs.wisc.edu/~dbbook")


# quick sanity check that pages host serves and www does not
for host in ("www.cs.wisc.edu", "pages.cs.wisc.edu"):
    u = f"http://{host}/%7Edbbook/openAccess/thirdEdition/slides/slides3ed-english/Ch1_Intro.pdf"
    try:
        r = requests.get(u, timeout=20, headers={"User-Agent": "slidefetch/1.0"}, stream=True)
        head = next(r.iter_content(5), b"")
        print(f"  {host:20} status={r.status_code} first5={head!r} ctype={r.headers.get('content-type')}")
        r.close()
    except Exception as e:  # noqa: BLE001
        print(f"  {host:20} ERROR {type(e).__name__}: {e}")

# clear existing deck files (keep xlsx if any)
for p in DEST.iterdir():
    if p.is_file() and not p.name.endswith(".xlsx"):
        p.unlink()

links = _gather(URL, 45000, False, follow=True, max_pages=6)
seen, uniq = set(), []
for l in links:
    ru = rewrite(l.url)
    if ru in seen:
        continue
    seen.add(ru)
    uniq.append((ru, l))

dl = sk = fail = 0
for seq, (ru, l) in enumerate(uniq, 1):
    try:
        if l.file_type in ("html", "htm"):
            r = download_html_slides(ru, DEST, seq, l.name, timeout_ms=45000)
        else:
            r = download(_raw_github(ru), DEST, _ext(ru), l.number, seq, l.name)
    except Exception as e:  # noqa: BLE001
        print(f"    dl error {ru[:70]}: {e}")
        fail += 1
        continue
    if r.skipped:
        sk += 1
    elif r.ok:
        dl += 1
    else:
        fail += 1
        print(f"    FAILED {ru}")

n = sum(1 for p in DEST.iterdir() if p.is_file() and not p.name.endswith(".xlsx"))
print(f"\nfolder 73: downloaded={dl} skipped={sk} failed={fail} -> {n} deck file(s) on disk")
