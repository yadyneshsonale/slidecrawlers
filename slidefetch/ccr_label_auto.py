#!/usr/bin/env python3.11
"""Automatically label every slide-rich course (>= 4 ratings) with its top-2 slide links.

"Slide-rich" = the STEM / quantitative disciplines whose lecture slides are
widely posted online: CS, ECE, mathematics, statistics, physics, engineering,
chemistry and economics (see ``DISCIPLINES`` in ``ccr_slide_resolve``). Pass
``--disciplines math,physics`` to restrict to a subset.

For each eligible course this:
  1. gets candidate links -- from the cached ``ccr_slide_links.db`` first
     (instant), else a live DuckDuckGo search (Google is not scrapable: it
     returns a JS shell / consent wall, so it can only be used by a human in
     the label app);
  2. scores + ranks them with the same ``shortlist`` logic the resolver uses
     (aggregators rejected, academic/code/term/faculty signals scored);
  3. saves the top-2 links (with score + reason) to ``ccr_labels.db`` table
     ``auto_labels`` and re-exports ``ccr_auto_labels_review.md``.

The 12 golden examples keep the user's already-stated reason on link 1.

Resumable: rows already in ``auto_labels`` are skipped. Cached courses are
processed first so results appear immediately; the rest are searched live.

    python3.11 ccr_label_auto.py                 # cache + live, all slide-rich >=4
    python3.11 ccr_label_auto.py --no-live       # only the cached courses
    python3.11 ccr_label_auto.py --limit 50      # first 50 to-do courses
    python3.11 ccr_label_auto.py --disciplines math,physics,stats
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

import requests

from ccr_slide_resolve import (
    DISCIPLINES,
    course_discipline,
    load_courses,
    code_variants,
    _norm,
)
from ccr_slide_kb import classify_link, confirmation_trust
from ccr_slide_search import (
    search as ddg_search,
    brave_search,
    searxng_search,
    tavily_search,
    serpapi_search,
    QuotaExhausted,
)
from ccr_pattern_examples import EXAMPLES

COURSES_DB = "ccr_courses.db"
CACHE_DB = "ccr_slide_links.db"
OUT_DB = "ccr_labels.db"
REVIEW_MD = "ccr_auto_labels_review.md"
CORRECT_TXT = "correct.txt"
WRONG_TXT = "wrong.txt"

# Reasons the user has already stated, keyed by course.
STATED = {cc: why for cc, _chosen, why in EXAMPLES}

# How much a KB verdict is worth when re-ranking shortlisted candidates. A
# ``reject`` link (admin / catalog / registrar / library page) is dropped
# entirely -- this is what stops the labeller from emitting bulletin/registrar
# junk when the search only returned generic university pages.
_VERDICT_RANK = {"accept": 2, "ambiguous": 1}


def load_gold() -> tuple[dict[str, str], set[str]]:
    """Human-verified gold from correct.txt / wrong.txt.

    Returns ``(good, bad)`` where *good* maps ``course_college`` -> the verified
    slide link (adopted verbatim as the label) and *bad* is the set of courses a
    human confirmed have no usable slides (the labeller abstains on them).
    """
    good: dict[str, str] = {}
    bad: set[str] = set()
    try:
        with open(CORRECT_TXT, encoding="utf-8") as fh:
            for i, ln in enumerate(fh):
                if i == 0 or not ln.strip():
                    continue
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 4 and p[0].strip() and p[3].strip():
                    good[p[0].strip()] = p[3].strip()
    except FileNotFoundError:
        pass
    try:
        with open(WRONG_TXT, encoding="utf-8") as fh:
            for ln in fh:
                if not ln.strip():
                    continue
                key = ln.split("\t", 1)[0].strip()
                if key:
                    bad.add(key)
    except FileNotFoundError:
        pass
    return good, bad


def load_all_courses(courses_db: str
                     ) -> dict[str, tuple[str, str, str, int, str]]:
    """Every course in ``ccr_courses.db`` (no discipline / rating filter).

    course_college -> (course_code, college_name, department, num_ratings,
    discipline). Discipline is best-effort (``other`` when it matches no
    slide-rich group). Used when labelling the full catalogue.
    """
    con = sqlite3.connect(courses_db)
    out: dict[str, tuple[str, str, str, int, str]] = {}
    for cc, code, college, dept, nr in con.execute(
        "SELECT course_college, course_code, college_name, department, "
        "num_ratings FROM courses"
    ):
        if not cc:
            continue
        disc = course_discipline(code, dept) or "other"
        out.setdefault(cc, (code or "", college or "", dept or "", nr or 0, disc))
    con.close()
    return out


def init_out(out_db: str) -> sqlite3.Connection:
    con = sqlite3.connect(out_db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS auto_labels ("
        "course_college TEXT PRIMARY KEY, course_code TEXT, num_ratings INTEGER, "
        "link_1 TEXT, score_1 INTEGER, reason_1 TEXT, "
        "link_2 TEXT, score_2 INTEGER, reason_2 TEXT, "
        "num_candidates INTEGER, source TEXT, saved_at TEXT, discipline TEXT)"
    )
    # Raw search results per live-searched course, so a later blocklist / ranking
    # change can re-pick the best link WITHOUT spending API credits again.
    con.execute(
        "CREATE TABLE IF NOT EXISTS live_candidates ("
        "course_college TEXT PRIMARY KEY, candidates_json TEXT, "
        "provider TEXT, searched_at TEXT)"
    )
    # Migrate DBs created before the discipline column existed.
    cols = {r[1] for r in con.execute("PRAGMA table_info(auto_labels)")}
    if "discipline" not in cols:
        con.execute("ALTER TABLE auto_labels ADD COLUMN discipline TEXT")
    con.commit()
    return con


def load_cache(cache_db: str) -> dict[str, list[str]]:
    """course_college -> cached candidate links (from the earlier search run)."""
    try:
        con = sqlite3.connect(cache_db)
    except sqlite3.Error:
        return {}
    out: dict[str, list[str]] = {}
    try:
        rows = con.execute(
            "SELECT course_college, links_json FROM slide_links WHERE num_links > 0"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    for cc, lj in rows:
        try:
            links = json.loads(lj) if lj else []
        except (TypeError, json.JSONDecodeError):
            links = []
        if links:
            out[cc] = links
    con.close()
    return out


def load_live_candidates(out_db: str) -> dict[str, list[str]]:
    """course_college -> previously-fetched API search results (live_candidates).

    Lets a resume reuse searches already paid for, so only never-searched
    courses spend new API credits, and lets a changed blocklist re-pick for free.
    """
    out: dict[str, list[str]] = {}
    try:
        con = sqlite3.connect(out_db)
        rows = con.execute(
            "SELECT course_college, candidates_json FROM live_candidates"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return out
    for cc, cj in rows:
        try:
            links = json.loads(cj) if cj else []
        except (TypeError, json.JSONDecodeError):
            links = []
        if links:
            out[cc] = links
    return out


def export_markdown(con: sqlite3.Connection, path: str) -> None:
    rows = con.execute(
        "SELECT course_college, num_ratings, link_1, score_1, reason_1, "
        "link_2, score_2, reason_2, source, discipline FROM auto_labels "
        "ORDER BY COALESCE(discipline, 'zz'), course_college"
    ).fetchall()
    by_disc: dict[str, int] = {}
    for r in rows:
        by_disc[r[9] or "other"] = by_disc.get(r[9] or "other", 0) + 1
    summary = ", ".join(f"{d}:{n}" for d, n in sorted(by_disc.items()))
    lines = ["# Auto-labelled slide links (top 2 per slide-rich course, >= 4 ratings)",
             "", f"{len(rows)} course(s). By discipline: {summary}", ""]
    for cc, nr, l1, s1, r1, l2, s2, r2, src, disc in rows:
        lines.append(f"## {cc}  ({nr} ratings, {disc or 'other'}, {src})")
        if l1:
            lines.append(f"1. [{s1}] {l1}")
            lines.append(f"   - {r1}")
        if l2:
            lines.append(f"2. [{s2}] {l2}")
            lines.append(f"   - {r2}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def label_course(code: str, college: str, links: list[str],
                 cc: str) -> tuple | None:
    """Return the auto_labels row tuple for *cc*, or ``None`` if no usable link.

    Uses ONLY the reasoning distilled from the human correct.txt / wrong.txt
    annotations (:func:`ccr_slide_kb.classify_link`): every candidate is
    classified, the ones the reasoning calls wrong are dropped, and the single
    best-scored surviving link is kept.

      * ``reject`` (catalog / registrar / bulletin / library / homepage / README)
        -> dropped.
      * ``ambiguous`` with no course signal (a stray university page) -> dropped.
      * a code match on a *different* university's academic host (cross-university
        coincidence) -> dropped (``confirmation_trust``).

    Ranking is by the classifier verdict (``accept`` > ``ambiguous``), then its
    confidence, then a small deck/slide/code bonus -- NOT the old heuristic
    shortlist score. Only ``link_1`` is produced.
    """
    seen: set[str] = set()
    cands: list[tuple[int, float, int, str, str]] = []  # rank, conf, bonus, url, reason
    variants, _num = code_variants(code)
    for url in links:
        if not url or not url.strip():
            continue
        key = url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        v = classify_link(url, code, college)
        if v.verdict == "reject":
            continue
        # Drop a stray university page: an ``ambiguous`` link is only worth
        # keeping when it carries a course signal -- a slide/lecture/deck/
        # course-site marker in the classifier reason, or the course code in the
        # URL itself (e.g. a GitHub repo ``.../gt_cs7637/...``). ``accept`` links
        # always carry one.
        has_code = any(vv and _norm(vv) in _norm(url) for vv in variants)
        if v.verdict == "ambiguous" and not has_code and not re.search(
                r"code|slides?|deck|lecture|course-page|course-site|"
                r"github:slides|bare-root", v.reason):
            continue
        # Cross-university coincidence -> not this course's slides.
        trust, _why = confirmation_trust(url, code, college)
        if not trust:
            continue
        bonus = (2 if "deck" in v.reason else 0) + (
            1 if ("slides" in v.reason or has_code) else 0)
        cands.append((_VERDICT_RANK.get(v.verdict, 0), v.confidence, bonus,
                      url, v.reason))
    if not cands:
        return None
    cands.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    rank, conf, _bonus, url, reason = cands[0]
    verdict = "accept" if rank == 2 else "ambiguous"
    r1 = f"kb:{verdict}:{reason} (conf {conf:.2f})"
    if cc in STATED:  # keep the user's stated reasoning
        r1 = f"USER: {STATED[cc]} | {r1}"
    return (url, int(round(conf * 100)), r1, "", None, "", len(cands))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--courses-db", default=COURSES_DB)
    ap.add_argument("--cache-db", default=CACHE_DB)
    ap.add_argument("--out-db", default=OUT_DB)
    ap.add_argument("--min-ratings", type=int, default=4)
    ap.add_argument("--disciplines", default="",
                    help="comma-separated subset of slide-rich disciplines to "
                         "label (default: all). Choices: "
                         + ", ".join(DISCIPLINES))
    ap.add_argument("--no-live", action="store_true",
                    help="only label courses that already have cached links")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the cached links entirely and web-search every "
                         "course fresh (mutually useful with --disciplines)")
    ap.add_argument("--all-courses", action="store_true",
                    help="label EVERY course in ccr_courses.db (ignore the "
                         "slide-rich discipline / min-ratings filter)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--search-delay", type=float, default=2.0)
    ap.add_argument("--max-attempts", type=int, default=2,
                    help="DDG attempts per course; low = fail fast when DDG is "
                         "rate-limited (Google is not scrapable)")
    args = ap.parse_args()

    disciplines: list[str] | None = None
    if args.disciplines.strip():
        disciplines = [d.strip().lower() for d in args.disciplines.split(",")
                       if d.strip()]
        bad = [d for d in disciplines if d not in DISCIPLINES]
        if bad:
            ap.error(f"unknown discipline(s): {', '.join(bad)}; "
                     f"choose from {', '.join(DISCIPLINES)}")

    if args.all_courses:
        targets = load_all_courses(args.courses_db)
    else:
        targets = load_courses(args.courses_db, args.min_ratings, disciplines)
    cache = {} if args.no_cache else load_cache(args.cache_db)
    con = init_out(args.out_db)
    # Reuse searches already stored (and paid for) in a previous run, so a
    # resume only spends credits on never-searched courses.
    api_cache = load_live_candidates(args.out_db)
    done = {r[0] for r in con.execute("SELECT course_college FROM auto_labels")}

    # Backfill the discipline column on rows written before it existed.
    missing = con.execute(
        "SELECT course_college, course_code FROM auto_labels "
        "WHERE discipline IS NULL OR discipline = ''"
    ).fetchall()
    for mcc, mcode in missing:
        info = targets.get(mcc)
        disc = info[4] if info else course_discipline(mcode, None)
        con.execute("UPDATE auto_labels SET discipline = ? WHERE course_college = ?",
                    (disc, mcc))
    if missing:
        con.commit()

    # Human gold overrides everything: adopt the verified link for known-good
    # courses, and abstain (drop any existing auto row) for courses a human
    # confirmed have no usable slides. Done up-front so a rerun always reflects
    # the latest annotations, even for courses labelled in an earlier run.
    good_gold, bad_gold = load_gold()
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    gold_written = gold_dropped = 0
    for cc in targets:
        if cc in bad_gold:
            if cc in done:
                con.execute("DELETE FROM auto_labels WHERE course_college = ?", (cc,))
                gold_dropped += 1
            continue
        if cc in good_gold:
            code, college, _dept, nr, disc = targets[cc]
            con.execute(
                "INSERT OR REPLACE INTO auto_labels (course_college, course_code, "
                "num_ratings, link_1, score_1, reason_1, link_2, score_2, reason_2, "
                "num_candidates, source, saved_at, discipline) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [cc, code, nr, good_gold[cc], 100, "USER-VERIFIED (correct.txt)",
                 "", None, "", 1, "gold", now(), disc],
            )
            gold_written += 1
    if gold_written or gold_dropped:
        con.commit()
        done = {r[0] for r in con.execute("SELECT course_college FROM auto_labels")}
    gold_skip = set(good_gold) | bad_gold

    # Cached courses first (instant): the old link cache OR previously-fetched
    # API results (live_candidates). Then the rest via live API search. Gold
    # courses are already handled above and never searched.
    def _cached(cc: str) -> bool:
        return cc in cache or cc in api_cache
    cached_cc = [cc for cc in targets
                 if _cached(cc) and cc not in done and cc not in gold_skip]
    live_cc = [cc for cc in targets
               if not _cached(cc) and cc not in done and cc not in gold_skip]
    order = cached_cc + ([] if args.no_live else live_cc)
    if args.limit:
        order = order[: args.limit]

    disc_counts: dict[str, int] = {}
    for cc in targets:
        d = targets[cc][4]
        disc_counts[d] = disc_counts.get(d, 0) + 1
    breakdown = ", ".join(f"{d}:{n}" for d, n in sorted(disc_counts.items()))
    scope = ("all courses" if args.all_courses
             else f"slide-rich courses (>= {args.min_ratings} ratings)")
    print(f"{len(targets)} {scope} "
          f"[{breakdown}]; gold: {gold_written} adopted, {len(bad_gold & set(targets))} "
          f"no-slides ({gold_dropped} dropped); {len(done)} labelled; {len(cached_cc)} "
          f"cached + {0 if args.no_live else len(live_cc)} live to do", flush=True)

    session = requests.Session()

    # Keyed search APIs (no scraping rate-limits). Priority: Tavily -> SerpAPI.
    # When every configured API has run out of credits, PAUSE the run rather
    # than fall back to the blocked/garbage scrapers.
    api_have = {
        "tavily": bool(os.environ.get("TAVILY_API_KEY")),
        "serpapi": bool(os.environ.get("SERPAPI_API_KEY")
                        or os.environ.get("SERPAPI_KEY")),
    }
    use_apis = any(api_have.values())
    exhausted = {"tavily": False, "serpapi": False}
    if use_apis:
        print("search backends: "
              + ", ".join(k for k, v in api_have.items() if v)
              + " (keyed API; scrapers disabled)", flush=True)

    saved = 0
    paused = False
    for idx, cc in enumerate(order, 1):
        code, college, _dept, nr, disc = targets[cc]
        if cc in cache:
            links, source = cache[cc], "cache"
        elif cc in api_cache:
            # Reuse a previously-paid API search (re-picks with current rules).
            links, source = api_cache[cc], "recache"
        elif use_apis:
            query = f"{code} {college} course slides"
            links, source, prov = [], "live", ""
            if api_have["tavily"] and not exhausted["tavily"]:
                try:
                    links = tavily_search(session, query,
                                          max_attempts=args.max_attempts)
                    if links:
                        prov = "tavily"
                except QuotaExhausted:
                    exhausted["tavily"] = True
                    print(f"[{idx}/{len(order)}] Tavily quota exhausted "
                          f"-> switching to SerpAPI", flush=True)
            if not links and api_have["serpapi"] and not exhausted["serpapi"]:
                try:
                    links = serpapi_search(session, query,
                                           max_attempts=args.max_attempts)
                    if links:
                        prov = "serpapi"
                except QuotaExhausted:
                    exhausted["serpapi"] = True
                    print(f"[{idx}/{len(order)}] SerpAPI quota exhausted",
                          flush=True)
            # Cache the raw results so a future re-pick needs no new API credits.
            if links:
                con.execute(
                    "INSERT OR REPLACE INTO live_candidates "
                    "(course_college, candidates_json, provider, searched_at) "
                    "VALUES (?,?,?,?)",
                    [cc, json.dumps(links), prov, now()],
                )
                con.commit()
            # Pause if every configured API is now out of credits.
            if all(exhausted[k] for k, v in api_have.items() if v):
                print(f"\n[{idx}/{len(order)}] ALL search API quotas exhausted "
                      f"-> PAUSING (resumable: rerun to continue).", flush=True)
                paused = True
                break
            time.sleep(args.search_delay)
        else:
            query = f"{code} {college} course slides"
            try:
                # No API key -> fall back to local SearXNG, Brave, DDG (scrapers).
                links = searxng_search(session, query,
                                       max_attempts=args.max_attempts)
                if not links:
                    links = brave_search(session, query,
                                         max_attempts=args.max_attempts)
                if not links:
                    links = ddg_search(session, query,
                                       max_attempts=args.max_attempts)
                source = "live"
            except Exception as err:  # noqa: BLE001
                print(f"[{idx}/{len(order)}] {cc!r}: search failed ({err})",
                      flush=True)
                links, source = [], "live"
            time.sleep(args.search_delay)

        row = label_course(code, college, links, cc) if links else None
        if not row:
            print(f"[{idx}/{len(order)}] {cc!r}: no usable link ({source})",
                  flush=True)
            continue
        l1, s1, r1, l2, s2, r2, ncand = row
        con.execute(
            "INSERT OR REPLACE INTO auto_labels (course_college, course_code, "
            "num_ratings, link_1, score_1, reason_1, link_2, score_2, reason_2, "
            "num_candidates, source, saved_at, discipline) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [cc, code, nr, l1, s1, r1, l2, s2, r2, ncand, source, now(), disc],
        )
        con.commit()
        saved += 1
        if saved % 200 == 0:
            export_markdown(con, REVIEW_MD)
        extra = f" + {l2}" if l2 else ""
        print(f"[{idx}/{len(order)}] {cc!r} ({source}): SAVED top "
              f"{2 if l2 else 1} -> {l1}{extra}", flush=True)

    export_markdown(con, REVIEW_MD)
    total = con.execute("SELECT COUNT(*) FROM auto_labels").fetchone()[0]
    con.close()
    status = "PAUSED (API quotas exhausted)" if paused else "done"
    print(f"\n{status}; saved {saved} this run; {total} courses labelled in "
          f"{args.out_db} (+ {REVIEW_MD})")


if __name__ == "__main__":
    main()
