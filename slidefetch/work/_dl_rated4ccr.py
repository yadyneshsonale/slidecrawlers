#!/usr/bin/env python3
"""Download slides for every CCR course with >=4 ratings that has a COMPLETE
1..N lecture-slide sequence.

Pipeline (resumable, keyed by ``course_college``):

  1. select   -- ccr_courses.db courses with num_ratings >= 4
  2. resolve  -- confirmed link (ccr_verified.db) else web search
                 ("<code> <college> course slides"): Tavily -> SerpAPI (keyed) ->
                 DuckDuckGo scrape (KEYLESS fallback). Ranked by
                 ccr_slide_kb.classify_link, reduced by slidefetch.urls.reduce_url.
  3. gather   -- slidefetch _gather + link fixes (github raw/repo, google slides)
  4. assess   -- _completeness.assess (gap-free 1..N); ambiguous -> vLLM/LLM (--llm)
  5. download -- COMPLETE courses only, and kept only if the slides really land on
                 disk in order; partial folders are removed.

Storage (see --out, default rated4ccr/):
  * rated4ccr.db         -- METADATA, only complete-and-in-order courses (+ their decks)
  * rated4ccr_search.db  -- searched links (all results) + per-course progress (resume)
  * rated4ccr/<slug>/    -- the downloaded decks for each complete course

Run from the slidefetch root with the venv::

    PYTHONPATH=. .venv/bin/python work/_dl_rated4ccr.py --verified-only --llm
    TAVILY_API_KEY=... PYTHONPATH=. .venv/bin/python work/_dl_rated4ccr.py --llm
    PYTHONPATH=. .venv/bin/python work/_dl_rated4ccr.py --emit > work/amb.json
    PYTHONPATH=. .venv/bin/python work/_dl_rated4ccr.py --ingest work/verdicts.tsv
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

import _completeness as comp
import _linkfix as lf
import ccr_slide_search as css
from ccr_slide_kb import classify_link
from slidefetch.cli import _gather
from slidefetch.download import download, download_html_slides, sanitize
from slidefetch.urls import reduce_url

ROOT = Path(__file__).resolve().parent.parent          # slidefetch/
COURSES_DB = ROOT / "ccr_courses.db"
VERIFIED_DB = ROOT / "ccr_verified.db"
CACHE_DB = ROOT / "ccr_slide_links.db"
LABELS_DB = ROOT / "ccr_labels.db"
DECK_EXTS = (".pdf", ".ppt", ".pptx")
VLLM_BASE = "http://127.0.0.1:8000/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Databases: metadata (complete only) + searches/progress (separate)
# --------------------------------------------------------------------------- #
def open_metadata_db(out_dir: Path) -> sqlite3.Connection:
    """rated4ccr.db -- ONLY courses whose slides are present and in order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(out_dir / "rated4ccr.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS courses (
            course_college  TEXT PRIMARY KEY,
            course_code     TEXT,
            college_name    TEXT,
            num_ratings     INTEGER,
            star_rating     REAL,
            ccr_link        TEXT,
            resolved_link   TEXT,
            link_source     TEXT,   -- verified | cache | websearch
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
            course_college  TEXT,
            sequence_no     INTEGER,
            lecture_number  INTEGER,
            seq_label       TEXT,
            slide_url       TEXT,
            file_type       TEXT,
            saved_path      TEXT,
            sha256          TEXT,
            status          TEXT,   -- saved | skipped
            error           TEXT,
            UNIQUE(course_college, slide_url)
        );
        """
    )
    con.commit()
    return con


def open_search_db(out_dir: Path) -> sqlite3.Connection:
    """rated4ccr_search.db -- searched links (all) + progress for every course."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(out_dir / "rated4ccr_search.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_results (
            course_college  TEXT PRIMARY KEY,
            course_code     TEXT,
            college_name    TEXT,
            query           TEXT,
            provider        TEXT,   -- tavily | serpapi | ddg | none
            results_json    TEXT,   -- every URL returned, for later use
            searched_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS progress (
            course_college  TEXT PRIMARY KEY,
            course_code     TEXT,
            college_name    TEXT,
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
        "course_college", "course_code", "college_name", "num_ratings",
        "star_rating", "ccr_link", "resolved_link", "link_source", "folder",
        "folder_path", "num_decks", "seq_kind", "seq_min", "seq_max",
        "seq_expected", "complete_method", "complete_reason", "sequence_json",
        "updated_at",
    ]
    row = {**{c: None for c in cols}, **row, "updated_at": _now()}
    ph = ",".join("?" for _ in cols)
    upd = ",".join(f"{c}=excluded.{c}" for c in cols if c != "course_college")
    meta.execute(
        f"INSERT INTO courses ({','.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT(course_college) DO UPDATE SET {upd}",
        [row[c] for c in cols],
    )
    meta.commit()


def write_decks(meta: sqlite3.Connection, cc: str, results: list[dict]) -> None:
    for r in results:
        d = r["deck"]
        meta.execute(
            "INSERT INTO decks VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(course_college, slide_url) DO UPDATE SET "
            "sequence_no=excluded.sequence_no, lecture_number=excluded.lecture_number, "
            "seq_label=excluded.seq_label, file_type=excluded.file_type, "
            "saved_path=excluded.saved_path, sha256=excluded.sha256, "
            "status=excluded.status, error=excluded.error",
            (cc, r["sequence_no"], r["lecture_number"], r["seq_label"], d["url"],
             d["file_type"], r["saved_path"], r["sha256"], r["status"], r["error"]),
        )
    meta.commit()


def upsert_progress(sdb: sqlite3.Connection, row: dict) -> None:
    cols = [
        "course_college", "course_code", "college_name", "num_ratings",
        "resolved_link", "link_source", "search_query", "status", "num_decks",
        "seq_kind", "seq_expected", "complete_method", "complete_reason",
        "candidate_decks_json", "sequence_json", "folder", "updated_at",
    ]
    row = {**{c: None for c in cols}, **row, "updated_at": _now()}
    ph = ",".join("?" for _ in cols)
    upd = ",".join(f"{c}=excluded.{c}" for c in cols if c != "course_college")
    sdb.execute(
        f"INSERT INTO progress ({','.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT(course_college) DO UPDATE SET {upd}",
        [row[c] for c in cols],
    )
    sdb.commit()


def record_search(sdb: sqlite3.Connection, cc: str, code: str, college: str,
                  query: str, provider: str, urls: list[str]) -> None:
    sdb.execute(
        "INSERT INTO search_results VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(course_college) DO UPDATE SET query=excluded.query, "
        "provider=excluded.provider, results_json=excluded.results_json, "
        "searched_at=excluded.searched_at",
        (cc, code, college, query, provider, json.dumps(urls), _now()),
    )
    sdb.commit()


# --------------------------------------------------------------------------- #
# Source data
# --------------------------------------------------------------------------- #
def select_courses(min_ratings: int, order: str = "desc") -> list[dict]:
    direction = "ASC" if order == "asc" else "DESC"
    con = sqlite3.connect(f"file:{COURSES_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT course_college, course_code, college_name, num_ratings, "
        "star_rating, ccr_link FROM courses "
        "WHERE num_ratings >= ? AND course_college IS NOT NULL "
        f"ORDER BY num_ratings {direction}, course_college",
        (min_ratings,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def load_verified() -> dict[str, str]:
    if not VERIFIED_DB.exists():
        return {}
    con = sqlite3.connect(f"file:{VERIFIED_DB}?mode=ro", uri=True)
    out = {cc: url for cc, url in con.execute(
        "SELECT course_college, correct_url FROM verified_labels "
        "WHERE verdict='confirmed' AND correct_url LIKE 'http%'")}
    con.close()
    return out


def load_cache() -> dict[str, list[str]]:
    """course_college -> pooled candidate links from every cached/labeled source.

    Sources: ccr_slide_links.db (link_1..10, prior web-search output) plus
    ccr_labels.db human `labels` (gold, prepended so it wins ties) and the
    auto-labeler's `auto_labels` (link_1/link_2). The pool is ranked later via
    classify_link, so low-quality picks are dropped; it covers far more courses
    than the confirmed set and avoids a live search.
    """
    out: dict[str, list[str]] = {}
    if CACHE_DB.exists():
        con = sqlite3.connect(f"file:{CACHE_DB}?mode=ro", uri=True)
        link_cols = ", ".join(f"link_{i}" for i in range(1, 11))
        for row in con.execute(f"SELECT course_college, {link_cols} FROM slide_links"):
            urls = [u for u in row[1:] if u and u.startswith("http")]
            if urls:
                out[row[0]] = urls
        con.close()
    if LABELS_DB.exists():
        con = sqlite3.connect(f"file:{LABELS_DB}?mode=ro", uri=True)
        for cc, url in con.execute(
                "SELECT course_college, chosen_url FROM labels WHERE chosen_url LIKE 'http%'"):
            lst = out.setdefault(cc, [])
            if url not in lst:
                lst.insert(0, url)          # human gold ranks first on ties
        for cc, l1, l2 in con.execute(
                "SELECT course_college, link_1, link_2 FROM auto_labels"):
            lst = out.setdefault(cc, [])
            for u in (l1, l2):
                if u and u.startswith("http") and u not in lst:
                    lst.append(u)
        con.close()
    return out


# --------------------------------------------------------------------------- #
# Link resolution (Tavily -> SerpAPI -> keyless DuckDuckGo)
# --------------------------------------------------------------------------- #
def _rank_candidates(urls: list[str], code: str, college: str) -> str | None:
    ranked = []
    for u in urls:
        v = classify_link(u, code, college)
        if v.verdict == "reject":
            continue
        pref = 0 if v.verdict == "accept" else 1
        ranked.append((pref, -v.confidence, u))
    ranked.sort()
    return ranked[0][2] if ranked else None


def web_search(session: requests.Session, query: str, max_results: int,
               state: dict, allow_keyless: bool = True) -> tuple[list[str], str]:
    """Keyed APIs first, then a keyless DuckDuckGo scrape. Never raises.

    When a keyed API reports its quota is spent it is marked dead in *state* and
    skipped for the rest of the run, so callers can queue those courses for a
    subagent search instead of re-hitting an exhausted key.
    """
    for name, fn, deadkey in (("tavily", css.tavily_search, "tavily_dead"),
                              ("serpapi", css.serpapi_search, "serpapi_dead")):
        if state.get(deadkey):
            continue
        try:
            urls = fn(session, query, max_results=max_results)
        except css.QuotaExhausted:
            state[deadkey] = True
            urls = []
        except Exception:  # noqa: BLE001
            urls = []
        if urls:
            return urls, name
    if allow_keyless:
        try:
            urls = css.search(session, query)
        except Exception:  # noqa: BLE001
            urls = []
        if urls:
            return urls[:max_results], "ddg"
    return [], "none"


def resolve_link(sdb: sqlite3.Connection, course: dict, args, session,
                 state: dict) -> tuple[str | None, str, str | None]:
    cc = course["course_college"]
    if cc in state["verified"]:
        return state["verified"][cc], "verified", None
    if cc in state["cache"]:
        best = _rank_candidates(state["cache"][cc], course["course_code"],
                                course["college_name"])
        if best:
            return reduce_url(best), "cache", None
    if args.no_search:
        return None, "no-confirmed-link", None

    query = f"{course['course_code']} {course['college_name']} course slides".strip()
    urls, provider = web_search(session, query, args.search_results, state,
                                allow_keyless=not args.no_keyless)
    record_search(sdb, cc, course["course_code"], course["college_name"],
                  query, provider, urls)
    best = _rank_candidates(urls, course["course_code"], course["college_name"])
    if best:
        return reduce_url(best), "websearch", query
    # No usable link. Once Tavily's quota is spent, queue the course for a
    # subagent web-search (--emit-search / --ingest-search) instead of giving up.
    if state.get("tavily_dead") and not args.no_subagent_search:
        return None, "needs_search", query
    return None, "search-no-candidate", query


# --------------------------------------------------------------------------- #
# Gather decks (with link fixes)
# --------------------------------------------------------------------------- #
def _is_deck_file(url: str) -> bool:
    return urlparse(url).path.lower().endswith(DECK_EXTS)


def _gather_page(url: str, timeout_ms: int) -> list[dict]:
    try:
        links = _gather(url, timeout_ms, False, follow=True, max_pages=6)
    except Exception as err:  # noqa: BLE001
        print(f"    gather error: {type(err).__name__}: {err}", flush=True)
        return []
    out, seen = [], set()
    for sl in links:
        u = lf.raw_github(sl.url)
        if u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "name": sl.name or "", "text": sl.text or "",
                    "file_type": sl.file_type})
    return out


def gather_decks(url: str, timeout_ms: int) -> list[dict]:
    """Return candidate deck records {url,name,text,file_type} for a course link."""
    g = lf.gslides_pdf(url)
    if g:
        return [{"url": g.url, "name": g.name, "text": "", "file_type": g.file_type}]

    if lf.is_github(url):
        fixed = lf.github_decks(url)
        if fixed:
            return [{"url": f.url, "name": f.name, "text": "", "file_type": f.file_type}
                    for f in fixed]

    if _is_deck_file(url):
        du = lf.raw_github(url)
        single = {"url": du, "name": urlparse(du).path.rsplit("/", 1)[-1],
                  "text": "", "file_type": lf._ext_of(urlparse(du).path)}
        reduced = reduce_url(url)
        decks = _gather_page(reduced, timeout_ms) if reduced != url else []
        if decks:
            if not any(d["url"] in (du, url) for d in decks):
                decks.append(single)
            return decks
        return [single]

    return _gather_page(url, timeout_ms)


# --------------------------------------------------------------------------- #
# Download (files only; DB writes happen after completeness is confirmed)
# --------------------------------------------------------------------------- #
def _num_map(res: comp.CompletenessResult) -> dict[str, tuple[str | None, int | None]]:
    return {r["url"]: (r.get("kind"), r.get("number")) for r in res.per_deck}


def download_course(folder_dir: Path, decks: list[dict], res: comp.CompletenessResult,
                    timeout_ms: int) -> tuple[int, int, int, list[dict]]:
    """Download decks (ordered by lecture number). Returns (dl, sk, fail, results)."""
    nmap = _num_map(res)
    ordered = sorted(
        decks,
        key=lambda d: (nmap.get(d["url"], (None, None))[1] is None,
                       nmap.get(d["url"], (None, 10**6))[1] or 10**6, d["name"]),
    )
    dl = sk = fail = 0
    results: list[dict] = []
    for seq, d in enumerate(ordered, 1):
        kind, num = nmap.get(d["url"], (None, None))
        status = saved_path = sha = error = None
        try:
            if d["file_type"] in ("html", "htm"):
                r = download_html_slides(d["url"], folder_dir, seq, d["name"] or None,
                                         timeout_ms=timeout_ms)
            else:
                r = download(d["url"], folder_dir, d["file_type"], num, seq,
                             d["name"] or None)
        except Exception as err:  # noqa: BLE001
            r, status, error = None, "failed", str(err)[:200]
        if r is not None:
            if r.skipped:
                status, sk = "skipped", sk + 1
            elif r.ok:
                status, dl = "saved", dl + 1
            else:
                status, fail = "failed", fail + 1
            saved_path, sha, error = r.path, r.sha256, r.error
        results.append({"deck": d, "sequence_no": seq, "lecture_number": num,
                        "seq_label": kind, "status": status, "saved_path": saved_path,
                        "sha256": sha, "error": error})
    return dl, sk, fail, results


def _seq_fields(res: comp.CompletenessResult) -> dict:
    nums = res.numbers or []
    return {
        "seq_kind": res.kind, "seq_expected": res.expected_n,
        "complete_method": res.method, "complete_reason": res.reason,
        "sequence_json": json.dumps(res.per_deck, ensure_ascii=False),
        "seq_min": min(nums) if nums else None,
        "seq_max": max(nums) if nums else None,
    }


def _prog_seq(sf: dict) -> dict:
    return {"seq_kind": sf["seq_kind"], "seq_expected": sf["seq_expected"],
            "complete_method": sf["complete_method"], "complete_reason": sf["complete_reason"],
            "sequence_json": sf["sequence_json"]}


# --------------------------------------------------------------------------- #
# Per-course processing
# --------------------------------------------------------------------------- #
# A plain resume skips courses with a settled outcome; no_link/no_decks/error stay
# retryable so a later run (keyless search now reachable, transient fetch cleared,
# or a key added) can finish them.
# A plain resume skips every course with a settled outcome; only transient errors
# retry. New courses still flow to needs_search on first processing. no_decks/no_link
# are terminal: re-running the same resolved link (or an exhausted key) can't change them.
# Use --refresh to force a re-attempt (e.g. after a key quota resets).
SKIP_ON_RESUME = {"complete", "incomplete", "ambiguous", "download_incomplete", "timeout",
                  "needs_search", "no_decks", "no_link"}


class _CourseTimeout(Exception):
    """Raised by the SIGALRM watchdog when one course exceeds its time budget."""


_alarm_escalations = 0


def _on_alarm(signum, frame):  # noqa: ARG001
    # Re-arm so that if the interrupted call's cleanup also blocks (Playwright's
    # __exit__ can hang closing a stuck browser), the alarm fires again instead
    # of stalling the run.
    globals()["_alarm_escalations"] += 1
    if globals()["_alarm_escalations"] < 4:
        signal.alarm(20)
    raise _CourseTimeout()


def process_course(meta: sqlite3.Connection, sdb: sqlite3.Connection, course: dict,
                   args, session, state: dict) -> str:
    """Resume-guard + per-course wall-clock watchdog around :func:`_run_course`.

    A single hanging URL (a stalled fetch/render that ignores its own timeout)
    can otherwise block the whole run; the SIGALRM alarm guarantees progress.
    """
    cc = course["course_college"]
    prev = sdb.execute("SELECT status FROM progress WHERE course_college=?", (cc,)).fetchone()
    retry = {s.strip() for s in (args.retry or "").split(",") if s.strip()}
    if prev and prev[0] in SKIP_ON_RESUME and prev[0] not in retry and not args.refresh:
        return "skip"

    slug = sanitize(cc)
    folder_dir = Path(args.out) / slug
    prog = {"course_college": cc, "course_code": course["course_code"],
            "college_name": course["college_name"], "num_ratings": course["num_ratings"]}
    globals()["_alarm_escalations"] = 0
    signal.alarm(max(30, args.course_timeout))
    try:
        return _run_course(meta, sdb, course, args, session, state, cc, slug, folder_dir, prog)
    except _CourseTimeout:
        shutil.rmtree(folder_dir, ignore_errors=True)
        upsert_progress(sdb, {**prog, "status": "timeout",
                              "complete_reason": f"exceeded {args.course_timeout}s wall-clock"})
        return "timeout"
    finally:
        signal.alarm(0)


def _run_course(meta: sqlite3.Connection, sdb: sqlite3.Connection, course: dict,
                args, session, state: dict, cc: str, slug: str, folder_dir: Path,
                prog: dict) -> str:
    url, source, query = resolve_link(sdb, course, args, session, state)
    if not url:
        status = "needs_search" if source == "needs_search" else "no_link"
        upsert_progress(sdb, {**prog, "link_source": source, "search_query": query,
                              "status": status})
        return status
    return _process_resolved(meta, sdb, course, args, url, source, query,
                             cc, slug, folder_dir, prog)


def _process_resolved(meta: sqlite3.Connection, sdb: sqlite3.Connection, course: dict,
                      args, url: str, source: str, query: str | None, cc: str,
                      slug: str, folder_dir: Path, prog: dict) -> str:
    decks = gather_decks(url, args.timeout)
    prog = {**prog, "resolved_link": url, "link_source": source, "search_query": query,
            "folder": slug, "num_decks": len(decks)}

    if not decks:
        upsert_progress(sdb, {**prog, "status": "no_decks"})
        return "no_decks"

    res = comp.assess([comp.Deck(d["name"], d["text"], d["url"]) for d in decks],
                      min_decks=args.min_decks)
    if res.complete is None and args.llm:
        j = comp.llm_judge(cc, res.per_deck, base_url=args.llm_base, model=args.llm_model)
        if j is not None:
            res = j
    sf = _seq_fields(res)

    if res.complete is True:
        dl, sk, fail, results = download_course(folder_dir, decks, res, args.timeout)
        saved = [r["deck"] for r in results if r["status"] in ("saved", "skipped")]
        # Verify the slides actually landed on disk, IN ORDER. A code-decided
        # numbered sequence is re-checked with the same rule; an LLM/subagent
        # judgment covers the whole named set, so require every deck to land.
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
                "course_college": cc, "course_code": course["course_code"],
                "college_name": course["college_name"], "num_ratings": course["num_ratings"],
                "star_rating": course["star_rating"], "ccr_link": course["ccr_link"],
                "resolved_link": url, "link_source": source, "folder": slug,
                "folder_path": str(folder_dir), "num_decks": len(decks),
                "seq_kind": sf["seq_kind"], "seq_min": sf["seq_min"], "seq_max": sf["seq_max"],
                "seq_expected": sf["seq_expected"], "complete_method": sf["complete_method"],
                "complete_reason": sf["complete_reason"], "sequence_json": sf["sequence_json"]})
            write_decks(meta, cc, [r for r in results if r["status"] in ("saved", "skipped")])
            upsert_progress(sdb, {**prog, "status": "complete", **_prog_seq(sf)})
            return f"complete N={res.expected_n} (dl={dl} skip={sk} fail={fail})"
        # slides didn't fully land / not in order on disk -> discard the partial folder
        shutil.rmtree(folder_dir, ignore_errors=True)
        upsert_progress(sdb, {**prog, "status": "download_incomplete", **_prog_seq(sf),
                              "complete_reason": f"{res.reason}; on disk: {disk_reason} "
                              f"(dl={dl} skip={sk} fail={fail})"})
        return f"download_incomplete (saved {dl + sk}/{len(decks)}, fail={fail})"

    if res.complete is False:
        upsert_progress(sdb, {**prog, "status": "incomplete", **_prog_seq(sf)})
        return f"incomplete ({res.reason})"

    upsert_progress(sdb, {**prog, "status": "ambiguous", **_prog_seq(sf),
                          "candidate_decks_json": json.dumps(decks, ensure_ascii=False)})
    return f"ambiguous ({res.reason})"


# --------------------------------------------------------------------------- #
# Subagent emit / ingest (uses the progress table in the search DB)
# --------------------------------------------------------------------------- #
def cmd_emit(sdb: sqlite3.Connection, limit: int, offset: int) -> None:
    rows = sdb.execute(
        "SELECT course_college, course_code, resolved_link, sequence_json "
        "FROM progress WHERE status='ambiguous' "
        "ORDER BY num_ratings DESC, course_college LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    batch = []
    for cc, code, link, seqj in rows:
        per_deck = json.loads(seqj) if seqj else []
        batch.append({"course_college": cc, "course_code": code, "resolved_link": link,
                      "decks": [{"name": r.get("name"), "kind": r.get("kind"),
                                 "number": r.get("number")} for r in per_deck]})
    print(comp.emit_batch(batch))


def cmd_ingest(meta: sqlite3.Connection, sdb: sqlite3.Connection, path: str, args) -> None:
    verdicts = comp.parse_ingest_tsv(Path(path).read_text(encoding="utf-8"))
    applied = downloaded = 0
    for cc, v in verdicts.items():
        row = sdb.execute(
            "SELECT course_code, college_name, num_ratings, resolved_link, link_source, "
            "candidate_decks_json, sequence_json, folder FROM progress WHERE course_college=?",
            (cc,)).fetchone()
        if not row:
            print(f"  ? unknown course: {cc}")
            continue
        code, college, nr, link, source, cand, seqj, slug = row
        decks = json.loads(cand) if cand else []
        per_deck = json.loads(seqj) if seqj else []
        applied += 1
        if v["complete"] and decks:
            res = comp.CompletenessResult(
                True, None, list(range(1, (v["n"] or len(decks)) + 1)),
                v["n"], "subagent", v["reason"] or "subagent-approved", per_deck)
            folder_dir = Path(args.out) / (slug or sanitize(cc))
            dl, sk, fail, results = download_course(folder_dir, decks, res, args.timeout)
            if (dl + sk) > 0:
                extra = select_one(cc)
                upsert_course(meta, {
                    "course_college": cc, "course_code": code, "college_name": college,
                    "num_ratings": nr, "star_rating": extra.get("star_rating"),
                    "ccr_link": extra.get("ccr_link"), "resolved_link": link,
                    "link_source": source, "folder": slug or sanitize(cc),
                    "folder_path": str(folder_dir), "num_decks": len(decks),
                    "seq_expected": v["n"], "complete_method": "subagent",
                    "complete_reason": res.reason,
                    "sequence_json": json.dumps(per_deck, ensure_ascii=False)})
                write_decks(meta, cc, [r for r in results if r["status"] in ("saved", "skipped")])
                sdb.execute("UPDATE progress SET status='complete', complete_method='subagent', "
                            "complete_reason=?, seq_expected=?, updated_at=? WHERE course_college=?",
                            (res.reason, v["n"], _now(), cc))
                downloaded += 1
                print(f"  + {cc}: complete N={v['n']} (dl={dl} skip={sk} fail={fail})")
            else:
                shutil.rmtree(folder_dir, ignore_errors=True)
                sdb.execute("UPDATE progress SET status='download_incomplete', updated_at=? "
                            "WHERE course_college=?", (_now(), cc))
                print(f"  ! {cc}: download failed (fail={fail})")
        else:
            sdb.execute("UPDATE progress SET status='incomplete', complete_method='subagent', "
                        "complete_reason=?, updated_at=? WHERE course_college=?",
                        (v["reason"] or "subagent-rejected", _now(), cc))
            print(f"  - {cc}: incomplete")
    sdb.commit()
    print(f"\ningested {applied} verdict(s); downloaded {downloaded} course(s)")


def select_one(cc: str) -> dict:
    con = sqlite3.connect(f"file:{COURSES_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT star_rating, ccr_link FROM courses WHERE course_college=?",
                    (cc,)).fetchone()
    con.close()
    return dict(r) if r else {}


def select_full_course(cc: str) -> dict | None:
    con = sqlite3.connect(f"file:{COURSES_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    r = con.execute(
        "SELECT course_college, course_code, college_name, num_ratings, star_rating, "
        "ccr_link FROM courses WHERE course_college=?", (cc,)).fetchone()
    con.close()
    return dict(r) if r else None


# --------------------------------------------------------------------------- #
# Subagent web-search loop (for courses queued 'needs_search' once Tavily is spent)
# --------------------------------------------------------------------------- #
def _parse_search_tsv(text: str) -> dict[str, str]:
    """Parse a subagent link TSV: ``course_college<TAB>url`` -> {cc: url}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lower().startswith("course_college\t"):
            continue
        parts = line.split("\t")
        cc = parts[0].strip()
        if cc:
            out[cc] = parts[1].strip() if len(parts) > 1 else ""
    return out


def cmd_emit_search(sdb: sqlite3.Connection, limit: int, offset: int) -> None:
    """Print the batch of courses awaiting a subagent web-search (JSON)."""
    rows = sdb.execute(
        "SELECT course_college, course_code, college_name, num_ratings, search_query "
        "FROM progress WHERE status='needs_search' "
        "ORDER BY num_ratings DESC, course_college LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    batch = [{"course_college": cc, "course_code": code, "college_name": coll,
              "num_ratings": nr, "query": q or f"{code} {coll} course slides"}
             for cc, code, coll, nr, q in rows]
    print(json.dumps(batch, indent=1, ensure_ascii=False))


def cmd_ingest_search(meta: sqlite3.Connection, sdb: sqlite3.Connection, path: str,
                      args) -> None:
    """Apply subagent-found links (TSV course_college<TAB>url): gather + download."""
    try:
        signal.signal(signal.SIGALRM, _on_alarm)
    except (ValueError, AttributeError):
        pass
    links = _parse_search_tsv(Path(path).read_text(encoding="utf-8"))
    complete = 0
    for cc, url in links.items():
        course = select_full_course(cc)
        if not course:
            print(f"  ? unknown course: {cc}")
            continue
        prog = {"course_college": cc, "course_code": course["course_code"],
                "college_name": course["college_name"], "num_ratings": course["num_ratings"]}
        if not url or url.lower() in ("-", "none", "null"):
            upsert_progress(sdb, {**prog, "link_source": "subagent-search",
                                  "status": "no_link"})
            print(f"  - {cc}: no link")
            continue
        slug = sanitize(cc)
        folder_dir = Path(args.out) / slug
        query = f"{course['course_code']} {course['college_name']} course slides".strip()
        globals()["_alarm_escalations"] = 0
        signal.alarm(max(30, args.course_timeout))
        try:
            result = _process_resolved(meta, sdb, course, args, reduce_url(url),
                                       "subagent-search", query, cc, slug, folder_dir, prog)
        except _CourseTimeout:
            shutil.rmtree(folder_dir, ignore_errors=True)
            upsert_progress(sdb, {**prog, "status": "timeout",
                                  "complete_reason": f"exceeded {args.course_timeout}s"})
            result = "timeout"
        finally:
            signal.alarm(0)
        complete += result.startswith("complete")
        print(f"  {cc} -> {result}", flush=True)
    print(f"\ningested {len(links)} subagent link(s); {complete} complete")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def build_args():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "rated4ccr"))
    ap.add_argument("--min-ratings", type=int, default=4)
    ap.add_argument("--order", choices=["desc", "asc", "yield"], default="desc",
                    help="processing order: desc/asc by num_ratings, or 'yield' = courses "
                         "whose best cached link is a confident course page first")
    ap.add_argument("--min-decks", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=45000, help="per-fetch ms")
    ap.add_argument("--course-timeout", type=int, default=600,
                    help="max wall-clock seconds per course before it is abandoned")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--search-results", type=int, default=10)
    ap.add_argument("--no-cache", action="store_true",
                    help="do NOT use ccr_slide_links.db cached search links (default: use them)")
    ap.add_argument("--retry", default="",
                    help="comma-separated statuses to re-process on resume, e.g. "
                         "needs_search,no_decks (normally those are skipped)")
    ap.add_argument("--no-search", action="store_true",
                    help="verified/cache links only; never web-search")
    ap.add_argument("--no-keyless", action="store_true",
                    help="disable the keyless DuckDuckGo fallback (keyed APIs only)")
    ap.add_argument("--verified-only", action="store_true",
                    help="restrict the run to courses that already have a confirmed link")
    ap.add_argument("--llm", action="store_true",
                    help="auto-judge ambiguous completeness with an OpenAI-compatible LLM")
    ap.add_argument("--llm-base", default=os.environ.get("SLIDEFETCH_LLM_BASE", VLLM_BASE))
    ap.add_argument("--llm-model", default=os.environ.get("SLIDEFETCH_LLM_MODEL"))
    ap.add_argument("--refresh", action="store_true", help="re-process finished courses")
    ap.add_argument("--dry", action="store_true", help="resolve + print only")
    ap.add_argument("--emit", action="store_true", help="print ambiguous batch JSON")
    ap.add_argument("--ingest", metavar="TSV", help="apply subagent verdicts, download")
    ap.add_argument("--no-subagent-search", action="store_true",
                    help="when Tavily quota is exhausted, mark courses no_link instead "
                         "of queuing them (needs_search) for a subagent web-search")
    ap.add_argument("--emit-search", action="store_true",
                    help="print courses queued for a subagent web-search (status=needs_search)")
    ap.add_argument("--ingest-search", metavar="TSV",
                    help="apply subagent-found links (course_college<TAB>url), gather+download")
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
        cmd_ingest(meta, sdb, args.ingest, args)
        return 0
    if args.emit_search:
        cmd_emit_search(sdb, args.limit or 25, args.offset)
        return 0
    if args.ingest_search:
        cmd_ingest_search(meta, sdb, args.ingest_search, args)
        return 0

    courses = select_courses(args.min_ratings, args.order)
    state = {
        "verified": load_verified(),
        "cache": {} if args.no_cache else load_cache(),
        "have_key": bool(os.environ.get("TAVILY_API_KEY")
                         or os.environ.get("SERPAPI_API_KEY")
                         or os.environ.get("SERPAPI_KEY")),
    }
    if args.verified_only:
        courses = [c for c in courses if c["course_college"] in state["verified"]]
    if args.order == "yield":
        # Front-load courses whose best cached link is a confident course page
        # (classify_link 'accept'), then 'ambiguous', then no-good/no link. This
        # maximizes early completes since yield tracks link quality, not rating.
        def _yield_key(c: dict):
            cc = c["course_college"]
            if cc in state["verified"]:
                return (0, 0.0)  # already settled; skips fast on resume
            best = (3, 0.0)
            for u in state["cache"].get(cc, []):
                v = classify_link(u, c["course_code"], c["college_name"])
                if v.verdict == "accept":
                    best = min(best, (1, -v.confidence))
                elif v.verdict == "ambiguous":
                    best = min(best, (2, -v.confidence))
            return best
        courses.sort(key=_yield_key)
    search_mode = "off" if args.no_search else (
        "keyed+keyless" if state["have_key"] and not args.no_keyless else
        "keyed" if state["have_key"] else
        "keyless" if not args.no_keyless else "off")
    print(f"{len(courses)} course(s) num_ratings>={args.min_ratings}"
          f"{' (verified-only)' if args.verified_only else ''}; "
          f"confirmed-links={len(state['verified'])}; "
          f"cached-links={len(state['cache'])}; web-search={search_mode}; "
          f"llm={'on' if args.llm else 'off'}"
          f"{'; retry=' + args.retry if args.retry else ''}", flush=True)

    todo = courses[: args.limit] if args.limit else courses
    if args.dry:
        session = requests.Session()
        for i, c in enumerate(todo, 1):
            url, source, query = resolve_link(sdb, c, args, session, state)
            print(f"  {i:>4} [{source:18}] {c['course_college'][:44]:44} "
                  f"n={c['num_ratings']:<4} {(url or '-')[:66]}")
        return 0

    try:
        signal.signal(signal.SIGALRM, _on_alarm)
    except (ValueError, AttributeError):
        pass  # watchdog needs the main thread on a POSIX platform
    session = requests.Session()
    tally: dict[str, int] = {}
    for i, c in enumerate(todo, 1):
        result = process_course(meta, sdb, c, args, session, state)
        tally[result.split()[0]] = tally.get(result.split()[0], 0) + 1
        print(f"[{i}/{len(todo)}] {c['course_college'][:42]:42} -> {result}", flush=True)
        if result != "skip":
            time.sleep(0.3)
    print("\nsummary:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())), flush=True)
    print(f"metadata (complete only): {out_dir/'rated4ccr.db'}", flush=True)
    print(f"searched links + progress: {out_dir/'rated4ccr_search.db'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
