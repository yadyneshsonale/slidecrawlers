#!/usr/bin/env python3
"""Download slides for every RateMyProfessors course with >=N ratings that has a
COMPLETE 1..N lecture-slide sequence.

This is the RMP twin of ``work/_dl_rated4ccr.py``: it runs the exact same
resolve -> gather -> assess -> download pipeline, but the course universe comes
from RateMyProfessors instead of College Class Reviews.

Pipeline (resumable, keyed by ``course_school`` = "<CODE> - <School> - <Instructor>"):

  1. select   -- rmp_course_ratings.db ``course_summary`` = one row per
                 (professor, course) they teach; keep every row with num_ratings
                 >= N. Each professor+course is its own search target.
  2. resolve  -- a subagent finds the best lecture-slides page/deck URL per course
                 using the course code + university + INSTRUCTOR name
                 (--emit-search -> subagent -> --ingest-search). When no subagent
                 link is stored, it falls back to web search ("<code> <school>
                 <instructor> course slides"), unless --no-search is given.
  3. gather   -- slidefetch _gather + link fixes (github raw/repo, google slides)
  4. assess   -- _completeness.assess (gap-free 1..N); ambiguous ones are judged by
                 a subagent (--emit -> subagent -> --ingest), NOT a local LLM.
  5. download -- COMPLETE courses only, kept only if the slides really land on
                 disk in order; partial folders are removed.

Both the search and the completeness judgement use a subagent, so no vLLM/LLM
endpoint is touched (that endpoint is shared with other programs).

Storage (see --out, default rated4rmp/):
  * rated4rmp.db         -- METADATA, only complete-and-in-order courses (+ decks)
  * rated4rmp_search.db  -- searched links + subagent links + per-course progress
  * rated4rmp/<slug>/    -- the downloaded decks for each complete course

Run from the slidefetch root with the venv::

    # 1. emit courses that need a link, hand to a search subagent, ingest links
    PYTHONPATH=. .venv/bin/python work/_dl_rated4rmp.py --emit-search > work/rmp_search.json
    PYTHONPATH=. .venv/bin/python work/_dl_rated4rmp.py --ingest-search work/rmp_links.tsv
    # 2. gather + assess + download using the subagent links (no web search, no LLM)
    PYTHONPATH=. .venv/bin/python work/_dl_rated4rmp.py --no-search
    # 3. judge the ambiguous ones with a subagent, then download the approved
    PYTHONPATH=. .venv/bin/python work/_dl_rated4rmp.py --emit > work/rmp_amb.json
    PYTHONPATH=. .venv/bin/python work/_dl_rated4rmp.py --ingest work/rmp_verdicts.tsv
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import requests

import _completeness as comp
import _dl_rated4ccr as base
from slidefetch.download import sanitize
from slidefetch.urls import reduce_url

ROOT = Path(__file__).resolve().parent.parent          # slidefetch/
RMP_DB = ROOT / "rmp_course_ratings.db"


def _now() -> str:
    return base._now()


# --------------------------------------------------------------------------- #
# Databases: metadata (complete only) + searches/progress (separate)
# --------------------------------------------------------------------------- #
def open_metadata_db(out_dir: Path) -> sqlite3.Connection:
    """rated4rmp.db -- ONLY courses whose slides are present and in order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(out_dir / "rated4rmp.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS courses (
            course_school   TEXT PRIMARY KEY,
            course_code     TEXT,
            school          TEXT,
            instructor      TEXT,
            legacy_id       TEXT,
            num_ratings     INTEGER,
            avg_quality     REAL,
            avg_difficulty  REAL,
            resolved_link   TEXT,
            link_source     TEXT,   -- subagent-search | websearch
            folder          TEXT,
            folder_path     TEXT,
            num_decks       INTEGER,
            seq_kind        TEXT,
            seq_min         INTEGER,
            seq_max         INTEGER,
            seq_expected    INTEGER,
            complete_method TEXT,   -- code | llm | subagent
            complete_reason TEXT,
            sequence_json   TEXT,
            updated_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS decks (
            course_school   TEXT,
            sequence_no     INTEGER,
            lecture_number  INTEGER,
            seq_label       TEXT,
            slide_url       TEXT,
            file_type       TEXT,
            saved_path      TEXT,
            sha256          TEXT,
            status          TEXT,   -- saved | skipped
            error           TEXT,
            UNIQUE(course_school, slide_url)
        );
        """
    )
    con.commit()
    return con


def open_search_db(out_dir: Path) -> sqlite3.Connection:
    """rated4rmp_search.db -- searched links (all) + progress for every course."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(out_dir / "rated4rmp_search.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_results (
            course_school   TEXT PRIMARY KEY,
            course_code     TEXT,
            school          TEXT,
            query           TEXT,
            provider        TEXT,   -- tavily | serpapi | ddg | subagent | none
            results_json    TEXT,
            searched_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS resolved_links (
            course_school   TEXT PRIMARY KEY,
            url             TEXT,
            reason          TEXT,
            source          TEXT,   -- subagent-search
            added_at        TEXT
        );
        CREATE TABLE IF NOT EXISTS progress (
            course_school   TEXT PRIMARY KEY,
            course_code     TEXT,
            school          TEXT,
            instructor      TEXT,
            num_ratings     INTEGER,
            resolved_link   TEXT,
            link_source     TEXT,
            search_query    TEXT,
            status          TEXT,   -- complete|incomplete|ambiguous|download_incomplete|no_link|no_decks|error
            num_decks       INTEGER,
            seq_kind        TEXT,
            seq_expected    INTEGER,
            complete_method TEXT,
            complete_reason TEXT,
            candidate_decks_json TEXT,
            sequence_json   TEXT,
            folder          TEXT,
            updated_at      TEXT
        );
        """
    )
    con.commit()
    return con


def upsert_course(meta: sqlite3.Connection, row: dict) -> None:
    cols = [
        "course_school", "course_code", "school", "instructor", "legacy_id",
        "num_ratings", "avg_quality", "avg_difficulty", "resolved_link",
        "link_source", "folder", "folder_path", "num_decks", "seq_kind",
        "seq_min", "seq_max", "seq_expected", "complete_method",
        "complete_reason", "sequence_json", "updated_at",
    ]
    row = {**{c: None for c in cols}, **row, "updated_at": _now()}
    ph = ",".join("?" for _ in cols)
    upd = ",".join(f"{c}=excluded.{c}" for c in cols if c != "course_school")
    meta.execute(
        f"INSERT INTO courses ({','.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT(course_school) DO UPDATE SET {upd}",
        [row[c] for c in cols],
    )
    meta.commit()


def write_decks(meta: sqlite3.Connection, cs: str, results: list[dict]) -> None:
    for r in results:
        d = r["deck"]
        meta.execute(
            "INSERT INTO decks VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(course_school, slide_url) DO UPDATE SET "
            "sequence_no=excluded.sequence_no, lecture_number=excluded.lecture_number, "
            "seq_label=excluded.seq_label, file_type=excluded.file_type, "
            "saved_path=excluded.saved_path, sha256=excluded.sha256, "
            "status=excluded.status, error=excluded.error",
            (cs, r["sequence_no"], r["lecture_number"], r["seq_label"], d["url"],
             d["file_type"], r["saved_path"], r["sha256"], r["status"], r["error"]),
        )
    meta.commit()


def upsert_progress(sdb: sqlite3.Connection, row: dict) -> None:
    cols = [
        "course_school", "course_code", "school", "instructor", "num_ratings",
        "resolved_link", "link_source", "search_query", "status", "num_decks",
        "seq_kind", "seq_expected", "complete_method", "complete_reason",
        "candidate_decks_json", "sequence_json", "folder", "updated_at",
    ]
    row = {**{c: None for c in cols}, **row, "updated_at": _now()}
    ph = ",".join("?" for _ in cols)
    upd = ",".join(f"{c}=excluded.{c}" for c in cols if c != "course_school")
    sdb.execute(
        f"INSERT INTO progress ({','.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT(course_school) DO UPDATE SET {upd}",
        [row[c] for c in cols],
    )
    sdb.commit()


def record_search(sdb: sqlite3.Connection, cs: str, code: str, school: str,
                  query: str, provider: str, urls: list[str]) -> None:
    sdb.execute(
        "INSERT INTO search_results VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(course_school) DO UPDATE SET query=excluded.query, "
        "provider=excluded.provider, results_json=excluded.results_json, "
        "searched_at=excluded.searched_at",
        (cs, code, school, query, provider, json.dumps(urls), _now()),
    )
    sdb.commit()


# --------------------------------------------------------------------------- #
# Source data -- RateMyProfessors course_summary aggregated to unique courses
# --------------------------------------------------------------------------- #
def norm_code(code: str) -> str:
    """Normalise an RMP free-text course code: upper-case, strip whitespace."""
    return re.sub(r"\s+", "", (code or "").upper())


def select_courses(min_ratings: int) -> list[dict]:
    """Every (professor, course) pair on RateMyProfessors with >= min ratings.

    RMP's ``course_summary`` is one row per (professor, course) -- i.e. the list
    of professors and the courses they teach. Every row whose rating count meets
    ``min_ratings`` becomes its OWN search target (keyed by
    ``course_school`` = "<CODE> - <School> - <Instructor>") so the instructor's
    name can be used in the slide search. The same course taught by two
    professors yields two rows on purpose (their offerings/slides differ).
    """
    con = sqlite3.connect(f"file:{RMP_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT legacy_id, prof_name, school, course, num_ratings, avg_quality, "
        "avg_difficulty FROM course_summary "
        "WHERE course IS NOT NULL AND school IS NOT NULL "
        "AND prof_name IS NOT NULL").fetchall()
    con.close()

    courses = []
    for r in rows:
        code = norm_code(r["course"])
        n = r["num_ratings"] or 0
        if not code or n < min_ratings:
            continue
        prof = r["prof_name"].strip()
        if not prof:
            continue
        courses.append({
            "course_school": f"{code} - {r['school']} - {prof}",
            "course_code": code,
            "school": r["school"],
            "instructor": prof,
            "legacy_id": r["legacy_id"],
            "num_ratings": n,
            "avg_quality": round(r["avg_quality"], 2) if r["avg_quality"] is not None else None,
            "avg_difficulty": round(r["avg_difficulty"], 2) if r["avg_difficulty"] is not None else None,
        })
    courses.sort(key=lambda c: (-c["num_ratings"], c["course_school"]))
    return courses


def index_courses(courses: list[dict]) -> dict[str, dict]:
    return {c["course_school"]: c for c in courses}


# --------------------------------------------------------------------------- #
# Link resolution (web search only -- Tavily -> SerpAPI -> keyless DuckDuckGo)
# --------------------------------------------------------------------------- #
def resolve_link(sdb: sqlite3.Connection, course: dict, args,
                 session) -> tuple[str | None, str, str | None]:
    cs = course["course_school"]
    # A subagent-provided link (via --ingest-search) wins and needs no network.
    row = sdb.execute("SELECT url FROM resolved_links WHERE course_school=?", (cs,)).fetchone()
    if row and row[0]:
        return row[0], "subagent-search", None
    if args.no_search:
        return None, "no-search", None

    query = (f"{course['course_code']} {course['school']} "
             f"{course['instructor']} course slides").strip()
    urls, provider = base.web_search(session, query, args.search_results,
                                     allow_keyless=not args.no_keyless)
    record_search(sdb, cs, course["course_code"], course["school"],
                  query, provider, urls)
    best = base._rank_candidates(urls, course["course_code"], course["school"])
    if best:
        return reduce_url(best), "websearch", query
    return None, "search-no-candidate", query


# --------------------------------------------------------------------------- #
# Per-course processing
# --------------------------------------------------------------------------- #
SKIP_ON_RESUME = {"complete", "incomplete", "ambiguous", "download_incomplete"}


def process_course(meta: sqlite3.Connection, sdb: sqlite3.Connection, course: dict,
                   args, session) -> str:
    cs = course["course_school"]
    prev = sdb.execute("SELECT status FROM progress WHERE course_school=?", (cs,)).fetchone()
    if prev and prev[0] in SKIP_ON_RESUME and not args.refresh:
        return "skip"

    prog = {"course_school": cs, "course_code": course["course_code"],
            "school": course["school"], "instructor": course["instructor"],
            "num_ratings": course["num_ratings"]}

    url, source, query = resolve_link(sdb, course, args, session)
    if not url:
        upsert_progress(sdb, {**prog, "link_source": source, "search_query": query,
                              "status": "no_link"})
        return "no_link"

    slug = sanitize(cs)
    folder_dir = Path(args.out) / slug
    decks = base.gather_decks(url, args.timeout)
    prog = {**prog, "resolved_link": url, "link_source": source, "search_query": query,
            "folder": slug, "num_decks": len(decks)}

    if not decks:
        upsert_progress(sdb, {**prog, "status": "no_decks"})
        return "no_decks"

    res = comp.assess([comp.Deck(d["name"], d["text"], d["url"]) for d in decks],
                      min_decks=args.min_decks)
    if res.complete is None and args.llm:
        j = comp.llm_judge(cs, res.per_deck, base_url=args.llm_base, model=args.llm_model)
        if j is not None:
            res = j
    sf = base._seq_fields(res)

    if res.complete is True:
        dl, sk, fail, results = base.download_course(folder_dir, decks, res, args.timeout)
        saved = [r["deck"] for r in results if r["status"] in ("saved", "skipped")]
        # Verify the slides actually landed on disk, IN ORDER (method-aware, same
        # rule as rated4ccr): a code-numbered sequence is re-assessed; an
        # LLM/subagent judgment covers the whole named set, so require all land.
        if res.method == "code":
            ver = comp.assess([comp.Deck(d["name"], d["text"], d["url"]) for d in saved],
                              min_decks=args.min_decks)
            ok, disk_reason = ver.complete is True, ver.reason
        else:
            ok = fail == 0 and len(saved) == len(decks)
            disk_reason = f"{res.method} set, all {len(saved)}/{len(decks)} landed" \
                if ok else f"{res.method} set incomplete on disk (fail={fail})"
        if (dl + sk) > 0 and ok:
            upsert_course(meta, {
                "course_school": cs, "course_code": course["course_code"],
                "school": course["school"], "num_ratings": course["num_ratings"],
                "instructor": course["instructor"], "legacy_id": course["legacy_id"],
                "avg_quality": course["avg_quality"], "avg_difficulty": course["avg_difficulty"],
                "resolved_link": url, "link_source": source, "folder": slug,
                "folder_path": str(folder_dir), "num_decks": len(decks),
                "seq_kind": sf["seq_kind"], "seq_min": sf["seq_min"], "seq_max": sf["seq_max"],
                "seq_expected": sf["seq_expected"], "complete_method": sf["complete_method"],
                "complete_reason": sf["complete_reason"], "sequence_json": sf["sequence_json"]})
            write_decks(meta, cs, [r for r in results if r["status"] in ("saved", "skipped")])
            upsert_progress(sdb, {**prog, "status": "complete", **base._prog_seq(sf)})
            return f"complete N={res.expected_n} (dl={dl} skip={sk} fail={fail})"
        # slides didn't fully land / not in order on disk -> discard the partial folder
        shutil.rmtree(folder_dir, ignore_errors=True)
        upsert_progress(sdb, {**prog, "status": "download_incomplete", **base._prog_seq(sf),
                              "complete_reason": f"{res.reason}; on disk: {disk_reason} "
                              f"(dl={dl} skip={sk} fail={fail})"})
        return f"download_incomplete (saved {dl + sk}/{len(decks)}, fail={fail})"

    if res.complete is False:
        upsert_progress(sdb, {**prog, "status": "incomplete", **base._prog_seq(sf)})
        return f"incomplete ({res.reason})"

    upsert_progress(sdb, {**prog, "status": "ambiguous", **base._prog_seq(sf),
                          "candidate_decks_json": json.dumps(decks, ensure_ascii=False)})
    return f"ambiguous ({res.reason})"


# --------------------------------------------------------------------------- #
# Subagent SEARCH emit / ingest (resolve links with a subagent, no web APIs)
# --------------------------------------------------------------------------- #
def _settled_or_linked(sdb: sqlite3.Connection, cs: str) -> bool:
    """True if the course already has a subagent link or a settled outcome."""
    if sdb.execute("SELECT 1 FROM resolved_links WHERE course_school=?", (cs,)).fetchone():
        return True
    row = sdb.execute("SELECT status FROM progress WHERE course_school=?", (cs,)).fetchone()
    return bool(row and row[0] in SKIP_ON_RESUME)


def cmd_emit_search(sdb: sqlite3.Connection, courses: list[dict], limit: int,
                    offset: int) -> None:
    """Emit the courses that still need a slide link, for a search subagent.

    Output is a JSON list of {course_school, course_code, school, instructor,
    num_ratings}. A subagent finds the best lecture-slides page/deck URL for each
    (using code + university + instructor) and returns a TSV
    (course_school<TAB>url<TAB>reason) to feed back via --ingest-search.
    """
    pending = [c for c in courses if not _settled_or_linked(sdb, c["course_school"])]
    window = pending[offset: offset + limit] if limit else pending[offset:]
    batch = [{"course_school": c["course_school"], "course_code": c["course_code"],
              "school": c["school"], "instructor": c["instructor"],
              "num_ratings": c["num_ratings"]} for c in window]
    print(json.dumps(batch, ensure_ascii=False, indent=2))


def cmd_ingest_search(sdb: sqlite3.Connection, path: str) -> None:
    """Store subagent-found links (TSV: course_school<TAB>url[<TAB>reason])."""
    added = skipped = 0
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.rstrip("\n")
        if not ln.strip():
            continue
        parts = ln.split("\t")
        cs = parts[0].strip()
        url = parts[1].strip() if len(parts) > 1 else ""
        reason = parts[2].strip() if len(parts) > 2 else ""
        if cs.lower() in ("course_school", "course_college") or not url:
            skipped += 1
            continue
        if not url.lower().startswith("http"):
            print(f"  ? not a url, skipped: {cs} -> {url[:60]}")
            skipped += 1
            continue
        sdb.execute(
            "INSERT INTO resolved_links VALUES (?,?,?,?,?) "
            "ON CONFLICT(course_school) DO UPDATE SET url=excluded.url, "
            "reason=excluded.reason, source=excluded.source, added_at=excluded.added_at",
            (cs, url, reason, "subagent-search", _now()))
        added += 1
    sdb.commit()
    print(f"ingested {added} subagent link(s); skipped {skipped}")


# --------------------------------------------------------------------------- #
# Subagent COMPLETENESS emit / ingest (uses the progress table in the search DB)
# --------------------------------------------------------------------------- #
def cmd_emit(sdb: sqlite3.Connection, limit: int, offset: int) -> None:
    rows = sdb.execute(
        "SELECT course_school, course_code, resolved_link, sequence_json "
        "FROM progress WHERE status='ambiguous' "
        "ORDER BY num_ratings DESC, course_school LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    batch = []
    for cs, code, link, seqj in rows:
        per_deck = json.loads(seqj) if seqj else []
        batch.append({"course_college": cs, "course_code": code, "resolved_link": link,
                      "decks": [{"name": r.get("name"), "kind": r.get("kind"),
                                 "number": r.get("number")} for r in per_deck]})
    print(comp.emit_batch(batch))


def cmd_ingest(meta: sqlite3.Connection, sdb: sqlite3.Connection, path: str, args,
               idx: dict[str, dict]) -> None:
    verdicts = comp.parse_ingest_tsv(Path(path).read_text(encoding="utf-8"))
    applied = downloaded = 0
    for cs, v in verdicts.items():
        row = sdb.execute(
            "SELECT course_code, school, num_ratings, resolved_link, link_source, "
            "candidate_decks_json, sequence_json, folder FROM progress WHERE course_school=?",
            (cs,)).fetchone()
        if not row:
            print(f"  ? unknown course: {cs}")
            continue
        code, school, nr, link, source, cand, seqj, slug = row
        decks = json.loads(cand) if cand else []
        per_deck = json.loads(seqj) if seqj else []
        applied += 1
        if v["complete"] and decks:
            res = comp.CompletenessResult(
                True, None, list(range(1, (v["n"] or len(decks)) + 1)),
                v["n"], "subagent", v["reason"] or "subagent-approved", per_deck)
            folder_dir = Path(args.out) / (slug or sanitize(cs))
            dl, sk, fail, results = base.download_course(folder_dir, decks, res, args.timeout)
            if (dl + sk) > 0:
                extra = idx.get(cs, {})
                upsert_course(meta, {
                    "course_school": cs, "course_code": code, "school": school,
                    "num_ratings": nr, "instructor": extra.get("instructor"),
                    "legacy_id": extra.get("legacy_id"),
                    "avg_quality": extra.get("avg_quality"),
                    "avg_difficulty": extra.get("avg_difficulty"),
                    "resolved_link": link, "link_source": source,
                    "folder": slug or sanitize(cs), "folder_path": str(folder_dir),
                    "num_decks": len(decks), "seq_expected": v["n"],
                    "complete_method": "subagent", "complete_reason": res.reason,
                    "sequence_json": json.dumps(per_deck, ensure_ascii=False)})
                write_decks(meta, cs, [r for r in results if r["status"] in ("saved", "skipped")])
                sdb.execute("UPDATE progress SET status='complete', complete_method='subagent', "
                            "complete_reason=?, seq_expected=?, updated_at=? WHERE course_school=?",
                            (res.reason, v["n"], _now(), cs))
                downloaded += 1
                print(f"  + {cs}: complete N={v['n']} (dl={dl} skip={sk} fail={fail})")
            else:
                shutil.rmtree(folder_dir, ignore_errors=True)
                sdb.execute("UPDATE progress SET status='download_incomplete', updated_at=? "
                            "WHERE course_school=?", (_now(), cs))
                print(f"  ! {cs}: download failed (fail={fail})")
        else:
            sdb.execute("UPDATE progress SET status='incomplete', complete_method='subagent', "
                        "complete_reason=?, updated_at=? WHERE course_school=?",
                        (v["reason"] or "subagent-rejected", _now(), cs))
            print(f"  - {cs}: incomplete")
    sdb.commit()
    print(f"\ningested {applied} verdict(s); downloaded {downloaded} course(s)")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def build_args():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "rated4rmp"))
    ap.add_argument("--min-ratings", type=int, default=4)
    ap.add_argument("--min-decks", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=45000, help="per-fetch ms")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--search-results", type=int, default=10)
    ap.add_argument("--no-search", action="store_true",
                    help="never web-search (nothing resolves; mostly for testing)")
    ap.add_argument("--no-keyless", action="store_true",
                    help="disable the keyless DuckDuckGo fallback (keyed APIs only)")
    ap.add_argument("--llm", action="store_true",
                    help="auto-judge ambiguous completeness with an OpenAI-compatible LLM")
    ap.add_argument("--llm-base", default=os.environ.get("SLIDEFETCH_LLM_BASE", base.VLLM_BASE))
    ap.add_argument("--llm-model", default=os.environ.get("SLIDEFETCH_LLM_MODEL"))
    ap.add_argument("--refresh", action="store_true", help="re-process finished courses")
    ap.add_argument("--dry", action="store_true", help="resolve + print only")
    ap.add_argument("--emit-search", action="store_true",
                    help="print JSON of courses needing a link, for a search subagent")
    ap.add_argument("--ingest-search", metavar="TSV",
                    help="store subagent-found links (course_school<TAB>url[<TAB>reason])")
    ap.add_argument("--emit", action="store_true", help="print ambiguous batch JSON")
    ap.add_argument("--ingest", metavar="TSV", help="apply subagent verdicts, download")
    ap.add_argument("--offset", type=int, default=0)
    return ap.parse_args()


def main() -> int:
    args = build_args()
    out_dir = Path(args.out)
    meta = open_metadata_db(out_dir)
    sdb = open_search_db(out_dir)

    if args.emit:
        cmd_emit(sdb, args.limit or 25, args.offset)
        return 0
    if args.ingest:
        cmd_ingest(meta, sdb, args.ingest, args, index_courses(select_courses(1)))
        return 0

    courses = select_courses(args.min_ratings)

    if args.emit_search:
        cmd_emit_search(sdb, courses, args.limit, args.offset)
        return 0
    if args.ingest_search:
        cmd_ingest_search(sdb, args.ingest_search)
        return 0

    have_key = bool(os.environ.get("TAVILY_API_KEY")
                    or os.environ.get("SERPAPI_API_KEY")
                    or os.environ.get("SERPAPI_KEY"))
    search_mode = "off" if args.no_search else (
        "keyed+keyless" if have_key and not args.no_keyless else
        "keyed" if have_key else
        "keyless" if not args.no_keyless else "off")
    print(f"{len(courses)} RMP course(s) num_ratings>={args.min_ratings}; "
          f"web-search={search_mode}; llm={'on' if args.llm else 'off'}", flush=True)

    todo = courses[: args.limit] if args.limit else courses
    if args.dry:
        session = requests.Session()
        for i, c in enumerate(todo, 1):
            url, source, query = resolve_link(sdb, c, args, session)
            print(f"  {i:>4} [{source:18}] {c['course_school'][:44]:44} "
                  f"n={c['num_ratings']:<4} {(url or '-')[:66]}")
        return 0

    session = requests.Session()
    tally: dict[str, int] = {}
    for i, c in enumerate(todo, 1):
        result = process_course(meta, sdb, c, args, session)
        tally[result.split()[0]] = tally.get(result.split()[0], 0) + 1
        print(f"[{i}/{len(todo)}] {c['course_school'][:42]:42} -> {result}", flush=True)
        if result != "skip":
            time.sleep(0.3)
    print("\nsummary:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())), flush=True)
    print(f"metadata (complete only): {out_dir/'rated4rmp.db'}", flush=True)
    print(f"searched links + progress: {out_dir/'rated4rmp_search.db'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
