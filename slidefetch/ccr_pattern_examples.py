#!/usr/bin/env python3.11
"""Show the top-20 search links next to YOUR chosen slide URL for N examples.

For every golden example taken from ``final_scraper/method.txt`` this runs the
same query the pipeline uses -- ``"<course_college> course slides"`` -- pulls
the top 20 result links, and prints them next to the URL you picked, annotated
with the resolver's pattern score so the choice criteria are explicit.

Only CS courses with >= 4 ratings are included (verified against ccr_courses.db).

    python3.11 ccr_pattern_examples.py            # all examples
    python3.11 ccr_pattern_examples.py -n 6       # first 6 examples
"""
from __future__ import annotations

import argparse
import random
import re
import sqlite3
import time
import urllib.parse

import requests

from ccr_slide_search import (
    HTML_ENDPOINT, LITE_ENDPOINT, USER_AGENTS, _HTML_RE, _LITE_RE, _decode,
)
from ccr_slide_resolve import _univ_tokens, code_variants, is_cs, score_link
from slidefetch.urls import host_of

# (course_college, your_chosen_url, why-you-picked-it) straight from method.txt.
EXAMPLES: list[tuple[str, str, str]] = [
    ("COMP2710 - Auburn University",
     "https://www.eng.auburn.edu/~xqin/courses/comp2710/spring10/lectures.htm",
     "faculty ~user page on the .edu, course code + term + 'lectures' in path"),
    ("CS143 - Stanford University",
     "https://web.stanford.edu/class/cs143/lectures/",
     "stanford /class/<code>/lectures/ dept pattern"),
    ("CS135 - University of Waterloo",
     "https://student.cs.uwaterloo.ca/~cs135/slides/",
     "dept student.cs host, ~<code>/slides/"),
    ("CMSC250 - University of Maryland",
     "https://www.cs.umd.edu/~gasarch/COURSES/250/S26/slides.html",
     "cross-listed (Math dept) but lives on cs.umd.edu; ~prof + number + slides"),
    ("COMP233 - Concordia University",
     "https://users.encs.concordia.ca/~doedel/courses/comp-233/slides.pdf",
     "hyphenated code comp-233, single combined slides.pdf (@@)"),
    ("CSE167 - University of California, San Diego",
     "https://cseweb.ucsd.edu/classes/wi18/cse167-a/",
     "cseweb host, term wi18, code with section suffix cse167-a"),
    ("EECS6893 - Columbia University",
     "https://www.ee.columbia.edu/~cylin/course/bigdata/",
     "named course 'bigdata' (no code in URL); cross-listed on ee.columbia.edu"),
    ("CSE205 - Arizona State University",
     "http://www.javiergs.com/teaching/cse205/",
     "instructor personal .com site /teaching/<code>"),
    ("CS122 - Carnegie Mellon University",
     "https://www.cs.cmu.edu/~15122/handouts.shtml",
     "CMU 15- numbering (CS122 -> 15122); handouts.shtml not 'slides'"),
    ("CS4820 - Cornell University",
     "https://www.cs.cornell.edu/courses/cs4820/2024sp/lectures/",
     "cs.cornell.edu/courses/<code>/<term>/lectures/"),
    ("CS3510 - Georgia Institute of Technology",
     "https://www.cs3510.com/lectures/",
     "course has its own vanity .com domain /lectures/"),
    ("CSE232 - Michigan State University",
     "https://cse.msu.edu/~cse232/us22/lectures.html",
     "cse.msu.edu/~<code>/<term>/lectures.html"),
]


def search20(session: requests.Session, query: str, want: int = 20,
             max_attempts: int = 6) -> list[str]:
    """Return up to *want* organic result URLs (top of the page first)."""
    for attempt in range(max_attempts):
        headers = {"User-Agent": random.choice(USER_AGENTS),
                   "Accept-Language": "en-US,en;q=0.9"}
        for endpoint, pattern in ((HTML_ENDPOINT, _HTML_RE), (LITE_ENDPOINT, _LITE_RE)):
            try:
                resp = session.post(endpoint, data={"q": query},
                                    headers=headers, timeout=25)
            except requests.RequestException:
                continue
            if resp.status_code == 200:
                seen, out = set(), []
                for href in pattern.findall(resp.text):
                    url = _decode(href)
                    if url not in seen:
                        seen.add(url)
                        out.append(url)
                if out:
                    return out[:want]
        time.sleep(5 * (attempt + 1) + random.uniform(0, 3))
    return []


def _host_match(a: str, b: str) -> bool:
    return bool(host_of(a)) and host_of(a) == host_of(b)


def _norm_url(u: str) -> str:
    """Loose URL key for exact-match detection (scheme/host/path only)."""
    p = urllib.parse.urlsplit(u.strip())
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    path = re.sub(r"%7e", "~", p.path, flags=re.IGNORECASE).rstrip("/").lower()
    return f"{host}{path}"


def verify_cs_4ratings(courses_db: str) -> dict[str, int]:
    """course_college -> num_ratings, restricted to CS courses with >= 4."""
    conn = sqlite3.connect(courses_db)
    out: dict[str, int] = {}
    for cc, code, dept, nr in conn.execute(
        "SELECT course_college, course_code, department, num_ratings FROM courses"
    ):
        if cc and is_cs(code, dept) and (nr or 0) >= 4:
            out[cc] = nr
    conn.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--num", type=int, default=len(EXAMPLES),
                    help="how many examples to show")
    ap.add_argument("--courses-db", default="ccr_courses.db")
    ap.add_argument("--out", default="pattern_examples_review.md",
                    help="report file you can edit and type your reasons into")
    ap.add_argument("--delay", type=float, default=3.0)
    args = ap.parse_args()

    eligible = verify_cs_4ratings(args.courses_db)
    session = requests.Session()
    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    def flush() -> None:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError as err:
            print(f"[warn] could not write {args.out}: {err}")

    emit("# Pattern review: top-20 links vs. your chosen slide URL")
    emit("")
    emit("For each CS example (>= 4 ratings) below: your pick, my pattern read, "
         "and the top-20 search results scored by the resolver's rules.")
    emit("Fill in the `YOUR REASON` blank in your own words so the heuristics "
         "can be tuned to match how you actually choose.")
    emit("")

    for i, (cc, chosen, why) in enumerate(EXAMPLES[: args.num], 1):
        code = cc.split(" - ", 1)[0]
        college = cc.split(" - ", 1)[1] if " - " in cc else ""
        nr = eligible.get(cc)
        variants, num = code_variants(code)
        univ_toks = _univ_tokens(college)

        emit("=" * 100)
        emit(f"## [{i}] {cc}   (ratings={nr}, CS+>=4={'YES' if nr else 'NO'})")
        emit(f"- YOUR PICK : {chosen}")
        emit(f"- MY READ   : {why}")
        emit(f"- code variants looked for: {sorted(variants)}  num={num}")
        emit("")
        emit("  YOUR REASON (type here): ______________________________________")
        emit("")

        urls = search20(session, f"{cc} course slides", want=20)
        if not urls:
            emit("  (no results returned)")
            emit("")
            flush()
            time.sleep(args.delay)
            continue

        chosen_key = _norm_url(chosen)
        exact_rank = next((r for r, u in enumerate(urls, 1)
                           if _norm_url(u) == chosen_key), None)
        host_ranks = [r for r, u in enumerate(urls, 1) if _host_match(u, chosen)]
        if exact_rank:
            emit(f"  >> your exact pick appeared at rank #{exact_rank}.")
        elif host_ranks:
            emit(f"  >> your pick's host appears at rank(s) {host_ranks}, but the "
                 f"exact page is NOT in the top 20 -> you reached it by link "
                 f"manipulation (path/term edit).")
        else:
            emit("  >> your pick is NOT in the top 20 at all -> found purely by "
                 "host/code knowledge or manipulation, not the raw results.")
        emit("-" * 100)

        for rank, url in enumerate(urls, 1):
            sc, reason = score_link(url, variants, num, univ_toks)
            if _norm_url(url) == chosen_key:
                mark = "  <<<< YOUR EXACT PICK"
            elif _host_match(url, chosen):
                mark = "  <<<< same host as your pick"
            else:
                mark = ""
            tag = "REJECT" if sc < 0 else f"score={sc:>2}"
            emit(f"  {rank:>2}. [{tag:>9}] {url}{mark}")
            if sc >= 0:
                emit(f"        reason: {reason}")
        emit("")
        flush()
        time.sleep(args.delay)

    print(f"\n[written] editable report -> {args.out}")


if __name__ == "__main__":
    main()
