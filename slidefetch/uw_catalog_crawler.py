#!/usr/bin/env python3
"""Crawl the UW Allen School (CSE) course catalog and download lecture slides.

Pipeline
--------
1. **Parse the catalog.** Read the course-list page
   (https://www.cs.washington.edu/academics/courses/) -- or a saved HTML file --
   and pull out every course entry: code, title, catalog URL, the section it
   sits under (Introductory / Undergraduate Major / Graduate ...), its
   description and prerequisites.
2. **Visit each course page.** e.g. ``/courses/cse446`` lists every quarter the
   course ran as ``Spring, 2026 (Jaques)`` linking to ``/courses/cse446/26sp/``.
   Each such link is an *offering*; the name(s) in parentheses are the
   instructors.
3. **Crawl every offering for slides.** Fetch the offering home page and follow
   its lecture/schedule sub-pages (staying inside that offering) looking for
   slide decks -- reusing slidefetch's ``find_slides`` / ``find_index_links``.
4. **Download** any slide deck found (reusing slidefetch's verified downloader)
   into ``<out>/<code>/<term>/``.
5. **Record everything** in a SQLite DB (``uw_catalog.db``) across four tables:
   ``courses``, ``offerings``, ``instructors`` and ``slides``.

This module deliberately reuses the slidefetch package for the hard parts
(JS rendering, slide-link heuristics, magic-byte-verified downloads).

Examples
--------
    # Parse the live catalog into the DB, list courses, download nothing:
    python uw_catalog_crawler.py --dry-run

    # Populate courses + offerings + instructors for every course (no slides):
    python uw_catalog_crawler.py --no-download

    # Full crawl: latest 2 offerings of the 400-level ML/AI courses:
    python uw_catalog_crawler.py --filter 'cse4(4|7)' --max-offerings 2

    # Everything, all offerings (long!):
    python uw_catalog_crawler.py --all-offerings
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import certifi
from bs4 import BeautifulSoup

from slidefetch.download import download, download_html_slides, sanitize
from slidefetch.extract import find_index_links, find_slides

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

CATALOG_URL = "https://www.cs.washington.edu/academics/courses/"
COURSE_HOST = "courses.cs.washington.edu"
DEFAULT_DB = "uw_catalog.db"
DEFAULT_OUT = "downloads/uw"

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 slidefetch/1.0"
)

# A catalog entry's anchor: ``https://courses.cs.washington.edu/courses/cse446``
# (no trailing path). The featured-course links use a trailing slash and
# different text, so they are naturally excluded.
COURSE_LINK_RE = re.compile(
    r"^https?://courses\.cs\.washington\.edu/courses/([a-z]+\d+[a-z0-9]*)/?$",
    re.IGNORECASE,
)

# A course code at the start of an anchor's text, e.g. ``CSE446``, ``CSE143X``,
# ``CSEM501``, ``CSEP517``, ``CSED502``.
CODE_PREFIX_RE = re.compile(r"^([A-Z]+\s?\d+[A-Z0-9]*)", re.IGNORECASE)

# Quarter slug as the single path segment of an offering URL, e.g. ``26sp``,
# ``25au``, ``24wi``, ``23su`` (also tolerates ``sp26`` and 4-digit years).
TERM_SEG_RE = re.compile(
    r"^(?:\d{2}(?:au|wi|sp|su)|(?:au|wi|sp|su)\d{2}|\d{4}|\d{4}-\d{2})$",
    re.IGNORECASE,
)

PREREQ_RE = re.compile(
    r"((?:Prerequisite|Prerequisites|Recommended)\b.*)$",
    re.IGNORECASE | re.DOTALL,
)

_JS_SHELL_MARKERS = (
    "you need to have javascript enabled",
    "javascript is required",
    "please enable javascript",
    "enable javascript to",
)


# --------------------------------------------------------------------------- #
# Data records
# --------------------------------------------------------------------------- #

@dataclass
class Course:
    code: str                       # CSE446 (normalised, no spaces, upper)
    title: str                      # Machine Learning
    url: str                        # https://courses.cs.washington.edu/courses/cse446
    category: str = ""              # Undergraduate Major
    description: str = ""
    prerequisites: str = ""


@dataclass
class Offering:
    course_code: str
    term: str                       # 26sp
    url: str                        # https://courses.cs.washington.edu/courses/cse446/26sp/
    term_label: str = ""            # "Spring, 2026"
    instructors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _looks_unrendered(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in _JS_SHELL_MARKERS)


def _urllib_get(url: str, timeout: float) -> str:
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "replace")


def fetch_html(url: str, *, render: bool = False, timeout_ms: int = 30000) -> tuple[str, str]:
    """Return ``(html, raw_html)`` for *url*.

    Uses a fast urllib GET for static pages; falls back to slidefetch's
    Playwright loader when *render* is requested, the page looks like a JS shell,
    or the static fetch fails (e.g. 403 to non-browser clients).
    """
    timeout_s = max(1.0, timeout_ms / 1000)
    if not render:
        try:
            html = _urllib_get(url, timeout_s)
            if not _looks_unrendered(html):
                return html, html
        except urllib.error.HTTPError as err:
            if err.code in (404, 410):
                raise
        except Exception:  # noqa: BLE001 - fall back to the browser loader
            pass

    # Lazy import: only pay the Playwright import cost when we actually render.
    from slidefetch.fetch import fetch as render_fetch

    res = render_fetch(url, timeout_ms=timeout_ms, force_render=render)
    return res.html, res.raw_html


# --------------------------------------------------------------------------- #
# Catalog parsing
# --------------------------------------------------------------------------- #

def _normalise_code(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def _extract_prerequisites(description: str) -> str:
    m = PREREQ_RE.search(description)
    return m.group(1).strip() if m else ""


def parse_catalog(html: str, base_url: str = CATALOG_URL) -> list[Course]:
    """Parse the catalog page into a de-duplicated list of :class:`Course`.

    Category headers are ``<h3 class="mt-5">`` (e.g. *Undergraduate Major*); each
    course is a bold anchor to ``/courses/<code>`` followed by a ``<span>``
    description. Non-bold in-description links (the CSE197 "Section Offerings"
    list) are skipped because they are not styled bold and each also has its own
    bold entry elsewhere.
    """
    soup = BeautifulSoup(html, "lxml")
    courses: list[Course] = []
    seen: set[str] = set()
    current_category = ""

    for el in soup.find_all(["h3", "a"]):
        if el.name == "h3":
            classes = el.get("class") or []
            if "mt-5" in classes:
                current_category = el.get_text(" ", strip=True)
            continue

        href = (el.get("href") or "").strip()
        m = COURSE_LINK_RE.match(href)
        if not m:
            continue
        style = (el.get("style") or "").lower()
        if "bold" not in style:
            continue  # skip in-description section-offering links

        text = el.get_text(" ", strip=True)
        cm = CODE_PREFIX_RE.match(text)
        if not cm:
            continue
        code = _normalise_code(cm.group(1))
        if code in seen:
            continue
        seen.add(code)

        title = text[cm.end():].strip(" -:\u2013")
        span = el.find_next_sibling("span")
        description = span.get_text(" ", strip=True) if span else ""
        # The CSE197-style "Section Offerings" list bloats the description; trim.
        description = re.split(r"\bSection Offerings:", description)[0].strip()

        courses.append(
            Course(
                code=code,
                title=title,
                url=urljoin(base_url, href),
                category=current_category,
                description=description,
                prerequisites=_extract_prerequisites(description),
            )
        )
    return courses


def parse_offerings(html: str, course: Course) -> list[Offering]:
    """Parse a course landing page into its quarterly :class:`Offering` list.

    Recognises anchors like ``Spring, 2026 (Jaques)`` ->
    ``/courses/cse446/26sp/``. The single path segment after the course code is
    the term; the name(s) in the final parentheses of the link text are the
    instructors.
    """
    soup = BeautifulSoup(html, "lxml")
    code_lower = course.code.lower()
    prefix = f"/courses/{code_lower}/"
    offerings: list[Offering] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = urljoin(course.url + "/", a["href"].strip())
        parts = urlparse(href)
        host = parts.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host != COURSE_HOST:
            continue
        path = parts.path
        if not path.lower().startswith(prefix):
            continue
        segs = [s for s in path[len(prefix):].split("/") if s]
        if len(segs) != 1:
            continue  # only one extra segment == an offering root
        term = segs[0]
        if not TERM_SEG_RE.match(term):
            continue  # e.g. /admin, /staff -- not a quarter
        norm = f"https://{COURSE_HOST}/courses/{code_lower}/{term.lower()}/"
        if norm in seen:
            continue
        seen.add(norm)

        text = a.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
        instructors: list[str] = []
        term_label = text
        im = re.search(r"\(([^)]*)\)\s*$", text)
        if im:
            term_label = text[: im.start()].strip().rstrip(",; ")
            instructors = _split_instructors(im.group(1))

        offerings.append(
            Offering(
                course_code=course.code,
                term=term.lower(),
                url=norm,
                term_label=term_label,
                instructors=instructors,
            )
        )
    return offerings


def _split_instructors(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    parts = re.split(r"\s*(?:,|;|/|&|\band\b|\+)\s*", raw)
    out: list[str] = []
    for p in parts:
        name = p.strip(" .")
        # Drop noise tokens that sometimes appear instead of a person.
        if not name or name.lower() in {"staff", "tba", "tbd", "various"}:
            continue
        out.append(name)
    return out


# --------------------------------------------------------------------------- #
# Slide gathering (bounded, single-offering crawl reusing slidefetch)
# --------------------------------------------------------------------------- #

def _keep_slide(link) -> bool:
    """Filter slidefetch hits down to genuine UW decks.

    UW lecture directories are full of Javadoc and rendered source listings
    (``Board.html``, ``package-summary.html``, ``args.c.html`` ...). slidefetch
    accepts those as candidate HTML slideshows purely because they sit in a
    ``lectures/`` folder (its weakest ``html+slide-dir`` signal), and they then
    fail to render as remark/reveal decks. Real UW decks are PDF/PPT, so we keep
    every non-HTML hit but require an HTML hit to carry an explicit slide signal
    in its own filename (keyword/number pattern or a "Lecture Slides" page),
    dropping the dir-only matches.
    """
    if link.file_type in ("html", "htm"):
        return link.reason != "html+slide-dir"
    return True


def gather_slides(
    start_url: str,
    *,
    render: bool,
    timeout_ms: int,
    depth: int,
    max_pages: int,
    delay: float,
    log=print,
) -> list:
    """Breadth-first crawl *within one offering* for slide-deck links.

    Follows only lecture/schedule index sub-pages whose path stays under
    ``start_url`` (so we never wander into other courses linked as
    prerequisites), down to *depth* hops and at most *max_pages* fetches.
    Returns a de-duplicated list of slidefetch ``SlideLink``.
    """
    base_path = urlparse(start_url).path.rstrip("/")

    def _in_scope(u: str) -> bool:
        p = urlparse(u).path.rstrip("/")
        return p == base_path or p.startswith(base_path + "/")

    visited: set[str] = set()
    found: dict[str, object] = {}
    queue: list[tuple[str, int]] = [(start_url, 0)]
    fetched = 0

    while queue and fetched < max_pages:
        url, level = queue.pop(0)
        key = url.split("#", 1)[0].rstrip("/")
        if key in visited:
            continue
        visited.add(key)

        try:
            html, raw = fetch_html(url, render=render, timeout_ms=timeout_ms)
        except Exception as err:  # noqa: BLE001
            log(f"      [fetch] skip {url} ({err})")
            continue
        fetched += 1

        for link in find_slides(html, url, raw_html=raw):
            if not _keep_slide(link):
                continue
            key = link.url.split("#", 1)[0]  # collapse #fragment duplicates
            found.setdefault(key, link)

        if level < depth:
            for sub in find_index_links(html, url, raw_html=raw):
                sk = sub.split("#", 1)[0].rstrip("/")
                if sk not in visited and _in_scope(sub):
                    queue.append((sub, level + 1))
        time.sleep(delay)

    return list(found.values())


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

def open_db(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS courses (
            code           TEXT PRIMARY KEY,
            title          TEXT,
            url            TEXT NOT NULL,
            category       TEXT,
            description    TEXT,
            prerequisites  TEXT,
            offerings_found    INTEGER NOT NULL DEFAULT 0,
            slides_downloaded  INTEGER NOT NULL DEFAULT 0,
            status         TEXT NOT NULL DEFAULT 'parsed',
            error          TEXT,
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS offerings (
            course_code      TEXT NOT NULL,
            term             TEXT NOT NULL,
            term_label       TEXT,
            url              TEXT PRIMARY KEY,
            instructors      TEXT,
            slides_found     INTEGER NOT NULL DEFAULT 0,
            downloaded_count INTEGER NOT NULL DEFAULT 0,
            skipped_count    INTEGER NOT NULL DEFAULT 0,
            duplicate_count  INTEGER NOT NULL DEFAULT 0,
            failed_count     INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'pending',
            error            TEXT,
            updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS instructors (
            name         TEXT NOT NULL,
            course_code  TEXT NOT NULL,
            term         TEXT,
            offering_url TEXT NOT NULL,
            UNIQUE(name, offering_url)
        );

        CREATE TABLE IF NOT EXISTS slides (
            course_code   TEXT NOT NULL,
            offering_url  TEXT NOT NULL,
            sequence_no   INTEGER,
            slide_url     TEXT NOT NULL,
            file_type     TEXT,
            saved_path    TEXT,
            sha256        TEXT,
            status        TEXT NOT NULL,
            error         TEXT,
            updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(offering_url, slide_url)
        );

        CREATE INDEX IF NOT EXISTS idx_offerings_course ON offerings(course_code);
        CREATE INDEX IF NOT EXISTS idx_slides_course   ON slides(course_code);
        CREATE INDEX IF NOT EXISTS idx_instr_name      ON instructors(name);
        """
    )
    conn.commit()
    return conn


def upsert_course(conn: sqlite3.Connection, c: Course) -> None:
    conn.execute(
        """
        INSERT INTO courses (code, title, url, category, description, prerequisites)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            title=excluded.title,
            url=excluded.url,
            category=excluded.category,
            description=excluded.description,
            prerequisites=excluded.prerequisites,
            updated_at=datetime('now')
        """,
        (c.code, c.title, c.url, c.category, c.description, c.prerequisites),
    )


def upsert_offering(conn: sqlite3.Connection, o: Offering) -> None:
    conn.execute(
        """
        INSERT INTO offerings (course_code, term, term_label, url, instructors)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            term=excluded.term,
            term_label=excluded.term_label,
            instructors=excluded.instructors,
            updated_at=datetime('now')
        """,
        (o.course_code, o.term, o.term_label, o.url, ", ".join(o.instructors)),
    )
    for name in o.instructors:
        conn.execute(
            """
            INSERT OR IGNORE INTO instructors (name, course_code, term, offering_url)
            VALUES (?, ?, ?, ?)
            """,
            (name, o.course_code, o.term, o.url),
        )


def update_offering_stats(conn: sqlite3.Connection, url: str, *, found: int,
                          downloaded: int, skipped: int, duplicate: int,
                          failed: int, status: str, error: str = "") -> None:
    conn.execute(
        """
        UPDATE offerings SET
            slides_found=?, downloaded_count=?, skipped_count=?,
            duplicate_count=?, failed_count=?, status=?, error=?,
            updated_at=datetime('now')
        WHERE url=?
        """,
        (found, downloaded, skipped, duplicate, failed, status, error, url),
    )


def record_slide(conn: sqlite3.Connection, *, course_code: str, offering_url: str,
                 sequence_no: int, slide_url: str, file_type: str,
                 saved_path: str | None, sha256: str | None, status: str,
                 error: str = "") -> None:
    conn.execute(
        """
        INSERT INTO slides (course_code, offering_url, sequence_no, slide_url,
                            file_type, saved_path, sha256, status, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(offering_url, slide_url) DO UPDATE SET
            sequence_no=excluded.sequence_no,
            file_type=excluded.file_type,
            saved_path=excluded.saved_path,
            sha256=excluded.sha256,
            status=excluded.status,
            error=excluded.error,
            updated_at=datetime('now')
        """,
        (course_code, offering_url, sequence_no, slide_url, file_type,
         saved_path, sha256, status, error),
    )


def update_course_stats(conn: sqlite3.Connection, code: str, *, offerings_found: int,
                        slides_downloaded: int, status: str, error: str = "") -> None:
    conn.execute(
        """
        UPDATE courses SET
            offerings_found=?, slides_downloaded=?, status=?, error=?,
            updated_at=datetime('now')
        WHERE code=?
        """,
        (offerings_found, slides_downloaded, status, error, code),
    )


def offering_is_done(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT status FROM offerings WHERE url=?", (url,)).fetchone()
    return bool(row) and row[0] in ("saved", "no-slides")


# --------------------------------------------------------------------------- #
# Crawl driver
# --------------------------------------------------------------------------- #

def _dest_dir(out_root: Path, code: str, term: str) -> Path:
    return out_root / sanitize(code.lower()) / sanitize(term.lower())


def crawl_offering(conn: sqlite3.Connection, off: Offering, args, log=print) -> int:
    """Crawl one offering for slides and download them. Returns #downloaded."""
    if not args.refresh and offering_is_done(conn, off.url):
        log(f"    = {off.term:8} already crawled (skip)")
        return 0

    instr = ", ".join(off.instructors) or "-"
    log(f"    > {off.term:8} {off.term_label:14} [{instr}]")
    try:
        links = gather_slides(
            off.url,
            render=args.render,
            timeout_ms=args.timeout,
            depth=args.depth,
            max_pages=args.max_pages,
            delay=args.delay,
            log=log,
        )
    except Exception as err:  # noqa: BLE001
        update_offering_stats(conn, off.url, found=0, downloaded=0, skipped=0,
                              duplicate=0, failed=0, status="error", error=str(err))
        conn.commit()
        log(f"      ! error: {err}")
        return 0

    log(f"      found {len(links)} slide link(s)")
    if not links:
        update_offering_stats(conn, off.url, found=0, downloaded=0, skipped=0,
                              duplicate=0, failed=0, status="no-slides")
        conn.commit()
        return 0

    if args.no_download:
        for seq, link in enumerate(links, start=1):
            record_slide(conn, course_code=off.course_code, offering_url=off.url,
                         sequence_no=seq, slide_url=link.url,
                         file_type=link.file_type, saved_path=None, sha256=None,
                         status="found")
        update_offering_stats(conn, off.url, found=len(links), downloaded=0,
                              skipped=0, duplicate=0, failed=0, status="found")
        conn.commit()
        return 0

    dest = _dest_dir(Path(args.out), off.course_code, off.term)
    downloaded = skipped = duplicate = failed = 0
    seen_hashes: dict[str, str] = {}

    for seq, link in enumerate(links, start=1):
        if link.file_type in ("html", "htm"):
            res = download_html_slides(link.url, dest, seq, link.name,
                                       timeout_ms=args.timeout)
        else:
            res = download(link.url, dest, link.file_type, link.number, seq,
                           link.name)

        if res.skipped:
            skipped += 1
            status = "skipped"
            record_slide(conn, course_code=off.course_code, offering_url=off.url,
                         sequence_no=seq, slide_url=link.url,
                         file_type=res.file_type, saved_path=res.path,
                         sha256=None, status=status, error=res.error)
        elif res.ok:
            if res.sha256 and res.sha256 in seen_hashes:
                try:
                    Path(res.path).unlink()
                except OSError:
                    pass
                duplicate += 1
                record_slide(conn, course_code=off.course_code, offering_url=off.url,
                             sequence_no=seq, slide_url=link.url,
                             file_type=res.file_type, saved_path=None,
                             sha256=res.sha256, status="duplicate",
                             error=f"dup of {seen_hashes[res.sha256]}")
            else:
                if res.sha256:
                    seen_hashes[res.sha256] = Path(res.path).name
                downloaded += 1
                record_slide(conn, course_code=off.course_code, offering_url=off.url,
                             sequence_no=seq, slide_url=link.url,
                             file_type=res.file_type, saved_path=res.path,
                             sha256=res.sha256, status="saved")
        else:
            failed += 1
            record_slide(conn, course_code=off.course_code, offering_url=off.url,
                         sequence_no=seq, slide_url=link.url,
                         file_type=res.file_type, saved_path=None, sha256=None,
                         status="failed", error=res.error)

    status = "saved" if downloaded else ("partial" if failed else "no-downloads")
    update_offering_stats(conn, off.url, found=len(links), downloaded=downloaded,
                          skipped=skipped, duplicate=duplicate, failed=failed,
                          status=status)
    conn.commit()
    log(f"      downloaded {downloaded}  skipped {skipped}  "
        f"dup {duplicate}  failed {failed}  ->  {dest}")
    return downloaded


def crawl_course(conn: sqlite3.Connection, course: Course, args, log=print) -> None:
    log(f"\n### {course.code}  {course.title}   [{course.category}]")

    if args.dry_run:
        return

    try:
        html, _ = fetch_html(course.url, render=args.render, timeout_ms=args.timeout)
    except Exception as err:  # noqa: BLE001
        update_course_stats(conn, course.code, offerings_found=0,
                            slides_downloaded=0, status="error", error=str(err))
        conn.commit()
        log(f"  ! failed to load course page: {err}")
        return

    offerings = parse_offerings(html, course)
    for off in offerings:
        upsert_offering(conn, off)
    conn.commit()
    log(f"  {len(offerings)} offering(s) found")

    selected = offerings if args.all_offerings else offerings[: args.max_offerings]
    total_downloaded = 0
    for off in selected:
        total_downloaded += crawl_offering(conn, off, args, log=log)

    status = "crawled" if offerings else "no-offerings"
    update_course_stats(conn, course.code, offerings_found=len(offerings),
                        slides_downloaded=total_downloaded, status=status)
    conn.commit()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _load_catalog_html(args) -> str:
    if args.catalog_file:
        return Path(args.catalog_file).read_text(encoding="utf-8", errors="replace")
    return fetch_html(args.catalog_url, render=args.render_catalog,
                      timeout_ms=args.timeout)[0]


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="uw_catalog_crawler",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_argument_group("catalog source")
    src.add_argument("--catalog-url", default=CATALOG_URL,
                     help=f"catalog page to parse (default: {CATALOG_URL})")
    src.add_argument("--catalog-file", default=None,
                     help="parse a saved catalog HTML file instead of fetching")
    src.add_argument("--render-catalog", action="store_true",
                     help="force JS rendering of the catalog page")

    sel = p.add_argument_group("selection")
    sel.add_argument("--filter", default=None, metavar="REGEX",
                     help="only crawl courses whose code matches this regex "
                          "(case-insensitive), e.g. 'cse4'")
    sel.add_argument("--limit", type=int, default=0,
                     help="max number of courses to crawl (0 = all)")
    sel.add_argument("--max-offerings", type=int, default=3,
                     help="most-recent offerings to crawl per course (default: 3)")
    sel.add_argument("--all-offerings", action="store_true",
                     help="crawl every offering of each course (overrides "
                          "--max-offerings)")

    beh = p.add_argument_group("behaviour")
    beh.add_argument("--out", default=DEFAULT_OUT,
                     help=f"download directory (default: {DEFAULT_OUT})")
    beh.add_argument("--db", default=DEFAULT_DB,
                     help=f"SQLite metadata DB (default: {DEFAULT_DB})")
    beh.add_argument("--dry-run", action="store_true",
                     help="parse + store the catalog only; visit no course pages")
    beh.add_argument("--no-download", action="store_true",
                     help="record courses/offerings/slide links but download "
                          "no files")
    beh.add_argument("--render", action="store_true",
                     help="force JS rendering for course/offering pages")
    beh.add_argument("--depth", type=int, default=2,
                     help="sub-page follow depth per offering (default: 2)")
    beh.add_argument("--max-pages", type=int, default=40,
                     help="max pages fetched per offering (default: 40)")
    beh.add_argument("--timeout", type=int, default=30000,
                     help="per-page timeout in ms (default: 30000)")
    beh.add_argument("--delay", type=float, default=0.5,
                     help="delay between requests in seconds (default: 0.5)")
    beh.add_argument("--refresh", action="store_true",
                     help="re-crawl offerings already marked done")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        html = _load_catalog_html(args)
    except Exception as err:  # noqa: BLE001
        print(f"ERROR: could not load catalog: {err}", file=sys.stderr)
        return 1

    courses = parse_catalog(html, base_url=args.catalog_url)
    if not courses:
        print("ERROR: no courses parsed from the catalog (page layout changed?)",
              file=sys.stderr)
        return 1

    if args.filter:
        pat = re.compile(args.filter, re.IGNORECASE)
        courses = [c for c in courses if pat.search(c.code)]
    if args.limit:
        courses = courses[: args.limit]

    conn = open_db(args.db)
    for c in courses:
        upsert_course(conn, c)
    conn.commit()

    by_cat: dict[str, int] = {}
    for c in courses:
        by_cat[c.category] = by_cat.get(c.category, 0) + 1
    print(f"parsed {len(courses)} course(s) into {args.db}")
    for cat, n in by_cat.items():
        print(f"  {n:3}  {cat or '(uncategorised)'}")

    if args.dry_run:
        print("\n(dry run) catalog stored; no course pages visited.")
        for c in courses:
            print(f"  {c.code:9} {c.title}")
        conn.close()
        return 0

    print(f"\n==== crawling {len(courses)} course(s) "
          f"(max-offerings={'all' if args.all_offerings else args.max_offerings}, "
          f"download={'no' if args.no_download else 'yes'}) ====")
    try:
        for c in courses:
            crawl_course(conn, c, args)
    except KeyboardInterrupt:
        print("\ninterrupted; progress saved.", file=sys.stderr)
    finally:
        conn.close()

    print(f"\ndone. metadata DB: {args.db}")
    if not args.no_download:
        print(f"slides under: {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
