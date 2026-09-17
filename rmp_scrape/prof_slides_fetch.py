#!/usr/bin/env python3.11
"""For the top-rated RMP professors, find their course slide decks and download.

Pipeline, working through professors in descending order of number of ratings
(the ``professors`` table populated by scrape.py):

  1. For each distinct course a professor teaches (``professor_courses``), run a
     web search for ``"<first> <last> <university> <course> course slides"``
     via the local SearXNG instance.
  2. Visit the top result pages (skipping aggregators / non-university hosts),
     collect slide-deck links (PDF/PPT/PPTX or client-side HTML decks), and
     follow one hop into lecture/schedule sub-pages when a landing page lists
     none directly.
  3. Check whether those links form a *sequential* set of slides -- i.e. several
     decks numbered consecutively (Lecture 1, 2, 3 ...). Only then is the page
     treated as a genuine course deck.
  4. If sequential, download every deck into a fresh per-professor/per-course
     directory under ``downloads/prof_slides/``.

Progress is stored in ``prof_slides.db`` and the crawl is resumable: any
(professor, course) already recorded with a terminal status is skipped. Runs
best inside a tmux session; requires the slidefetch python3.11 venv (bs4 +
playwright) and the SearXNG instance on http://localhost:8080.

    slidefetch/.venv/bin/python rmp_scrape/prof_slides_fetch.py --limit-profs 1
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
SLIDEFETCH = Path.home() / "slidefetch"
sys.path.insert(0, str(SLIDEFETCH))

from slidefetch.download import DownloadResult, download, download_html_slides, sanitize
from slidefetch.extract import SlideLink, find_index_links, find_slides
from slidefetch.fetch import fetch
from slidefetch.search import web_search
from slidefetch.urls import is_university

# SearXNG JSON backend (same one the ccr pipeline uses).
sys.path.insert(0, str(SLIDEFETCH))
from ccr_slide_search import searxng_search  # noqa: E402

PROF_DB = HERE / "rmp_professors.db"
OUT_ROOT = HERE / "downloads" / "prof_slides"
DEST_DB = HERE / "prof_slides.db"
CACHE_DIR = HERE / ".search_cache"

SLIDE_EXTS = (".pdf", ".ppt", ".pptx")
# Course "names" from RMP that carry no useful search signal.
JUNK_COURSES = {"class", "other", "n/a", "na", "none", "lab", "lecture"}

# Discipline ordering. A professor belongs to a discipline when any keyword is a
# substring of their RMP ``department``. Processed in this order (CS first), and
# each professor is assigned to the FIRST discipline they match, so e.g. an
# "Electrical & Computer Engineering" prof lands in ece, not the broad
# "engineering" bucket. Within a discipline, professors are taken by rating count.
DISCIPLINE_ORDER = ["cs", "ece", "math", "stats", "physics",
                    "engineering", "chemistry", "economics"]
DISCIPLINE_KEYWORDS = {
    "cs": ("computer science", "computing", "information technology",
           "information systems", "software engineering", "informatics",
           "information science", "data science"),
    "ece": ("electrical", "computer engineering", "electronic"),
    "math": ("mathematic",),
    "stats": ("statistic",),
    "physics": ("physics",),
    "engineering": ("engineering",),
    "chemistry": ("chemistry", "chemical"),
    "economics": ("economic",),
}


def discipline_of(department: str | None) -> str | None:
    """First discipline (in DISCIPLINE_ORDER) whose keywords match *department*."""
    dep = (department or "").lower()
    if not dep:
        return None
    for key in DISCIPLINE_ORDER:
        if any(kw in dep for kw in DISCIPLINE_KEYWORDS[key]):
            return key
    return None


def log(*a):
    print(*a, flush=True)


def load_tavily_key() -> bool:
    """Ensure TAVILY_API_KEY is in the environment.

    Falls back to reading ``~/.tavily_key`` (a file the user writes directly, so
    the secret never passes through anything else). Returns True if a key is set.
    """
    import os
    if os.environ.get("TAVILY_API_KEY", "").strip():
        return True
    key_file = Path.home() / ".tavily_key"
    if key_file.is_file():
        key = key_file.read_text("utf-8").strip()
        if key:
            os.environ["TAVILY_API_KEY"] = key
            return True
    return False


# --------------------------------------------------------------------------- #
# Web search (Tavily if a key is set, else SearXNG with a Bing fallback)
# --------------------------------------------------------------------------- #
def web_results(session, query: str, engine: str) -> list[str]:
    """Return result URLs for *query*.

    Order: Tavily API (if ``TAVILY_API_KEY`` is set — clean, agent-grade
    results), else the local SearXNG instance. SearXNG is queried with the
    instance default engines first (picks up Brave/DDG when they are not
    rate-limited); if that yields nothing it retries pinned to Bing, the only
    general engine currently reachable from this host.
    """
    import os
    if os.environ.get("TAVILY_API_KEY", "").strip():
        try:
            hits = web_search(query, limit=12, cache_dir=CACHE_DIR,
                              use_browser=False)
            if hits:
                return [h.url for h in hits]
        except Exception:  # noqa: BLE001 — fall through to SearXNG
            pass
    urls = searxng_search(session, query, engines=engine)
    if not urls and engine != "bing":
        urls = searxng_search(session, query, engines="bing")
    return urls


# --------------------------------------------------------------------------- #
# Sequential-slide detection
# --------------------------------------------------------------------------- #
def longest_run(numbers: list[int]) -> int:
    """Length of the longest run of consecutive integers in *numbers*."""
    s = sorted(set(numbers))
    if not s:
        return 0
    best = run = 1
    for prev, cur in zip(s, s[1:]):
        run = run + 1 if cur == prev + 1 else 1
        best = max(best, run)
    return best


def is_sequential(links: list[SlideLink], min_len: int = 3) -> tuple[bool, int]:
    """True when the slide links are numbered as a consecutive lecture series.

    Requires at least *min_len* decks whose extracted numbers include a run of
    consecutive values (e.g. Lecture 1, 2, 3). Returns (ok, run_length).
    """
    nums = [ln.number for ln in links if ln.number is not None]
    run = longest_run(nums)
    return run >= min_len, run


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def open_dest_db() -> sqlite3.Connection:
    DEST_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DEST_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attempts (
            professor_id   TEXT,
            course_name    TEXT,
            professor_name TEXT,
            school_name    TEXT,
            num_ratings    INTEGER,
            query          TEXT,
            num_results    INTEGER DEFAULT 0,
            slides_found   INTEGER DEFAULT 0,
            sequential     INTEGER DEFAULT 0,
            run_length     INTEGER DEFAULT 0,
            working_url    TEXT,
            folder         TEXT,
            downloaded     INTEGER DEFAULT 0,
            skipped        INTEGER DEFAULT 0,
            failed         INTEGER DEFAULT 0,
            status         TEXT,
            error          TEXT,
            updated_at     TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (professor_id, course_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            professor_id   TEXT,
            course_name    TEXT,
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


def already_done(conn: sqlite3.Connection, pid: str, course: str) -> bool:
    row = conn.execute(
        "SELECT status FROM attempts WHERE professor_id=? AND course_name=?",
        (pid, course),
    ).fetchone()
    return bool(row) and row[0] not in (None, "", "error")


# --------------------------------------------------------------------------- #
# Slide discovery on a page (reuses the ccr673 approach)
# --------------------------------------------------------------------------- #
def _ends_with_slide(url: str) -> bool:
    from urllib.parse import urlsplit
    return urlsplit(url).path.lower().endswith(SLIDE_EXTS)


def find_on_page(url: str, timeout_ms: int, follow: bool, max_pages: int):
    """Fetch *url* and return slide links (following one hop if needed)."""
    if _ends_with_slide(url):
        from urllib.parse import urlsplit
        name = urlsplit(url).path.rsplit("/", 1)[-1] or "slide"
        ext = name.rsplit(".", 1)[-1].lower()
        return [SlideLink(url=url, text=name, file_type=ext, number=None,
                          reason="direct-file", name=name)]
    try:
        res = fetch(url, timeout_ms=timeout_ms)
    except Exception as err:  # noqa: BLE001
        log(f"      [fetch] error: {err}")
        return []
    if res.status and res.status >= 400:
        return []
    links = find_slides(res.html, url, raw_html=res.raw_html)
    if links:
        return links
    if follow:
        subs = find_index_links(res.html, url, raw_html=res.raw_html)[:max_pages]
        collected, seen = [], set()
        for sub in subs:
            try:
                sres = fetch(sub, timeout_ms=timeout_ms)
            except Exception:  # noqa: BLE001
                continue
            for s in find_slides(sres.html, sub, raw_html=sres.raw_html):
                if s.url not in seen:
                    seen.add(s.url)
                    collected.append(s)
        return collected
    return []


def download_links(links, dest: Path, timeout_ms: int):
    dest.mkdir(parents=True, exist_ok=True)
    downloaded = skipped = failed = 0
    seen_hashes: dict[str, str] = {}
    results = []
    for seq, link in enumerate(links, start=1):
        if link.file_type in ("html", "htm"):
            res = download_html_slides(link.url, dest, seq, link.name,
                                       timeout_ms=timeout_ms)
        else:
            res = download(link.url, dest, link.file_type, link.number, seq,
                           link.name)
        if res.skipped:
            skipped += 1
        elif res.ok:
            if res.sha256 and res.sha256 in seen_hashes:
                try:
                    Path(res.path).unlink()
                except OSError:
                    pass
                res = DownloadResult(res.url, res.path, res.sha256,
                                     res.file_type, True, error="duplicate")
            else:
                if res.sha256:
                    seen_hashes[res.sha256] = Path(res.path).name
                downloaded += 1
                log(f"      + {Path(res.path).name}")
        else:
            failed += 1
        results.append((seq, link, res))
    return results, (downloaded, skipped, failed)


# --------------------------------------------------------------------------- #
# Per (professor, course) processing
# --------------------------------------------------------------------------- #
def process_course(conn, session, prof, course, args):
    pid = prof["id"]
    first = (prof["first_name"] or "").strip()
    last = (prof["last_name"] or "").strip()
    uni = (prof["school_name"] or "").strip()
    pname = f"{first} {last}".strip()
    query = f"{pname} {uni} {course} course slides".strip()
    dest = OUT_ROOT / sanitize(pname) / sanitize(course)

    log(f"  - {course!r}: {query!r}")
    try:
        results_urls = web_results(session, query, args.engine)
    except Exception as err:  # noqa: BLE001
        results_urls = []
        log(f"      [search] error: {err}")

    # Keep only university / course-host pages; drop aggregators.
    candidates = []
    for u in results_urls:
        ok, _ = is_university(u)
        if ok:
            candidates.append(u)
    candidates = candidates[: args.max_pages_per_course]

    best = None  # (links, url, run_length)
    for cu in candidates:
        links = find_on_page(cu, args.timeout, follow=not args.no_follow,
                             max_pages=args.max_subpages)
        if not links:
            continue
        seq_ok, run = is_sequential(links, min_len=args.min_seq)
        log(f"      {len(links)} link(s) @ {cu}  (run={run}, sequential={seq_ok})")
        if seq_ok:
            best = (links, cu, run)
            break

    if best is None:
        conn.execute(
            """INSERT OR REPLACE INTO attempts
               (professor_id, course_name, professor_name, school_name,
                num_ratings, query, num_results, slides_found, sequential,
                run_length, status, error, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (pid, course, pname, uni, prof["num_ratings"], query,
             len(results_urls), 0, 0, 0, "no-sequential",
             "no sequential slide deck found"),
        )
        conn.commit()
        return

    links, working_url, run = best
    res, counters = download_links(links, dest, args.timeout)
    downloaded, skipped, failed = counters

    conn.execute("DELETE FROM files WHERE professor_id=? AND course_name=?",
                 (pid, course))
    for seq, link, r in res:
        conn.execute(
            """INSERT INTO files
               (professor_id, course_name, seq, slide_url, file_type,
                saved_path, sha256, status, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (pid, course, seq, link.url, r.file_type, r.path, r.sha256,
             "skipped" if r.skipped else ("ok" if r.ok else "failed"), r.error),
        )

    status = "saved" if downloaded else ("partial" if failed else "no-downloads")
    conn.execute(
        """INSERT OR REPLACE INTO attempts
           (professor_id, course_name, professor_name, school_name, num_ratings,
            query, num_results, slides_found, sequential, run_length,
            working_url, folder, downloaded, skipped, failed, status, error,
            updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (pid, course, pname, uni, prof["num_ratings"], query,
         len(results_urls), len(links), 1, run, working_url, str(dest),
         downloaded, skipped, failed, status,
         "" if failed == 0 else f"{failed} file(s) failed"),
    )
    conn.commit()
    log(f"      -> {status}: {downloaded} new, {skipped} skip, {failed} fail "
        f"({working_url})")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def load_professors(limit=None):
    src = sqlite3.connect(PROF_DB)
    src.row_factory = sqlite3.Row
    q = """SELECT id, first_name, last_name, school_name, num_ratings, department
           FROM professors
           WHERE num_ratings > 0
           ORDER BY num_ratings DESC, full_name"""
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = src.execute(q).fetchall()
    src.close()
    return rows


def load_courses(pid: str, max_courses=None):
    src = sqlite3.connect(PROF_DB)
    q = """SELECT course_name FROM professor_courses
           WHERE professor_id=? ORDER BY course_count DESC"""
    rows = [r[0] for r in src.execute(q, (pid,))]
    src.close()
    out = []
    for c in rows:
        c = (c or "").strip()
        if not c or c.lower() in JUNK_COURSES:
            continue
        out.append(c)
        if max_courses and len(out) >= max_courses:
            break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit-profs", type=int, default=None,
                    help="process only the top-N professors")
    ap.add_argument("--max-courses", type=int, default=8,
                    help="max distinct courses to search per professor")
    ap.add_argument("--max-pages-per-course", type=int, default=6,
                    help="max search-result pages to visit per course")
    ap.add_argument("--max-subpages", type=int, default=25,
                    help="max lecture/schedule sub-pages to follow per page")
    ap.add_argument("--min-seq", type=int, default=3,
                    help="minimum consecutive-numbered decks to count as sequential")
    ap.add_argument("--timeout", type=int, default=30000,
                    help="per-page navigation timeout (ms)")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="seconds between searches")
    ap.add_argument("--engine", default="",
                    help="pin SearXNG to a specific engine (e.g. 'bing'); "
                         "empty = instance default. Ignored if TAVILY_API_KEY is set")
    ap.add_argument("--disciplines", default=",".join(DISCIPLINE_ORDER),
                    help="comma-separated discipline order to process "
                         f"(default: {','.join(DISCIPLINE_ORDER)})")
    ap.add_argument("--no-follow", action="store_true",
                    help="do not follow lecture/schedule sub-pages")
    args = ap.parse_args(argv)

    if load_tavily_key():
        log("search: Tavily API key loaded")
    else:
        log("search: no Tavily key (env or ~/.tavily_key) -> SearXNG fallback "
            "(currently unreliable on this host)")

    disciplines = [d.strip() for d in args.disciplines.split(",") if d.strip()]
    conn = open_dest_db()
    session = requests.Session()
    all_profs = load_professors(args.limit_profs)

    # Bucket professors by discipline (each prof to its first matching discipline).
    buckets: dict[str, list] = {d: [] for d in disciplines}
    for prof in all_profs:
        d = discipline_of(prof["department"])
        if d in buckets:
            buckets[d].append(prof)
    for d in disciplines:
        log(f"  {d}: {len(buckets[d])} professor(s)")

    try:
        for d in disciplines:
            profs = buckets[d]
            if not profs:
                continue
            log(f"\n========== DISCIPLINE: {d.upper()} "
                f"({len(profs)} professors) ==========")
            for prof in profs:
                courses = load_courses(prof["id"], args.max_courses)
                if not courses:
                    continue
                pname = f"{prof['first_name']} {prof['last_name']}".strip()
                log(f"\n### [{d}] {pname} — {prof['school_name']} "
                    f"(ratings={prof['num_ratings']}, dept={prof['department']}, "
                    f"{len(courses)} course(s))")
                for course in courses:
                    if already_done(conn, prof["id"], course):
                        log(f"  = skip {course!r} (done)")
                        continue
                    try:
                        process_course(conn, session, prof, course, args)
                    except Exception as err:  # noqa: BLE001
                        log(f"  ! error on {course!r}: {err}")
                        conn.execute(
                            """INSERT OR REPLACE INTO attempts
                               (professor_id, course_name, professor_name,
                                school_name, num_ratings, status, error, updated_at)
                               VALUES (?,?,?,?,?,?,?,datetime('now'))""",
                            (prof["id"], course, pname, prof["school_name"],
                             prof["num_ratings"], "error", str(err)),
                        )
                        conn.commit()
                    time.sleep(args.sleep)
    except KeyboardInterrupt:
        log("\nInterrupted — progress saved, safe to resume.")

    saved = conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE status='saved'").fetchone()[0]
    dl = conn.execute("SELECT COUNT(*) FROM files WHERE status='ok'").fetchone()[0]
    log(f"\nDone. {saved} course(s) with sequential decks, {dl} file(s) downloaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
