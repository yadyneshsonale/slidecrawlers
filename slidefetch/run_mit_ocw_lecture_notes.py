#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

API_URL = "https://api.learn.mit.edu/api/v1/learning_resources/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 mit-ocw-runner/1.0"
)
COURSE_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def get_json(url: str, timeout: int = 60) -> dict:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_html(url: str, timeout: int = 60) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _normalize_space(text: str) -> str:
    return " ".join(text.split())


def _extract_prerequisites_from_soup(soup: BeautifulSoup) -> str:
    pattern = re.compile(r"prereq(?:uisite)?s?", re.IGNORECASE)

    lines = []
    for raw_line in soup.get_text("\n").splitlines():
        line = _normalize_space(raw_line)
        if line:
            lines.append(line)

    for index, line in enumerate(lines):
        if not pattern.search(line):
            continue

        match = re.search(r"prereq(?:uisite)?s?\s*:?[\s-]*(.+)", line, re.IGNORECASE)
        if match and match.group(1):
            value = match.group(1).strip()
            if 3 <= len(value) <= 300:
                return value

        if index + 1 < len(lines):
            next_line = lines[index + 1].strip()
            if 3 <= len(next_line) <= 300 and not pattern.search(next_line):
                return next_line

    return ""


def _dedupe_keep_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _normalize_space(value)
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


_HONORIFICS = frozenset({
    "prof", "professor", "dr", "mr", "ms", "mrs", "miss", "phd", "md",
})


def _name_tokens(name: str) -> frozenset[str]:
    """Lowercase word tokens for a person name, stripping honorifics/initials."""
    cleaned = re.sub(r"[^a-z\s]", " ", name.lower())
    return frozenset(
        t for t in cleaned.split()
        if len(t) > 1 and t not in _HONORIFICS
    )


def _split_instructors(instructors: str) -> list[str]:
    if not instructors:
        return []
    parts = [p.strip() for p in instructors.split("|")]
    return [p for p in parts if p]


def _names_match(db_name: str, course_name: str) -> bool:
    """True only when all DB name tokens appear in the course name tokens
    and at least 2 tokens overlap (requires both first + last name)."""
    db_toks = _name_tokens(db_name)
    course_toks = _name_tokens(course_name)
    if len(db_toks) < 2 or len(course_toks) < 2:
        # Single-word name in either side → can't confirm first+last; skip.
        return False
    # All DB tokens must be present in course tokens (handles middle initials
    # on the course side: "Haynes R. Miller" matches DB "Haynes Miller").
    return db_toks <= course_toks or course_toks <= db_toks


def _course_already_downloaded(course_url: str, out_dir: Path) -> bool:
    # Mirrors slidefetch's slug logic to avoid invoking CLI for known courses.
    from slidefetch.urls import already_downloaded

    return already_downloaded(course_url, [out_dir, out_dir / "new"])


def _has_any_instructor_in_mit_db(instructors: str, db_path: Path) -> tuple[bool, str]:
    names = _split_instructors(instructors)
    if not names or not db_path.exists():
        return False, ""

    try:
        conn = sqlite3.connect(db_path)
        db_names = [row[0] for row in conn.execute("SELECT name FROM professors") if row[0]]
    except sqlite3.Error:
        return False, ""
    finally:
        try:
            conn.close()
        except Exception:
            pass

    for course_name in names:
        for db_name in db_names:
            if _names_match(db_name, course_name):
                return True, course_name
    return False, ""


def _extract_people_and_departments(soup: BeautifulSoup, course_url: str) -> tuple[str, str]:
    instructor_names: list[str] = []
    department_names: list[str] = []

    for a in soup.find_all("a", href=True):
        href = urljoin(course_url, a["href"].strip())
        text = _normalize_space(a.get_text(" ", strip=True))
        if not text:
            continue

        if "/search/?q=" in href:
            instructor_names.append(text)
        elif "/search/?d=" in href:
            department_names.append(text)

    instructors = " | ".join(_dedupe_keep_order(instructor_names))
    departments = " | ".join(_dedupe_keep_order(department_names))
    return instructors, departments


def iter_ocw_courses_with_lecture_notes() -> Iterable[str]:
    next_url = (
        f"{API_URL}?limit=100&offset=0&offered_by=ocw"
        "&resource_type=course&course_feature=Lecture%20Notes"
    )
    seen: set[str] = set()
    while next_url:
        payload = get_json(next_url)
        for item in payload.get("results", []):
            url = item.get("url")
            if url and url not in seen:
                seen.add(url)
                yield url
        next_url = payload.get("next")


def iter_courses_from_file(path: Path) -> Iterable[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    seen: set[str] = set()

    # Accept full links, root-relative course hrefs from copied HTML, and bare /courses paths.
    for href in COURSE_HREF_RE.findall(text):
        candidate = href.strip()
        if not candidate:
            continue
        if candidate.startswith("/courses/"):
            candidate = urljoin("https://ocw.mit.edu", candidate)
        if not candidate.startswith("https://ocw.mit.edu/courses/"):
            continue
        if candidate not in seen:
            seen.add(candidate)
            yield candidate

    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if candidate.startswith("/courses/"):
            candidate = urljoin("https://ocw.mit.edu", candidate)
        if not candidate.startswith("https://ocw.mit.edu/courses/"):
            continue
        if candidate not in seen:
            seen.add(candidate)
            yield candidate


def extract_course_metadata(course_url: str) -> tuple[str | None, str, str, str]:
    html = get_html(course_url)
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    candidates: list[str] = []
    syllabus_url: str | None = None
    instructors, departments = _extract_people_and_departments(soup, course_url)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True).lower()
        full = urljoin(course_url, href)

        if text == "syllabus" or href.lower().endswith("/pages/syllabus/"):
            syllabus_url = full

        if "lecture notes" in text or "lecture slides" in text:
            candidates.append(full)
            continue

        low_href = href.lower()
        if (
            "lecture-notes" in low_href
            or "/lecture_notes" in low_href
            or "lecture-slides" in low_href
            or "/lecture_slides" in low_href
        ):
            candidates.append(full)

    prerequisites = _extract_prerequisites_from_soup(soup)
    if not prerequisites and syllabus_url:
        try:
            syllabus_html = get_html(syllabus_url)
            syllabus_soup = BeautifulSoup(syllabus_html, "lxml")
            for tag in syllabus_soup(["script", "style", "noscript"]):
                tag.decompose()
            prerequisites = _extract_prerequisites_from_soup(syllabus_soup)
        except Exception:
            pass

    if not candidates:
        return None, instructors, departments, prerequisites

    for c in candidates:
        low = c.lower()
        if "/pages/" in low and ("lecture-notes" in low or "lecture-slides" in low):
            return c, instructors, departments, prerequisites
    return candidates[0], instructors, departments, prerequisites


def run_slidefetch(
    url: str,
    *,
    out_dir: Path,
    timeout: int,
    course_url: str,
    instructors: str,
    departments: str,
    prerequisites: str,
) -> int:
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "slidefetch.cli",
        url,
        "--out",
        str(out_dir),
        "--timeout",
        str(timeout),
        "--course-url",
        course_url,
    ]
    if instructors:
        cmd.extend(["--course-instructors", instructors])
    if departments:
        cmd.extend(["--course-departments", departments])
    if prerequisites:
        cmd.extend(["--course-prerequisites", prerequisites])
    proc = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stderr)
    return proc.returncode


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Run slidefetch on MIT OCW Lecture Notes pages for all matching courses."
    )
    parser.add_argument("--out", default="new_downloads", help="Output root directory")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between courses")
    parser.add_argument("--timeout", type=int, default=30000, help="slidefetch page timeout in ms")
    parser.add_argument("--max-courses", type=int, default=0, help="Optional limit; 0 = all")
    parser.add_argument(
        "--links-file",
        default="",
        help="Optional HTML/text file containing MIT OCW course links; skips API crawl when set",
    )
    parser.add_argument(
        "--state-file",
        default="work/mit_ocw_lecture_notes_state.json",
        help="Resume/progress state file",
    )
    parser.add_argument(
        "--mit-professors-db",
        default="mit_professors.db",
        help="SQLite DB with professors(name, ...) used to gate downloads",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    state_path = Path(args.state_file)
    mit_prof_db = Path(args.mit_professors_db)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    processed: set[str] = set()
    failures: dict[str, str] = {}

    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            processed = set(state.get("processed", []))
            failures = dict(state.get("failures", {}))
            print(f"[resume] loaded {len(processed)} processed entries")
        except Exception:
            pass

    total = 0
    done = 0

    if args.links_file:
        links_path = Path(args.links_file)
        if not links_path.exists():
            raise SystemExit(f"links file not found: {links_path}")
        print(f"[source] links file: {links_path}")
        course_iterable = iter_courses_from_file(links_path)
    else:
        print("[source] MIT OCW API crawl")
        course_iterable = iter_ocw_courses_with_lecture_notes()

    for course_url in course_iterable:
        total += 1
        if args.max_courses and total > args.max_courses:
            break

        if course_url in processed:
            continue

        print(f"\n[course] {course_url}")

        try:
            lecture_notes_url, instructors, departments, prerequisites = extract_course_metadata(course_url)
            target = lecture_notes_url or course_url
            if lecture_notes_url:
                print(f"[notes] {lecture_notes_url}")
            else:
                print("[notes] not found; falling back to course page")
            if instructors:
                print(f"[instructors] {instructors}")
            if departments:
                print(f"[departments] {departments}")
            if prerequisites:
                print(f"[prerequisites] {prerequisites}")

            if _course_already_downloaded(course_url, out_dir):
                print("[skip] already saved")
                processed.add(course_url)
                done += 1
                if course_url in failures:
                    failures.pop(course_url, None)
                snapshot = {
                    "processed": sorted(processed),
                    "failures": failures,
                    "last_total_seen": total,
                }
                state_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
                time.sleep(max(0.0, args.delay))
                continue

            in_db, matched_name = _has_any_instructor_in_mit_db(instructors, mit_prof_db)
            if not in_db:
                print(f"[skip] no instructor found in mit_professors.db (instructors: {instructors!r})")
                processed.add(course_url)
                done += 1
                if course_url in failures:
                    failures.pop(course_url, None)
                snapshot = {
                    "processed": sorted(processed),
                    "failures": failures,
                    "last_total_seen": total,
                }
                state_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
                time.sleep(max(0.0, args.delay))
                continue

            rc = run_slidefetch(
                target,
                out_dir=out_dir,
                timeout=args.timeout,
                course_url=course_url,
                instructors=instructors,
                departments=departments,
                prerequisites=prerequisites,
            )
            if rc != 0:
                failures[course_url] = f"slidefetch exit code {rc}"
                print(f"[warn] slidefetch failed with exit code {rc}")
            elif course_url in failures:
                failures.pop(course_url, None)

            processed.add(course_url)
            done += 1

        except Exception as err:  # noqa: BLE001
            failures[course_url] = str(err)
            print(f"[warn] failed: {err}")

        snapshot = {
            "processed": sorted(processed),
            "failures": failures,
            "last_total_seen": total,
        }
        state_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
        time.sleep(max(0.0, args.delay))

    print("\n==== runner done ====")
    print(f"courses_seen: {total}")
    print(f"processed: {len(processed)}")
    print(f"new_processed_this_run: {done}")
    print(f"failures: {len(failures)}")
    print(f"state_file: {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
