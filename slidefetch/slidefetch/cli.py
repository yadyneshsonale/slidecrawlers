"""Command-line entry point for slidefetch.

Usage:
    python -m slidefetch.cli <url> [--out DIR] [--dry-run] [--render]
    python -m slidefetch.cli --search "cs course slides"
    python -m slidefetch.cli --disciplines cs,ee,me

With a URL, downloads every slide deck (PDF/PPT/PPTX) linked on <url>. With
--search/--disciplines, web-searches for course pages, keeps only university
sites that aren't already downloaded, and scrapes each. Output goes to
<out>/<page-name>/. Skips notes/homework/solutions and files already present.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from slidefetch.download import download, download_html_slides
from slidefetch.drive import enumerate_folder, is_drive_folder
from slidefetch.extract import SlideLink, find_index_links, find_slides
from slidefetch.fetch import fetch
from slidefetch.search import web_search
from slidefetch.urls import (
    already_downloaded,
    is_university,
    page_slug as _page_slug,
    reduce_url,
)

# New course folders are written under <out>/<SAVE_SUBDIR>/ so they stay
# separate from any existing corpus. That corpus is still scanned for dedup, so
# courses already downloaded are skipped and never overwritten.
SAVE_SUBDIR = "new"


def _metadata_db_path(args) -> Path:
    return Path(args.out) / "download_metadata.sqlite3"


def _open_metadata_db(args) -> sqlite3.Connection:
    db_path = _metadata_db_path(args)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            course_url TEXT NOT NULL,
            course_slug TEXT NOT NULL,
            course_output_dir TEXT NOT NULL,
            instructors TEXT,
            departments TEXT,
            prerequisites TEXT,
            sequence_no INTEGER NOT NULL,
            slide_url TEXT NOT NULL,
            file_type TEXT NOT NULL,
            saved_path TEXT,
            sha256 TEXT,
            status TEXT NOT NULL,
            error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            course_url TEXT NOT NULL UNIQUE,
            course_slug TEXT NOT NULL,
            scrape_url TEXT NOT NULL,
            course_output_dir TEXT NOT NULL,
            instructors TEXT,
            departments TEXT,
            prerequisites TEXT,
            slides_found INTEGER NOT NULL DEFAULT 0,
            downloaded_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            saved_paths_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            error TEXT
        )
        """
    )
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(downloads)")
    }
    if "instructors" not in columns:
        conn.execute("ALTER TABLE downloads ADD COLUMN instructors TEXT")
    if "departments" not in columns:
        conn.execute("ALTER TABLE downloads ADD COLUMN departments TEXT")
    if "prerequisites" not in columns:
        conn.execute("ALTER TABLE downloads ADD COLUMN prerequisites TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_downloads_course_slug
        ON downloads(course_slug)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_downloads_sequence
        ON downloads(course_slug, sequence_no)
        """
    )
    conn.commit()
    return conn


def _log_course_row(
    conn: sqlite3.Connection,
    *,
    course_url: str,
    course_slug: str,
    scrape_url: str,
    course_output_dir: str,
    instructors: str,
    departments: str,
    prerequisites: str,
    slides_found: int,
    downloaded_count: int,
    skipped_count: int,
    duplicate_count: int,
    failed_count: int,
    saved_paths: list[str],
    status: str,
    error: str,
) -> None:
    conn.execute(
        """
        INSERT INTO courses (
            course_url, course_slug, scrape_url, course_output_dir,
            instructors, departments, prerequisites,
            slides_found, downloaded_count, skipped_count,
            duplicate_count, failed_count, saved_paths_json,
            status, error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(course_url) DO UPDATE SET
            course_slug=excluded.course_slug,
            scrape_url=excluded.scrape_url,
            course_output_dir=excluded.course_output_dir,
            instructors=excluded.instructors,
            departments=excluded.departments,
            prerequisites=excluded.prerequisites,
            slides_found=excluded.slides_found,
            downloaded_count=excluded.downloaded_count,
            skipped_count=excluded.skipped_count,
            duplicate_count=excluded.duplicate_count,
            failed_count=excluded.failed_count,
            saved_paths_json=excluded.saved_paths_json,
            status=excluded.status,
            error=excluded.error,
            updated_at=datetime('now')
        """,
        (
            course_url,
            course_slug,
            scrape_url,
            course_output_dir,
            instructors,
            departments,
            prerequisites,
            slides_found,
            downloaded_count,
            skipped_count,
            duplicate_count,
            failed_count,
            json.dumps(saved_paths, ensure_ascii=True),
            status,
            error,
        ),
    )


def _log_download_row(
    conn: sqlite3.Connection,
    *,
    course_url: str,
    course_slug: str,
    course_output_dir: str,
    instructors: str,
    departments: str,
    prerequisites: str,
    sequence_no: int,
    slide_url: str,
    file_type: str,
    saved_path: str | None,
    sha256: str | None,
    status: str,
    error: str,
) -> None:
    conn.execute(
        """
        INSERT INTO downloads (
            course_url, course_slug, course_output_dir,
            instructors, departments, prerequisites,
            sequence_no, slide_url, file_type,
            saved_path, sha256, status, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            course_url,
            course_slug,
            course_output_dir,
            instructors,
            departments,
            prerequisites,
            sequence_no,
            slide_url,
            file_type,
            saved_path,
            sha256,
            status,
            error,
        ),
    )


def _save_root(args) -> Path:
    """Directory new course folders are written into (``<out>/new``)."""
    return Path(args.out) / SAVE_SUBDIR


def _existing_roots(args) -> list[Path]:
    """Folders scanned to decide whether a course is already downloaded.

    Includes both the existing top-level corpus (``<out>``) and the new save
    location (``<out>/new``) so re-runs never re-fetch or overwrite a course.
    """
    return [Path(args.out), _save_root(args)]


# Short discipline tokens mapped to richer search phrases. Lets the user type
# ``--disciplines cs,ee,me`` and get good-quality queries.
DISCIPLINES = {
    "cs": "computer science",
    "ee": "electrical engineering",
    "me": "mechanical engineering",
    "ce": "civil engineering",
    "che": "chemical engineering",
    "ae": "aerospace engineering",
    "bme": "biomedical engineering",
    "math": "mathematics",
    "physics": "physics",
    "stats": "statistics",
    "bio": "biology",
    "chem": "chemistry",
}


def _expand_queries(args) -> list[str]:
    """Build the list of search queries from --search and --disciplines."""
    queries: list[str] = []
    if args.search:
        queries.extend(q for q in args.search if q.strip())
    if args.disciplines:
        tokens = args.disciplines.strip().lower()
        names = list(DISCIPLINES) if tokens == "all" else [
            t.strip() for t in tokens.split(",") if t.strip()
        ]
        for tok in names:
            name = DISCIPLINES.get(tok, tok)
            queries.append(args.query_template.format(name=name, code=tok))

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _discover(args) -> int:
    """Search the web, keep only undownloaded university courses, download them."""
    queries = _expand_queries(args)
    if not queries:
        print("ERROR: nothing to search for (use --search or --disciplines)",
              file=sys.stderr)
        return 2

    allow_hosts = not args.strict_university
    plural = "y" if len(queries) == 1 else "ies"
    print(f"discovery mode: {len(queries)} quer{plural}")
    cache_dir = Path(args.out) / ".search_cache"

    # 1) Gather candidate course URLs across all queries; filter as we go.
    candidates: list[tuple[str, str]] = []  # (course_url, source_title)
    seen_slugs: set[str] = set()
    for q in queries:
        print(f"\n[search] {q!r}")
        try:
            hits = web_search(q, limit=args.limit, cache_dir=cache_dir,
                              tavily_key=args.tavily_key)
        except Exception as err:  # noqa: BLE001
            print(f"[search] failed: {err}")
            continue
        print(f"[search] {len(hits)} result(s)")
        for hit in hits:
            course = reduce_url(hit.url)
            ok, why = is_university(course, allow_course_hosts=allow_hosts)
            if not ok:
                print(f"  reject  {why:24}  {hit.url}")
                continue
            slug = _page_slug(course)
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            if already_downloaded(course, _existing_roots(args)):
                print(f"  skip    already-downloaded       {slug}")
                continue
            candidates.append((course, hit.title))
            print(f"  accept  {why:24}  {course}")
        time.sleep(1.0)  # be polite between search queries

    if not candidates:
        print("\nNo new university course pages found.")
        return 0

    if args.max_courses and len(candidates) > args.max_courses:
        print(f"\n(limiting to first {args.max_courses} of "
              f"{len(candidates)} courses)")
        candidates = candidates[: args.max_courses]

    # 2) Download slides from each accepted course page.
    print(f"\n==== downloading from {len(candidates)} course page(s) ====")
    for course, title in candidates:
        print(f"\n### {title or course}")
        _process_url(course, args)
        time.sleep(1.0)
    return 0


def _gather(url: str, timeout_ms: int, force_render: bool, follow: bool,
            max_pages: int) -> list:
    """Find slide links on `url`; if none, follow same-site index sub-pages.

    Returns a de-duplicated list of SlideLink across the visited pages.
    """
    if is_drive_folder(url):
        print(f"[drive] enumerating folder {url}")
        try:
            files = enumerate_folder(url, timeout_ms=timeout_ms)
        except Exception as err:  # noqa: BLE001
            print(f"[drive] failed: {err}")
            return []
        print(f"[drive] found {len(files)} file(s)")
        return [
            SlideLink(
                url=f.download_url,
                text=f.name,
                file_type=f.file_type,
                number=None,
                reason="drive-folder",
                name=f.name,
            )
            for f in files
        ]

    print(f"[fetch] {url}")
    result = fetch(url, timeout_ms=timeout_ms, force_render=force_render)
    print(f"[fetch] status={result.status} rendered={result.rendered}")

    links = find_slides(result.html, url, raw_html=result.raw_html)
    print(f"[extract] found {len(links)} slide link(s)")
    if links or not follow:
        return links

    index_pages = find_index_links(result.html, url, raw_html=result.raw_html)
    index_pages = index_pages[:max_pages]
    if not index_pages:
        return links
    print(f"[follow] no slides here; checking {len(index_pages)} sub-page(s)")

    seen: set[str] = set()
    collected: list = []
    for sub in index_pages:
        print(f"[follow] {sub}")
        try:
            sres = fetch(sub, timeout_ms=timeout_ms, force_render=force_render)
        except Exception as err:  # noqa: BLE001
            print(f"[follow] skip ({err})")
            continue
        sub_links = find_slides(sres.html, sub, raw_html=sres.raw_html)
        new = [s for s in sub_links if s.url not in seen]
        for s in new:
            seen.add(s.url)
        if new:
            print(f"[follow] +{len(new)} slide link(s)")
            collected.extend(new)
    print(f"[extract] {len(collected)} slide link(s) after following")
    return collected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="slidefetch",
        description="Download all course slides linked on a page, or search the "
                    "web for university courses and download them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  slidefetch \"https://web.stanford.edu/class/cs143/\"\n"
            "  slidefetch --search \"cs course slides\"\n"
            "  slidefetch --disciplines cs,ee,me\n"
            "  slidefetch --disciplines all --max-courses 30\n"
            "  slidefetch            # interactive: type a URL or a search query\n"
        ),
    )
    parser.add_argument("url", nargs="?", default=None,
                        help="course page URL to scrape (omit for interactive mode)")
    parser.add_argument("--out", default="downloads",
                        help="output directory (default: downloads/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="list slide links without downloading")
    parser.add_argument("--render", action="store_true",
                        help="force JavaScript rendering (networkidle)")
    parser.add_argument("--timeout", type=int, default=30000,
                        help="page navigation timeout in ms (default: 30000)")
    parser.add_argument("--no-follow", action="store_true",
                        help="do not follow lecture/schedule sub-pages")
    parser.add_argument("--max-pages", type=int, default=100,
                        help="max sub-pages to follow (default: 100)")
    parser.add_argument("--course-url", default=None,
                        help="canonical course page URL for metadata logging")
    parser.add_argument("--course-instructors", default="",
                        help="course instructor names for metadata logging")
    parser.add_argument("--course-departments", default="",
                        help="course departments for metadata logging")
    parser.add_argument("--course-prerequisites", default="",
                        help="course prerequisites text for metadata logging")

    disco = parser.add_argument_group("search / discovery")
    disco.add_argument("--search", action="append", metavar="QUERY",
                       help="search the web for this query and download matching "
                            "university course slides (repeatable)")
    disco.add_argument("--disciplines", metavar="LIST",
                       help="comma-separated disciplines to search for "
                            "(e.g. cs,ee,me or 'all'); uses --query-template")
    disco.add_argument("--query-template", default="{name} course lecture slides",
                       help="per-discipline query "
                            "(default: '{name} course lecture slides')")
    disco.add_argument("--limit", type=int, default=20,
                       help="max search results to consider per query (default: 20)")
    disco.add_argument("--max-courses", type=int, default=15,
                       help="max accepted courses to download per run (default: 15)")
    disco.add_argument("--strict-university", action="store_true",
                       help="reject course hosts like github.io; accept only "
                            ".edu/.ac.* and known universities")
    disco.add_argument("--tavily-key", default=None,
                       help="Tavily API key for search (else uses the "
                            "TAVILY_API_KEY environment variable)")

    args = parser.parse_args(argv)

    if args.search or args.disciplines:
        return _discover(args)

    if args.url is None:
        return _interactive(args)

    return _process_url(args.url, args)


def _interactive(args) -> int:
    print("slidefetch interactive mode")
    print("Type a course URL to scrape it, or any text to web-search for")
    print("university slides (e.g. 'cs course slides'). 'exit' to leave.\n")
    while True:
        try:
            line = input("slidefetch> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q"):
            break
        if line.startswith(("http://", "https://")):
            _process_url(line, args)
            print()
            continue
        # Anything else is treated as a web-search query.
        prev = args.search
        args.search = [line]
        try:
            _discover(args)
        finally:
            args.search = prev
        print()
    print("bye")
    return 0


def _process_url(url: str, args) -> int:
    if not url.startswith(("http://", "https://")):
        print("ERROR: url must start with http:// or https://", file=sys.stderr)
        return 2

    if not args.dry_run and already_downloaded(url, _existing_roots(args)):
        print(f"= skip   already downloaded (not overwriting): {_page_slug(url)}")
        return 0

    try:
        links = _gather(url, args.timeout, args.render,
                        follow=not args.no_follow, max_pages=args.max_pages)
    except Exception as err:  # noqa: BLE001
        print(f"ERROR: failed to load page: {err}", file=sys.stderr)
        return 1

    metadata_course_url = args.course_url or url
    course_instructors = (args.course_instructors or "").strip()
    course_departments = (args.course_departments or "").strip()
    course_prerequisites = (args.course_prerequisites or "").strip()
    course_slug = _page_slug(metadata_course_url)
    dest = _save_root(args) / course_slug
    conn = _open_metadata_db(args)

    if not links:
        _log_course_row(
            conn,
            course_url=metadata_course_url,
            course_slug=course_slug,
            scrape_url=url,
            course_output_dir=str(dest),
            instructors=course_instructors,
            departments=course_departments,
            prerequisites=course_prerequisites,
            slides_found=0,
            downloaded_count=0,
            skipped_count=0,
            duplicate_count=0,
            failed_count=0,
            saved_paths=[],
            status="no-slides",
            error="",
        )
        conn.commit()
        conn.close()
        print("No slide decks (PDF/PPT/PPTX) found on this page.")
        return 0

    if args.dry_run:
        for link in links:
            num = f"L{link.number:02d}" if link.number else "  -"
            print(f"  {num}  {link.file_type:4}  {link.url}")
        print(f"\n(dry run) would save into: {dest}/")
        return 0

    downloaded = skipped = failed = duplicate = 0
    saved_paths: list[str] = []
    seen_hashes: dict[str, str] = {}
    try:
        for sequence_no, link in enumerate(links, start=1):
            if link.file_type in ("html", "htm"):
                res = download_html_slides(
                    link.url,
                    dest,
                    sequence_no,
                    link.name,
                    timeout_ms=args.timeout,
                )
            else:
                res = download(
                    link.url,
                    dest,
                    link.file_type,
                    link.number,
                    sequence_no,
                    link.name,
                )
            if res.skipped:
                skipped += 1
                print(f"  = skip   {Path(res.path).name}")
            elif res.ok:
                if res.sha256 and res.sha256 in seen_hashes:
                    # byte-identical to a file already saved this run; drop it
                    try:
                        Path(res.path).unlink()
                    except OSError:
                        pass
                    duplicate += 1
                    print(f"  ~ dup    {Path(res.path).name}  (= {seen_hashes[res.sha256]})")
                else:
                    if res.sha256:
                        seen_hashes[res.sha256] = Path(res.path).name
                    downloaded += 1
                    if res.path:
                        saved_paths.append(res.path)
                    print(f"  + saved  {Path(res.path).name}")
            else:
                failed += 1
                print(f"  ! fail   {link.url}  ({res.error})")
        _log_course_row(
            conn,
            course_url=metadata_course_url,
            course_slug=course_slug,
            scrape_url=url,
            course_output_dir=str(dest),
            instructors=course_instructors,
            departments=course_departments,
            prerequisites=course_prerequisites,
            slides_found=len(links),
            downloaded_count=downloaded,
            skipped_count=skipped,
            duplicate_count=duplicate,
            failed_count=failed,
            saved_paths=saved_paths,
            status="saved" if downloaded else ("partial-failure" if failed else "no-downloads"),
            error="" if failed == 0 else f"{failed} file(s) failed",
        )
        conn.commit()
    finally:
        conn.close()

    print(f"\n==== done ====")
    print(f"downloaded: {downloaded}   skipped: {skipped}   "
          f"duplicates: {duplicate}   failed: {failed}")
    print(f"saved into: {dest}/")
    print(f"metadata db: {_metadata_db_path(args)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
