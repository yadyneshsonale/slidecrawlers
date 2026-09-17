#!/usr/bin/env python3.11
"""STEP 2 -- verify the auto-labelled slide links and save the correct one.

Uses the codified knowledge base (``ccr_slide_kb.classify_link``) plus a light
content check to decide, for every course in ``ccr_labels.db`` table ``auto_labels``
that has NOT been hand-annotated (``correct.txt`` / ``wrong.txt``), which of its two
candidate links is the real lecture-slides page.

Pipeline per candidate link (URL rules first, cheap content check second):

  * ``reject``  URL is an admin page / README / aggregator     -> dropped, no fetch.
  * ``accept`` + a direct deck file (.pdf/.ppt/.pptx)          -> trusted (confirmed).
  * otherwise (``accept`` html / ``ambiguous``)                -> fast ``requests`` fetch +
        ``find_slides``; if real decks are found it is confirmed, else it is left
        ``inconclusive`` for the subagent pass.

Per course:
  * >=1 confirmed link  -> save the best one (verdict ``confirmed``).
  * both links reject   -> ``no_valid_link``.
  * anything left over  -> ``ambiguous`` (handed to the subagent pass, which reads the
        knowledge base and opens the pages to judge topic / cross-university / JS sites).

Writes ``ccr_verified.db`` (table ``verified_labels``), resumable, and exports
``ccr_verified_review.md``.

    python3.11 ccr_verify_labels.py                 # full run (resumes)
    python3.11 ccr_verify_labels.py --limit 50      # first 50 to-do courses
    python3.11 ccr_verify_labels.py --no-fetch      # URL rules only (no content check)
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import warnings
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from ccr_slide_kb import classify_link, confirmation_trust
from slidefetch.extract import find_index_links, find_slides
from slidefetch.urls import reduce_url

try:  # noisy when a course page is actually XML/XHTML
    from bs4 import XMLParsedAsHTMLWarning
    warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
except Exception:  # noqa: BLE001
    pass

LABELS_DB = "ccr_labels.db"
OUT_DB = "ccr_verified.db"
REVIEW_MD = "ccr_verified_review.md"
CORRECT_TXT = "correct.txt"
WRONG_TXT = "wrong.txt"

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_DECK_EXTS = (".pdf", ".ppt", ".pptx")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _college_of(cc: str) -> str:
    return cc.split(" - ", 1)[1] if " - " in cc else ""


def _is_deck_file(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(_DECK_EXTS)


def load_annotated() -> set[str]:
    """course_college keys already hand-labelled (correct.txt + wrong.txt)."""
    keys: set[str] = set()
    for path, skip_header in ((CORRECT_TXT, True), (WRONG_TXT, False)):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
        except FileNotFoundError:
            continue
        for ln in (lines[1:] if skip_header else lines):
            key = ln.split("\t", 1)[0].strip()
            if key:
                keys.add(key)
    return keys


def init_out(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS verified_labels (
            course_college  TEXT PRIMARY KEY,
            course_code     TEXT,
            num_ratings     INTEGER,
            correct_url     TEXT,
            which_link      TEXT,
            verdict         TEXT,   -- confirmed | no_valid_link | ambiguous
            confidence      REAL,
            method          TEXT,
            deck_count      INTEGER,
            reason          TEXT,
            candidates_json TEXT,
            verified_at     TEXT
        )
        """
    )
    con.commit()
    return con


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _evidence(html: str, samples: list[str]) -> dict:
    m = _TITLE_RE.search(html or "")
    title = re.sub(r"\s+", " ", (m.group(1) if m else "")).strip()[:140]
    return {"title": title, "sample_decks": [u.rsplit("/", 1)[-1][:70] for u in samples]}


def _probe_once(session, url, timeout) -> tuple[int, list[str], str, str]:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
    except Exception as err:  # noqa: BLE001
        return 0, [], f"error:{type(err).__name__}", ""
    if r.status_code >= 400:
        return 0, [], f"http:{r.status_code}", ""
    ctype = r.headers.get("content-type", "").lower()
    if "html" not in ctype:
        return 0, [], f"non-html:{ctype.split(';')[0][:24]}", ""
    try:
        slides = find_slides(r.text, r.url)
    except Exception as err:  # noqa: BLE001
        return 0, [], f"parse-error:{type(err).__name__}", r.text
    return len(slides), [s.url for s in slides[:5]], "ok", r.text


def probe_slides(session: requests.Session, url: str, timeout: int,
                 follow: bool = False) -> tuple[int, list[str], str, dict, str]:
    """Fetch *url* (static) and count real slide decks via ``find_slides``.

    Returns ``(deck_count, samples, status, evidence, resolved_url)``. No JS rendering,
    so JS-heavy / Pages / GitHub sites return 0 here and are handed to the subagent.
    When *follow* is set and the page itself has no decks, tries ``reduce_url`` and up
    to two ``find_index_links`` sub-pages (course homepage -> lectures page).
    """
    n, samples, st, html = _probe_once(session, url, timeout)
    ev = _evidence(html, samples)
    if n > 0 or not follow or st != "ok":
        return n, samples, st, ev, url
    red = reduce_url(url)
    if red != url:
        n2, s2, st2, h2 = _probe_once(session, red, timeout)
        if n2 > 0:
            return n2, s2, "reduced", _evidence(h2, s2), red
    try:
        for sub in find_index_links(html, url)[:2]:
            n2, s2, st2, h2 = _probe_once(session, sub, timeout)
            if n2 > 0:
                return n2, s2, "followed", _evidence(h2, s2), sub
    except Exception:  # noqa: BLE001
        pass
    return n, samples, st, ev, url


def evaluate_link(session, url, code, college, do_fetch: bool) -> dict:
    """URL verdict + (for non-reject, non-deck-file) a content check."""
    v = classify_link(url, code, college)
    ev = {
        "url": url, "url_verdict": v.verdict, "url_reason": v.reason,
        "confidence": v.confidence, "deck_count": None, "status": None, "method": None,
    }
    if v.verdict == "reject":
        ev["status"] = "reject"
        return ev
    trust, trust_why = confirmation_trust(url, code, college)
    ev["trust"] = trust
    ev["trust_why"] = trust_why
    # A confirmation only counts when we can tie the page to THIS course (same university,
    # or the course code appears and the host is not a *different* known university);
    # off-site decks (cross-university code coincidences) are handed to the subagent.
    if v.verdict == "accept" and _is_deck_file(url):
        ev["method"] = "rule:deck-file"
        ev["status"] = "confirmed" if trust else "offsite-candidate"
        return ev
    if not do_fetch:
        ev["status"] = "pending"
        return ev
    n, samples, st, evid, resolved = probe_slides(session, url, timeout=12, follow=trust)
    ev["deck_count"] = n
    ev["fetch_status"] = st
    ev["evidence"] = evid
    if resolved != url:
        ev["resolved_url"] = resolved
    if n >= 2:
        ev["method"] = "fetch:decks" if trust else "fetch:decks-offsite"
        ev["status"] = "confirmed" if trust else "offsite-candidate"
    else:
        # 0 decks (JS/Pages/GitHub/empty) or a single stray link -> hand to the subagent.
        ev["status"] = "inconclusive"
    return ev


def decide(evals: list[dict]) -> dict:
    """Turn per-link evaluations into a course verdict + chosen link."""
    confirmed = [e for e in evals if e["status"] == "confirmed"]
    if confirmed:
        # Prefer more decks, then a deck file, then higher URL confidence, then link order.
        best = max(
            confirmed,
            key=lambda e: (
                e["deck_count"] or (1 if e["method"] == "rule:deck-file" else 0),
                e["confidence"],
            ),
        )
        return {
            "verdict": "confirmed", "correct_url": best["url"],
            "which_link": best["which"], "confidence": best["confidence"],
            "method": best["method"], "deck_count": best["deck_count"],
            "reason": f"{best['method']}; url:{best['url_reason']}",
        }
    if evals and all(e["status"] == "reject" for e in evals):
        why = "; ".join(f"{e['which']}:{e['url_reason']}" for e in evals)
        return {"verdict": "no_valid_link", "correct_url": "", "which_link": "",
                "confidence": 0.8, "method": "rule:reject", "deck_count": 0,
                "reason": f"both rejected [{why}]"}
    # something inconclusive/pending remains -> subagent.
    why = "; ".join(f"{e['which']}:{e['status']}/{e['url_reason']}" for e in evals)
    return {"verdict": "ambiguous", "correct_url": "", "which_link": "",
            "confidence": 0.0, "method": "pending-subagent", "deck_count": None,
            "reason": why}


def export_markdown(con: sqlite3.Connection, path: str) -> None:
    rows = con.execute(
        "SELECT verdict, COUNT(*) FROM verified_labels GROUP BY verdict"
    ).fetchall()
    counts = {v: n for v, n in rows}
    total = sum(counts.values())
    lines = ["# Verified slide links", "",
             f"{total} course(s): " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
             ""]
    for cc, url, verdict, method, reason in con.execute(
        "SELECT course_college, correct_url, verdict, method, reason FROM verified_labels "
        "WHERE verdict='confirmed' ORDER BY course_college"
    ):
        detail = reason if (reason or "").startswith(method) else f"{method}: {reason}"
        lines.append(f"## {cc}\n- {url}\n- {detail}\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _load_source_rows(labels_db: str, source: str, cap: int = 8):
    """Return (rows, rated) where rows = [(course_college, code, num_ratings, [(slot,url)])].

    ``source='auto_labels'`` -> the 2 pre-scored links per course.
    ``source='live_candidates'`` -> the JSON list of live search-result URLs per course
    (deduped and capped); code/ratings are taken from ``auto_labels`` when present, else the
    code is parsed from the ``COURSE - College`` key and ratings default to 0.
    ``rated`` = course_college set with num_ratings > 0 in auto_labels (for the skip rule).
    """
    con = sqlite3.connect(f"file:{labels_db}?mode=ro", uri=True)
    al = {cc: ((code or ""), (nr or 0)) for cc, code, nr in
          con.execute("SELECT course_college, course_code, num_ratings FROM auto_labels")}
    rows = []
    if source == "auto_labels":
        for cc, code, nr, l1, l2 in con.execute(
                "SELECT course_college, course_code, num_ratings, link_1, link_2 FROM auto_labels"):
            links = [(f"link_{i}", u.strip()) for i, u in enumerate((l1, l2), 1) if u and u.strip()]
            rows.append((cc, code or "", nr or 0, links))
    else:  # live_candidates
        for cc, cj in con.execute("SELECT course_college, candidates_json FROM live_candidates"):
            try:
                urls = json.loads(cj) if cj else []
            except Exception:  # noqa: BLE001
                urls = []
            seen, clean = set(), []
            for u in urls:
                if isinstance(u, dict):
                    u = u.get("url") or u.get("link") or ""
                u = (u or "").strip()
                key = u.split("#", 1)[0].rstrip("/")
                if u and key not in seen:
                    seen.add(key)
                    clean.append(u)
                if len(clean) >= cap:
                    break
            code, nr = al.get(cc, (cc.split(" - ", 1)[0], 0))
            links = [(f"link_{i}", u) for i, u in enumerate(clean, 1)]
            rows.append((cc, code, nr, links))
    con.close()
    rated = {cc for cc, (_code, nr) in al.items() if nr > 0}
    return rows, rated


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels-db", default=LABELS_DB)
    ap.add_argument("--out-db", default=OUT_DB)
    ap.add_argument("--source", choices=["auto_labels", "live_candidates"], default="auto_labels",
                    help="which candidate table to verify")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-fetch", action="store_true", help="URL rules only (no content check)")
    ap.add_argument("--progress", type=int, default=50, help="print every N courses")
    args = ap.parse_args()

    annotated = load_annotated()
    rows, rated = _load_source_rows(args.labels_db, args.source)

    con = init_out(args.out_db)
    done = {r[0] for r in con.execute("SELECT course_college FROM verified_labels")}

    # Build the to-do list.
    #   auto_labels     : skip annotated + anything already verified.
    #   live_candidates : skip annotated; skip courses that are already done AND rated
    #                     (num_ratings>0 -- "the 213"); re-do everything else (overwrite).
    todo, skipped_rated_done = [], 0
    for cc, code, nr, links in rows:
        if cc in annotated:
            continue
        if args.source == "live_candidates":
            if cc in done and cc in rated:
                skipped_rated_done += 1
                continue
        elif cc in done:
            continue
        if links:
            todo.append((cc, code, nr, links))
    if args.limit:
        todo = todo[: args.limit]
    print(f"source={args.source}; {len(rows)} rows; {len(annotated)} annotated; "
          f"{len(done)} already verified; skipped(done+rated)={skipped_rated_done}; "
          f"{len(todo)} to do this run", flush=True)

    session = requests.Session()
    session.headers.update({"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
    tally = {"confirmed": 0, "no_valid_link": 0, "ambiguous": 0}

    for i, (cc, code, nr, links) in enumerate(todo, 1):
        evals = []
        for which, url in links:
            ev = evaluate_link(session, url.strip(), code, _college_of(cc), not args.no_fetch)
            ev["which"] = which
            evals.append(ev)
        d = decide(evals)
        tally[d["verdict"]] = tally.get(d["verdict"], 0) + 1
        con.execute(
            "INSERT OR REPLACE INTO verified_labels (course_college, course_code, num_ratings, "
            "correct_url, which_link, verdict, confidence, method, deck_count, reason, "
            "candidates_json, verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cc, code, nr, d["correct_url"], d["which_link"], d["verdict"], d["confidence"],
             d["method"], d["deck_count"], d["reason"], json.dumps(evals), _now()),
        )
        con.commit()
        if i % args.progress == 0 or i == len(todo):
            print(f"[{i}/{len(todo)}] {tally}", flush=True)

    export_markdown(con, REVIEW_MD)
    grand = dict(con.execute("SELECT verdict, COUNT(*) FROM verified_labels GROUP BY verdict"))
    con.close()
    print(f"\ndone this run: {tally}")
    print(f"total in {args.out_db}: {grand}  (+ {REVIEW_MD})")


if __name__ == "__main__":
    main()
