#!/usr/bin/env python3
"""Verify rated4rmp output DBs."""
import sqlite3
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "rated4rmp"

for name in ("rated4rmp.db", "rated4rmp_search.db"):
    db = OUT / name
    print(f"\n===== {name} (exists={db.exists()}) =====")
    if not db.exists():
        continue
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    for t in [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]:
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"  {t}: {n} rows")
    if name == "rated4rmp_search.db":
        print("  -- progress status tally --")
        for status, c in con.execute(
                "SELECT status, COUNT(*) FROM progress GROUP BY status ORDER BY 2 DESC"):
            print(f"     {status:22} {c}")
        print("  -- sample resolved (websearch) --")
        for r in con.execute(
                "SELECT course_school, status, num_decks, resolved_link FROM progress "
                "WHERE resolved_link IS NOT NULL ORDER BY num_ratings DESC LIMIT 8"):
            print(f"     {r['course_school'][:40]:40} {r['status']:14} "
                  f"decks={r['num_decks']} {(r['resolved_link'] or '')[:50]}")
    if name == "rated4rmp.db":
        print("  -- complete courses (metadata) --")
        for r in con.execute(
                "SELECT course_code, school, instructor, num_ratings, num_decks, "
                "seq_expected, complete_method FROM courses "
                "ORDER BY num_ratings DESC"):
            print(f"     {r['course_code']:<10} {(r['instructor'] or '')[:18]:<18} "
                  f"{r['school'][:26]:<26} n={r['num_ratings']:<3} "
                  f"decks={r['num_decks']} N={r['seq_expected']} [{r['complete_method']}]")
    con.close()
