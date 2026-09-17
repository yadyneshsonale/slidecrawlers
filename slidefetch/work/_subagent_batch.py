#!/usr/bin/env python3.11
"""Phase 2b helper: feed ambiguous courses to a subagent and write verdicts back.

The deterministic pass (``ccr_verify_labels.py``) leaves the hard cases as
``verdict='ambiguous'`` in ``ccr_verified.db``, each with per-link evidence stored in
``candidates_json`` (URL verdict, trust, deck_count, page title, sample deck names). This
helper drives the subagent stage:

    # 1. print a batch (JSON) to paste into a subagent prompt
    python work/_subagent_batch.py emit --offset 0 --limit 25

    # 2. write the subagent's answers back (TSV: course_college<TAB>choice<TAB>reason,
    #    choice in {link_1, link_2, none})
    python work/_subagent_batch.py ingest work/verdicts.tsv

    python work/_subagent_batch.py stats
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone

DB = "ccr_verified.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(offset: int, limit: int) -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT course_college, course_code, num_ratings, candidates_json "
        "FROM verified_labels WHERE verdict='ambiguous' ORDER BY course_college "
        "LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    con.close()
    out = []
    for cc, code, nr, cj in rows:
        evals = json.loads(cj) if cj else []
        links = []
        for e in evals:
            # Skip search-result noise already rejected by the URL rules (live_candidates
            # can carry up to ~8 raw URLs, most of them aggregators/catalogs).
            if e.get("url_verdict") == "reject" or e.get("status") == "reject":
                continue
            ev = e.get("evidence") or {}
            links.append({
                "slot": e.get("which"),
                "url": e.get("url"),
                "url_verdict": e.get("url_verdict"),
                "url_reason": e.get("url_reason"),
                "trust": e.get("trust"),
                "deck_count": e.get("deck_count"),
                "fetch_status": e.get("fetch_status"),
                "page_title": ev.get("title", ""),
                "sample_decks": ev.get("sample_decks", []),
            })
        out.append({"course_college": cc, "course_code": code,
                    "num_ratings": nr, "links": links})
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)
    print()


def ingest(path: str) -> None:
    con = sqlite3.connect(DB)
    # url per slot for each course, so we can store the chosen correct_url.
    url_by = {}
    for cc, cj in con.execute(
        "SELECT course_college, candidates_json FROM verified_labels WHERE verdict='ambiguous'"
    ):
        d = {}
        for e in (json.loads(cj) if cj else []):
            d[e.get("which")] = e.get("url")
        url_by[cc] = d

    n_conf = n_none = n_skip = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            cc, choice = parts[0].strip(), parts[1].strip().lower()
            reason = parts[2].strip() if len(parts) > 2 else ""
            if cc not in url_by:
                n_skip += 1
                continue
            if re.fullmatch(r"link_\d+", choice) and url_by[cc].get(choice):
                url = url_by[cc].get(choice, "")
                con.execute(
                    "UPDATE verified_labels SET verdict='confirmed', correct_url=?, "
                    "which_link=?, method='subagent', confidence=0.75, reason=?, "
                    "verified_at=? WHERE course_college=? AND verdict='ambiguous'",
                    (url, choice, f"subagent: {reason}"[:300], _now(), cc))
                n_conf += 1
            elif choice in ("none", "no_valid_link", ""):
                con.execute(
                    "UPDATE verified_labels SET verdict='no_valid_link', correct_url='', "
                    "which_link='', method='subagent', confidence=0.6, reason=?, "
                    "verified_at=? WHERE course_college=? AND verdict='ambiguous'",
                    (f"subagent: {reason}"[:300], _now(), cc))
                n_none += 1
            else:
                n_skip += 1
    con.commit()
    con.close()
    print(f"ingested: confirmed={n_conf}, no_valid_link={n_none}, skipped={n_skip}")


def stats() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print(dict(con.execute("SELECT verdict, COUNT(*) FROM verified_labels GROUP BY verdict")))
    print("by method:",
          dict(con.execute("SELECT method, COUNT(*) FROM verified_labels GROUP BY method")))
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit"); e.add_argument("--offset", type=int, default=0); e.add_argument("--limit", type=int, default=25)
    i = sub.add_parser("ingest"); i.add_argument("path")
    sub.add_parser("stats")
    args = ap.parse_args()
    if args.cmd == "emit":
        emit(args.offset, args.limit)
    elif args.cmd == "ingest":
        ingest(args.path)
    else:
        stats()


if __name__ == "__main__":
    main()
