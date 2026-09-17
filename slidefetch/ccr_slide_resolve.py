#!/usr/bin/env python3.11
"""Find the actual slide links for every CS course in ``ccr_courses.db``.

Self-contained pipeline. Reads courses ONLY from ``ccr_courses.db`` (Computer
Science, with at least 4 ratings), searches the web itself for each, and saves
ONLY the resolved slide deck links. Patterns are distilled from
``final_scraper/method.txt``:

  0. Search     - web-search ``"<course_college> course slides"`` (DuckDuckGo,
                  no API key) for each CS course.
  1. Shortlist  - drop aggregator junk (coursehero/studocu/...), keep academic
                  and known course hosts (slidefetch.urls.is_university), plus
                  a low-priority allowance for github/github.io project pages.
  2. Rank       - score each surviving link by how strongly it matches the
                  course: course-code variants in the host/path, a
                  slide/lecture/notes keyword, a term token, the university name.
  3. Validate   - fetch the top candidates and look for actual slide decks
                  (slidefetch.extract.find_slides).
  4. Manipulate - if a candidate is only a course homepage, reach the slides by
                  reducing the URL, following one lecture/schedule sub-page, or
                  appending a known slide sub-path (lectures/ slides/ notes/ ...).

Only courses whose slide decks are found are written to ``ccr_course_slides.db``;
each row stores the deck links.

Usage:
    python3.11 ccr_slide_resolve.py                 # full run (resumes)
    python3.11 ccr_slide_resolve.py --limit 50
    python3.11 ccr_slide_resolve.py --min-ratings 4
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests

from ccr_slide_search import search as web_search_urls
from slidefetch.extract import find_index_links, find_slides
from slidefetch.fetch import fetch
from slidefetch.urls import host_of, is_university, reduce_url

# --------------------------------------------------------------------------- #
# CS filter
# --------------------------------------------------------------------------- #

# Course-code prefixes that denote a computing course even when the CCR
# department says something else (e.g. EECS6893 -> "Engineering",
# CMSC250 -> "Mathematics").
CS_CODE_PREFIX_RE = re.compile(
    r"^(CS|CSE|CSCI|CSC|COMP|CMSC|EECS|CPS|ECE|CIS|ICS|INFO|INF|SE|DATA|DSC)\d",
    re.IGNORECASE,
)
CS_DEPARTMENTS = {
    "computer science", "software engineering", "computer engineering",
    "computer & information sciences", "computer  information sciences",
    "computer science and engineering", "computer science  engineering",
    "computer science amp engineering", "computer engineering & computer science",
    "computer engineering  computer science", "electrical engineering & computer science",
    "electrical engineering amp computer science", "computer science and electrical engineering",
    "electrical & computer engineering", "systems & computer engineering",
    "computer & informational tech.", "computing security", "informatics",
    "data science", "computational science", "mathematical and computer sci.",
}


def is_cs(course_code: str | None, department: str | None) -> bool:
    if course_code and CS_CODE_PREFIX_RE.match(course_code):
        return True
    if department and department.strip().lower() in CS_DEPARTMENTS:
        return True
    return False


# --------------------------------------------------------------------------- #
# Slide-rich discipline filter
# --------------------------------------------------------------------------- #
#
# CS is not the only field whose lecture slides are widely posted online. The
# other STEM / quantitative disciplines below (ECE, mathematics, statistics,
# physics, engineering, chemistry, economics) publish course slide decks just
# as freely, so the auto-labeller can resolve them with the same patterns.
#
# Each discipline maps to (compiled course-code prefix regex, {department names
# lowercased}). A course belongs to a discipline if its code prefix OR its CCR
# department matches. Groups are checked in registry order, so CS wins for a
# code that could match two groups (e.g. ECE-coded EECS courses stay "cs",
# preserving previously-saved labels).

DISCIPLINES: dict[str, tuple[re.Pattern[str], set[str]]] = {
    "cs": (CS_CODE_PREFIX_RE, CS_DEPARTMENTS),
    "ece": (
        re.compile(r"^(ECE|EEE|EECE|EENG|ELEC|ECEN|ECEG|EE)\d", re.IGNORECASE),
        {
            "electrical engineering", "electrical & computer engineering",
            "electrical and computer engineering",
            "electrical & electronic engineering",
            "electrical and electronic engineering", "electronic engineering",
            "electronics", "electrical engineering technology",
        },
    ),
    "math": (
        re.compile(r"^(MATH|MTHE|MTHS|MTH|MATP|MATT|MAT|APMA|AMTH|MA)\d",
                   re.IGNORECASE),
        {
            "mathematics", "math", "mathematical sciences",
            "applied mathematics", "pure mathematics",
            "mathematics & statistics", "mathematics and statistics",
            "mathematical and computer sci.",
        },
    ),
    "stats": (
        re.compile(r"^(STATS|STAT|STOR|BIOST|BIOS|STT|STA|BST)\d", re.IGNORECASE),
        {
            "statistics", "biostatistics", "statistical science",
            "statistics & probability", "statistics and probability",
        },
    ),
    "physics": (
        re.compile(r"^(PHYS|PHYW|ASTRO|ASTR|PHY|AST)\d", re.IGNORECASE),
        {
            "physics", "astronomy", "astrophysics",
            "physics & astronomy", "physics and astronomy",
        },
    ),
    "engineering": (
        re.compile(
            r"^(ENGR|ENGN|ENGG|EGR|MECH|MENG|MEEN|MAE|CIVE|CIVL|CEE|CHEN|CHME|"
            r"BMED|BIOE|BME|ISYE|IENG|ISE|MATE|MSE|AERO|AAE|NUEN|NUCE|ENVE|"
            r"ENVR|ME|CE|AE|IE)\d",
            re.IGNORECASE,
        ),
        {
            "engineering", "mechanical engineering", "civil engineering",
            "chemical engineering", "biomedical engineering",
            "industrial engineering", "aerospace engineering",
            "materials science", "materials engineering",
            "environmental engineering", "engineering technology",
            "engineering science", "nuclear engineering",
            "systems engineering", "mechanical & aerospace engineering",
            "civil & environmental engineering",
        },
    ),
    "chemistry": (
        re.compile(r"^(CHEM|CHMY|BCHM|BIOC|CHM)\d", re.IGNORECASE),
        {
            "chemistry", "biochemistry", "chemistry & biochemistry",
            "chemistry and biochemistry",
        },
    ),
    "economics": (
        re.compile(r"^(ECON|ECNS|ECN|ECO)\d", re.IGNORECASE),
        {"economics", "agricultural economics", "applied economics"},
    ),
}


def course_discipline(course_code: str | None, department: str | None,
                      disciplines: list[str] | None = None) -> str | None:
    """Return the slide-rich discipline key for a course, or ``None``.

    *disciplines* optionally restricts (and orders) which groups are tried;
    defaults to every group in :data:`DISCIPLINES`.
    """
    keys = disciplines if disciplines is not None else list(DISCIPLINES)
    code = (course_code or "").strip()
    dept = (department or "").strip().lower()
    for key in keys:
        rx, deps = DISCIPLINES[key]
        if (code and rx.match(code)) or (dept and dept in deps):
            return key
    return None


def is_slide_rich(course_code: str | None, department: str | None,
                  disciplines: list[str] | None = None) -> bool:
    return course_discipline(course_code, department, disciplines) is not None


# --------------------------------------------------------------------------- #
# Pattern 3: course-code -> URL-token variants
# --------------------------------------------------------------------------- #

_CODE_RE = re.compile(r"^([A-Za-z]+)\s*0*(\d+)([A-Za-z]?)$")


def code_variants(course_code: str) -> tuple[set[str], str | None]:
    """Return (string-variants, numeric-core) for matching a code in a URL.

    e.g. ``CS0015`` -> ({"cs0015","cs015","cs15","cs-15","cs_15"}, "15").
    The numeric core (leading zeros stripped) is the strong matching signal.
    """
    code = (course_code or "").strip()
    variants: set[str] = set()
    if not code:
        return variants, None
    low = code.lower().replace(" ", "")
    variants.add(low)
    m = _CODE_RE.match(code)
    num = None
    if m:
        prefix = m.group(1).lower()
        raw_num = m.group(2)              # e.g. "0015"
        num = raw_num.lstrip("0") or "0"  # fully stripped, e.g. "15"
        suffix = m.group(3).lower()
        # Progressive zero strips: "0015" -> {"0015","015","15"} so we match
        # whichever padding the course site used (CS0015 -> cs015 at Brown).
        num_forms = {raw_num, num}
        n = raw_num
        while n.startswith("0") and len(n) > 1:
            n = n[1:]
            num_forms.add(n)
        for nf in num_forms:
            for sep in ("", "-", "_"):
                variants.add(f"{prefix}{sep}{nf}")
                variants.add(f"{prefix}{sep}{nf}{suffix}")
    return {v for v in variants if v}, num


# --------------------------------------------------------------------------- #
# Pattern 1/2: link shortlisting + ranking
# --------------------------------------------------------------------------- #

# Aggregators that never hold the real course deck. ``is_university`` already
# rejects most of these; this catches the few that slip through on a .com.
EXTRA_BLOCK = (
    "coursicle.com", "slidetodoc.com", "studocu.com", "coursehero.com",
    "scribd.com", "slideshare.net", "quizlet.com", "chegg.com",
    "catalog.", "courses.illinois.edu", "classes.", "ratemyprofessors.com",
    "youtube.com", "youtu.be", "reddit.com",
)

SLIDE_KEYWORDS = (
    "slide", "slides", "lecture", "lectures", "lec", "notes", "handout",
    "handouts", "schedule", "calendar", "lessons", "presentation",
)
# Term tokens that confirm a real course offering in the path.
TERM_RE = re.compile(
    r"(?:^|[/_-])(?:"
    r"(?:fall|spring|summer|winter|fa|sp|su|wi|au)\d{2,4}"
    r"|\d{4}(?:fall|spring|summer|winter|fa|sp|su|wi)"
    r"|\d{2}[sfuw]"
    r"|[sfuw]\d{2}"
    r"|ay\d{4}"
    r"|\d{4}-\d{2}"
    r")",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    """Lowercase and strip non-alphanumerics for loose substring matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _univ_tokens(college_name: str) -> list[str]:
    drop = {
        "university", "college", "institute", "of", "the", "at", "state",
        "technology", "school", "amp", "and", "polytechnic", "system",
    }
    toks = re.split(r"[^A-Za-z]+", (college_name or "").lower())
    return [t for t in toks if len(t) >= 4 and t not in drop]


def score_link(url: str, variants: set[str], num: str | None,
               univ_toks: list[str]) -> tuple[int, str]:
    """Score a candidate result URL for one course. Higher = better.

    Returns ``(score, reason)``; a score < 0 means "reject".
    """
    host = host_of(url)
    if not host:
        return -1, "no-host"
    if any(b in host or url.lower().startswith(f"https://{b}") for b in EXTRA_BLOCK):
        return -1, f"aggregator:{host}"

    ok, why = is_university(url, allow_course_hosts=True)
    is_github = host.endswith("github.com") or host.endswith("github.io")
    if not ok and not is_github:
        return -1, why

    parts = urlsplit(url)
    host_n = _norm(host)
    path_n = _norm(parts.path)
    blob = host_n + path_n

    score = 0
    reasons = [why if ok else "github"]
    if why == "academic-tld":
        score += 5
    elif why in ("known-university", "course-host"):
        score += 3
    elif is_github:
        score += 1  # project pages are a last resort (method.txt marks them ???)

    # Strongest signal: the course code (prefix+number) appears in host/path.
    code_hit = next((v for v in variants if _norm(v) and _norm(v) in blob), None)
    if code_hit:
        score += 8
        reasons.append(f"code:{code_hit}")
    elif num and num in path_n:
        score += 2
        reasons.append(f"num:{num}")

    # Slide / lecture / notes keyword in the path.
    kw = next((k for k in SLIDE_KEYWORDS if k in parts.path.lower()), None)
    if kw:
        score += 4
        reasons.append(f"kw:{kw}")

    # A term token confirms a real offering.
    if TERM_RE.search(parts.path):
        score += 3
        reasons.append("term")

    # University name present in the host (auburn, cornell, ...).
    if any(t in host_n for t in univ_toks):
        score += 2
        reasons.append("univ")

    # Faculty page (~user) is a common course-material location.
    if "/~" in parts.path or re.search(r"/~", url):
        score += 1
        reasons.append("faculty")

    return score, ",".join(reasons)


def shortlist(links: list[str], course_code: str,
              college_name: str) -> list[tuple[int, str, str]]:
    """Rank candidate links for a course; reject aggregators/non-academic.

    Returns ``[(score, url, reason)]`` sorted best-first.
    """
    variants, num = code_variants(course_code)
    univ_toks = _univ_tokens(college_name)
    ranked: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for url in links:
        if not url:
            continue
        key = url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        sc, reason = score_link(url, variants, num, univ_toks)
        if sc < 0:
            continue
        ranked.append((sc, url, reason))
    ranked.sort(key=lambda r: r[0], reverse=True)
    return ranked


# --------------------------------------------------------------------------- #
# Pattern 4: link manipulation
# --------------------------------------------------------------------------- #

SLIDE_SUBPATHS = (
    "lectures/", "slides/", "notes/", "lecture_slides/", "schedule.html",
    "lectures.html", "lectures.htm", "lect.html", "handouts.shtml",
    "LectureNotes.html", "calendar.html", "syllabus.html",
)


def _clean(url: str) -> str:
    """Drop fragments and ``;jsessionid=...`` style junk (DePaul pattern)."""
    parts = urlsplit(url)
    path = re.sub(r";jsessionid=[^/?]*", "", parts.path, flags=re.IGNORECASE)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


# Repo hosts where blindly appending slide sub-paths (e.g. ``/lectures.html``)
# would build bogus URLs / false positives. Pages hosts (github.io, gitlab.io)
# are real course sites and stay eligible.
APPEND_BLOCK_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")


def _appended_candidates(url: str) -> list[str]:
    host = host_of(url)
    if any(host == h or host.endswith("." + h) for h in APPEND_BLOCK_HOSTS):
        return []
    base = _clean(url)
    if not base.endswith("/") and not re.search(r"\.[a-z]{2,5}$", urlsplit(base).path):
        base += "/"
    root = base if base.endswith("/") else base.rsplit("/", 1)[0] + "/"
    out: list[str] = []
    for sub in SLIDE_SUBPATHS:
        out.append(root + sub)
    return out


def _bump_year(url: str):
    """Yield the URL with its 4-digit year advanced (S21->newer offerings)."""
    m = re.search(r"(19|20)\d{2}", url)
    if not m:
        return
    year = int(m.group(0))
    for new in range(year + 1, datetime.now().year + 2):
        yield url[: m.start()] + str(new) + url[m.end():]


def validate(url: str, timeout_ms: int) -> tuple[int, list[str]]:
    """Fetch *url* and return (slide_count, all_slide_deck_urls)."""
    try:
        res = fetch(url, timeout_ms=timeout_ms)
    except Exception:  # noqa: BLE001
        return 0, []
    slides = find_slides(res.html, url, raw_html=res.raw_html)
    return len(slides), [s.url for s in slides]


def _url_has_code(url: str, variants: set[str], num: str | None) -> bool:
    """True if *url* carries a course-code variant (host/path), i.e. it is
    course-specific rather than a generic catalog page."""
    blob = _norm(urlsplit(url).netloc + urlsplit(url).path)
    if any(_norm(v) and _norm(v) in blob for v in variants):
        return True
    return bool(num and num in _norm(urlsplit(url).path))


def manipulate(url: str, timeout_ms: int, max_subpages: int,
               variants: set[str] | None = None,
               num: str | None = None) -> tuple[str, int, list[str], str] | None:
    """Try to reach the slides from a homepage-like *url*.

    Order: reduce -> follow one lecture/schedule sub-page -> append slide
    sub-paths. Returns ``(slide_url, count, samples, method)`` or ``None``.
    """
    variants = variants or set()
    # 1) reduce a deep URL to its listing directory and re-check.
    reduced = reduce_url(_clean(url))
    if reduced != url:
        n, samples = validate(reduced, timeout_ms)
        if n:
            return reduced, n, samples, "reduced"

    # 2) follow same-site lecture/schedule sub-pages.
    try:
        res = fetch(_clean(url), timeout_ms=timeout_ms)
        for sub in find_index_links(res.html, url, raw_html=res.raw_html)[:max_subpages]:
            n, samples = validate(sub, timeout_ms)
            if n:
                return sub, n, samples, "followed"
    except Exception:  # noqa: BLE001
        pass

    # 3) append known slide sub-paths -- only to course-specific bases, so we
    #    do not turn a generic catalog page into a bogus per-course "hit".
    if _url_has_code(url, variants, num):
        for cand in _appended_candidates(url):
            n, samples = validate(cand, timeout_ms)
            if n:
                return cand, n, samples, "appended"

    # 4) bump the year (stale offering).
    for cand in _bump_year(_clean(url)):
        n, samples = validate(cand, timeout_ms)
        if n:
            return cand, n, samples, "term-bumped"
    return None


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def load_courses(courses_db: str, min_ratings: int,
                 disciplines: list[str] | None = None,
                 ) -> dict[str, tuple[str, str, str, int, str]]:
    """course_college -> (course_code, college_name, department, num_ratings, discipline).

    Only slide-rich courses (see :data:`DISCIPLINES`) with at least
    *min_ratings* ratings, read exclusively from ``ccr_courses.db``. Pass
    *disciplines* to restrict to a subset (e.g. ``["math", "physics"]``).
    """
    conn = sqlite3.connect(courses_db)
    out: dict[str, tuple[str, str, str, int, str]] = {}
    for cc, code, college, dept, nr in conn.execute(
        "SELECT course_college, course_code, college_name, department, num_ratings "
        "FROM courses WHERE num_ratings >= ?",
        (min_ratings,),
    ):
        if not cc:
            continue
        disc = course_discipline(code, dept, disciplines)
        if disc:
            out.setdefault(cc, (code or "", college or "", dept or "", nr or 0, disc))
    conn.close()
    return out


def load_cs_courses(courses_db: str, min_ratings: int) -> dict[str, tuple[str, str, str, int]]:
    """course_college -> (course_code, college_name, department, num_ratings).

    Backwards-compatible CS-only loader (drops the discipline field).
    """
    full = load_courses(courses_db, min_ratings, disciplines=["cs"])
    return {cc: v[:4] for cc, v in full.items()}


def init_db(conn: sqlite3.Connection) -> None:
    # Only slide links are stored: one row per course whose decks were found.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS course_slides (
            course_college   TEXT PRIMARY KEY,
            course_code      TEXT,
            college_name     TEXT,
            department       TEXT,
            num_ratings      INTEGER,
            slide_page_url   TEXT,
            slide_count      INTEGER,
            method           TEXT,
            slide_links_json TEXT,
            resolved_at      TEXT
        )
        """
    )
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--courses-db", default="ccr_courses.db",
                    help="the ONLY input DB (default: ccr_courses.db)")
    ap.add_argument("--out-db", default="ccr_course_slides.db",
                    help="output DB holding only resolved slide links")
    ap.add_argument("--min-ratings", type=int, default=4,
                    help="only courses with at least this many ratings (default: 4)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N CS courses")
    ap.add_argument("--top", type=int, default=3,
                    help="how many ranked candidates to validate (default: 3)")
    ap.add_argument("--timeout", type=int, default=25000,
                    help="page navigation timeout in ms")
    ap.add_argument("--max-subpages", type=int, default=8,
                    help="max lecture/schedule sub-pages to follow per course")
    ap.add_argument("--search-delay", type=float, default=2.0,
                    help="base seconds to wait between web searches")
    args = ap.parse_args()

    cs = load_cs_courses(args.courses_db, args.min_ratings)
    targets = sorted(cs)
    if args.limit is not None:
        targets = targets[: args.limit]

    out = sqlite3.connect(args.out_db)
    init_db(out)
    done = {r[0] for r in out.execute("SELECT course_college FROM course_slides")}
    todo = [cc for cc in targets if cc not in done]
    print(f"{len(cs)} CS courses (>= {args.min_ratings} ratings); "
          f"{len(done)} already have slides; {len(todo)} to do", flush=True)

    session = requests.Session()
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    saved = 0
    for idx, cc in enumerate(todo, 1):
        code, college, dept, nr = cs[cc]

        # 0) search the web ourselves (DuckDuckGo, no extra DB, no API key).
        try:
            urls = web_search_urls(session, f"{cc} course slides")
        except Exception as err:  # noqa: BLE001
            print(f"[{idx}/{len(todo)}] {cc!r}: search failed ({err})", flush=True)
            continue
        ranked = shortlist(urls, code, college)
        variants, num = code_variants(code)

        slide_url, slide_count, method, links = None, 0, "none", []
        for sc, url, _ in ranked[: args.top]:
            n, found = validate(url, args.timeout)
            if n:
                slide_url, slide_count, method, links = url, n, "direct", found
                break
            hit = manipulate(url, args.timeout, args.max_subpages, variants, num)
            if hit:
                slide_url, slide_count, links, method = hit
                break

        # Save ONLY when real slide links were found.
        if slide_url and links:
            out.execute(
                "INSERT OR REPLACE INTO course_slides (course_college, "
                "course_code, college_name, department, num_ratings, "
                "slide_page_url, slide_count, method, slide_links_json, "
                "resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [cc, code, college, dept, nr, slide_url, slide_count, method,
                 json.dumps(links), now()],
            )
            out.commit()
            saved += 1
            print(f"[{idx}/{len(todo)}] {cc!r}: SAVED {slide_count} slide links "
                  f"via {method} -> {slide_url}", flush=True)
        else:
            print(f"[{idx}/{len(todo)}] {cc!r}: no slides found", flush=True)
        time.sleep(args.search_delay)

    total = out.execute("SELECT COUNT(*) FROM course_slides").fetchone()[0]
    out.close()
    print(f"\ndone; saved {saved} this run; {total} courses with slide links "
          f"in {args.out_db}")


if __name__ == "__main__":
    main()
