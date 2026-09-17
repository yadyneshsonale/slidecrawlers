#!/usr/bin/env python3.11
"""Download slide decks for CCR courses with >4 reviews, using slidefetch.

For every course in ccr_rmp.db with num_ratings > 4 and an http(s) slide link
(646 courses), this driver:

  1. Visits the course slide URL and looks for slide decks (PDF/PPT/PPTX or a
     client-side HTML deck) listed on the page.
  2. If none are present, it reduces the link to the previous ``/`` (drops the
     last path segment) and tries again -- repeating until slides are found or
     the URL is reduced past its last path segment (treated as "invalid").
  3. When slides are found, every deck on that page is downloaded via slidefetch
     into a per-course folder under ``downloads/ccr673/``.
  4. Results (course name + the working slide-download link + counts) are written
     to a fresh SQLite DB ``ccr673_slides.db``.

Resumable: courses already recorded with a terminal status are skipped. Safe to
re-run. Intended to be launched inside a tmux session.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from slidefetch.download import DownloadResult, download, download_html_slides, sanitize
from slidefetch.extract import SlideLink, find_index_links, find_slides
from slidefetch.fetch import fetch

SRC_DB = Path.home() / "ccr_rmp" / "ccr_rmp.db"
OUT_ROOT = HERE / "downloads" / "ccr673"
DEST_DB = HERE / "ccr673_slides.db"

SLIDE_EXTS = (".pdf", ".ppt", ".pptx")


def log(*a):
    print(*a, flush=True)


# --------------------------------------------------------------------------- #
# URL reduction
# --------------------------------------------------------------------------- #
def reduction_candidates(url: str):
    """Yield the original URL, then progressively strip the last ``/`` segment.

    ``https://h/a/b/c.html`` -> ``https://h/a/b/`` -> ``https://h/a/`` and stops
    (the bare host root is intentionally NOT scanned to avoid pulling unrelated
    site-wide files). Yields the original URL first.
    """
    yield url
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    # Keep stripping until only one path segment would remain; never descend to
    # the bare domain root (segs == []), which is too broad to be a course page.
    # Also stop before a bare ``~user`` directory (a professor's personal root,
    # not a course), which otherwise pulls unrelated personal files.
    while len(segs) > 1:
        segs = segs[:-1]
        if len(segs) == 1 and segs[0].startswith("~"):
            break
        path = "/" + "/".join(segs) + "/"
        yield urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _ends_with_slide(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(SLIDE_EXTS)


# --------------------------------------------------------------------------- #
# Destination DB
# --------------------------------------------------------------------------- #
def open_dest_db() -> sqlite3.Connection:
    DEST_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DEST_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS courses (
            course_college TEXT PRIMARY KEY,
            course_code    TEXT,
            college_name   TEXT,
            num_ratings    REAL,
            original_url   TEXT,
            working_url    TEXT,
            reductions     INTEGER,
            folder         TEXT,
            slides_found   INTEGER DEFAULT 0,
            downloaded     INTEGER DEFAULT 0,
            skipped        INTEGER DEFAULT 0,
            duplicate      INTEGER DEFAULT 0,
            failed         INTEGER DEFAULT 0,
            status         TEXT,
            error          TEXT,
            updated_at     TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            course_college TEXT,
            seq            INTEGER,
            slide_url      TEXT,
            file_type      TEXT,
            saved_path     TEXT,
            sha256         TEXT,
            status         TEXT,
            error          TEXT,
            created_at     TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return conn


def already_done(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute(
        "SELECT status FROM courses WHERE course_college=?", (key,)
    ).fetchone()
    return bool(row) and row[0] in ("saved", "no-slides")


# --------------------------------------------------------------------------- #
# Per-course processing
# --------------------------------------------------------------------------- #
def find_on_page(url: str, timeout_ms: int, follow: bool, max_pages: int):
    """Fetch *url* and return (slide_links, fetch_ok). Never raises.

    A URL that is itself a slide file (``.pdf`` / ``.ppt`` / ``.pptx``) is
    returned as a single direct-download link -- Playwright cannot navigate to a
    binary file, so we never try to render it.
    """
    if _ends_with_slide(url):
        path = urlsplit(url).path
        name = path.rsplit("/", 1)[-1] or "slide"
        ext = name.rsplit(".", 1)[-1].lower()
        link = SlideLink(url=url, text=name, file_type=ext, number=None,
                         reason="direct-file", name=name)
        return [link], True
    try:
        res = fetch(url, timeout_ms=timeout_ms)
    except Exception as err:  # noqa: BLE001
        log(f"    [fetch] error: {err}")
        return [], False
    if res.status and res.status >= 400:
        log(f"    [fetch] status={res.status}")
        return [], False
    links = find_slides(res.html, url, raw_html=res.raw_html)
    if links:
        return links, True
    if follow:
        subs = find_index_links(res.html, url, raw_html=res.raw_html)[:max_pages]
        collected, seen = [], set()
        if subs:
            log(f"    [follow] no slides here; checking {len(subs)} sub-page(s)")
        for sub in subs:
            try:
                sres = fetch(sub, timeout_ms=timeout_ms)
            except Exception:  # noqa: BLE001
                continue
            for s in find_slides(sres.html, sub, raw_html=sres.raw_html):
                if s.url not in seen:
                    seen.add(s.url)
                    collected.append(s)
        if collected:
            return collected, True
    return [], True


def download_links(links, dest: Path, timeout_ms: int):
    """Download every slide link into *dest*; return (results, counters)."""
    dest.mkdir(parents=True, exist_ok=True)
    downloaded = skipped = failed = duplicate = 0
    seen_hashes: dict[str, str] = {}
    results: list[tuple[int, object, DownloadResult]] = []
    for seq, link in enumerate(links, start=1):
        if link.file_type in ("html", "htm"):
            res = download_html_slides(link.url, dest, seq, link.name,
                                       timeout_ms=timeout_ms)
        else:
            res = download(link.url, dest, link.file_type, link.number, seq,
                           link.name)
        if res.skipped:
            skipped += 1
            log(f"    = skip   {Path(res.path).name}")
        elif res.ok:
            if res.sha256 and res.sha256 in seen_hashes:
                try:
                    Path(res.path).unlink()
                except OSError:
                    pass
                duplicate += 1
                res = DownloadResult(res.url, res.path, res.sha256,
                                     res.file_type, True, error="duplicate")
                log(f"    ~ dup    {Path(res.path).name}")
            else:
                if res.sha256:
                    seen_hashes[res.sha256] = Path(res.path).name
                downloaded += 1
                log(f"    + saved  {Path(res.path).name}")
        else:
            failed += 1
            log(f"    ! fail   {link.url}  ({res.error})")
        results.append((seq, link, res))
    return results, (downloaded, skipped, duplicate, failed)


def process_course(conn, course, timeout_ms, follow, max_pages):
    key = course["course_college"]
    original = course["course_slide_links"]
    slug = sanitize(key) or "course"
    dest = OUT_ROOT / slug

    log(f"\n### {key}  (ratings={course['num_ratings']})")
    log(f"    url: {original}")

    found_links = None
    working_url = None
    reductions = -1
    results = []
    downloaded = skipped = duplicate = failed = 0
    # Remember the first candidate that at least *listed* slides, so we can
    # report it even if nothing ultimately downloaded.
    first_listing = None  # (links, url, reduction, results, counters)

    for i, cand in enumerate(reduction_candidates(original)):
        if i:
            log(f"    [reduce {i}] {cand}")
        links, _ = find_on_page(cand, timeout_ms, follow, max_pages)
        if not links:
            continue
        log(f"    [found] {len(links)} slide link(s) at reduction {i}")
        res, counters = download_links(links, dest, timeout_ms)
        d, s, dup, f = counters
        if first_listing is None:
            first_listing = (links, cand, i, res, counters)
        # Success = at least one deck actually obtained (new or already present).
        if d or s:
            found_links, working_url, reductions = links, cand, i
            results = res
            downloaded, skipped, duplicate, failed = counters
            break
        log("    [retry] links found but none downloaded; reducing further")

    if found_links is None and first_listing is not None:
        # Slides were listed somewhere but none could be downloaded; keep the
        # first such attempt for the record (status will be partial/no-downloads).
        found_links, working_url, reductions, results, counters = first_listing
        downloaded, skipped, duplicate, failed = counters

    if found_links is None:
        conn.execute(
            """INSERT OR REPLACE INTO courses
               (course_college, course_code, college_name, num_ratings,
                original_url, working_url, reductions, folder, slides_found,
                downloaded, skipped, duplicate, failed, status, error,
                updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (key, course["course_code"], course["college_name"],
             course["num_ratings"], original, None, -1, str(dest),
             0, 0, 0, 0, 0, "no-slides", "no slides on any reduced URL"),
        )
        conn.commit()
        log("    -> no slides found on any reduced URL")
        return

    conn.execute("DELETE FROM files WHERE course_college=?", (key,))
    for seq, link, res in results:
        conn.execute(
            """INSERT INTO files
               (course_college, seq, slide_url, file_type, saved_path,
                sha256, status, error)
               VALUES (?,?,?,?,?,?,?,?)""",
            (key, seq, link.url, res.file_type, res.path, res.sha256,
             "skipped" if res.skipped else ("ok" if res.ok else "failed"),
             res.error),
        )

    status = "saved" if downloaded else ("partial" if failed else "no-downloads")
    conn.execute(
        """INSERT OR REPLACE INTO courses
           (course_college, course_code, college_name, num_ratings,
            original_url, working_url, reductions, folder, slides_found,
            downloaded, skipped, duplicate, failed, status, error, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (key, course["course_code"], course["college_name"],
         course["num_ratings"], original, working_url, reductions, str(dest),
         len(found_links), downloaded, skipped, duplicate, failed, status,
         "" if failed == 0 else f"{failed} file(s) failed"),
    )
    conn.commit()
    log(f"    -> {status}: {downloaded} new, {skipped} skip, "
        f"{duplicate} dup, {failed} fail  ({working_url})")


def load_courses(limit=None):
    src = sqlite3.connect(SRC_DB)
    src.row_factory = sqlite3.Row
    q = """SELECT course_college, course_code, college_name, num_ratings,
                  course_slide_links
           FROM courses
           WHERE CAST(num_ratings AS INTEGER) > 4
             AND course_slide_links LIKE 'http%'
           ORDER BY course_college"""
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = src.execute(q).fetchall()
    src.close()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="process only the first N courses (testing)")
    ap.add_argument("--timeout", type=int, default=30000,
                    help="per-page navigation timeout in ms")
    ap.add_argument("--no-follow", action="store_true",
                    help="do not follow lecture/schedule sub-pages")
    ap.add_argument("--max-pages", type=int, default=40,
                    help="max sub-pages to follow per candidate")
    ap.add_argument("--retry-failed", action="store_true",
                    help="also reprocess courses previously marked no-slides")
    args = ap.parse_args(argv)

    conn = open_dest_db()
    courses = load_courses(args.limit)
    total = len(courses)
    log(f"CCR >4-review slide fetch: {total} course(s)")
    log(f"output folder: {OUT_ROOT}")
    log(f"metadata db:   {DEST_DB}\n")

    done = skipped = 0
    for idx, course in enumerate(courses, start=1):
        key = course["course_college"]
        if not args.retry_failed and already_done(conn, key):
            skipped += 1
            continue
        if args.retry_failed:
            row = conn.execute(
                "SELECT status FROM courses WHERE course_college=?", (key,)
            ).fetchone()
            if row and row[0] == "saved":
                skipped += 1
                continue
        log(f"===== [{idx}/{total}] =====")
        try:
            process_course(conn, course, args.timeout,
                           not args.no_follow, args.max_pages)
        except Exception as err:  # noqa: BLE001
            log(f"    !! unexpected error: {err}")
            conn.execute(
                """INSERT OR REPLACE INTO courses
                   (course_college, course_code, college_name, num_ratings,
                    original_url, working_url, reductions, folder, status,
                    error, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (key, course["course_code"], course["college_name"],
                 course["num_ratings"], course["course_slide_links"], None, -1,
                 str(OUT_ROOT / (sanitize(key) or "course")), "error", str(err)),
            )
            conn.commit()
        done += 1
        time.sleep(0.5)

    log(f"\nDONE. processed={done} skipped(existing)={skipped} total={total}")
    conn.close()


if __name__ == "__main__":
    main()
