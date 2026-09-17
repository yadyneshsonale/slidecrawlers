"""Command-line interface for slideratings.

Commands:
  crawl-unis   Rank CCR universities by number of rated courses.
  run          Process universities (rank order): CCR ratings + RMP + slides.
  status       Print database counts and the university ranking/progress.
"""
from __future__ import annotations

import argparse
import sys

from . import store
from .config import load_settings
from .pipeline import crawl_universities, run


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default=None, help="path to config.yaml")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="slideratings", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_crawl = sub.add_parser("crawl-unis", help="rank CCR universities")
    _add_common(p_crawl)
    p_crawl.add_argument("--limit", type=int, default=None,
                         help="stop after N rated universities")
    p_crawl.add_argument("--refresh", action="store_true",
                         help="ignore the HTML cache")
    p_crawl.add_argument("--dry-run", action="store_true",
                         help="print the ranking without downloading slides")

    p_run = sub.add_parser("run", help="process courses for ranked universities")
    _add_common(p_run)
    p_run.add_argument("--limit-unis", type=int, default=None)
    p_run.add_argument("--limit-courses", type=int, default=None)
    p_run.add_argument("--start-rank", type=int, default=1)
    p_run.add_argument("--uni", default=None, help="process a single CCR slug")
    p_run.add_argument("--no-resume", action="store_true",
                       help="re-process universities already marked done")

    p_status = sub.add_parser("status", help="show database counts and ranking")
    _add_common(p_status)
    p_status.add_argument("--top", type=int, default=20)

    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    settings.ensure_dirs()
    conn = store.connect(settings.db_path)

    if args.command == "crawl-unis":
        n = crawl_universities(settings, conn, limit=args.limit, refresh=args.refresh)
        rows = store.universities_by_rank(conn)[:50]
        for r in rows:
            print(f"{r['rank']:>4}  {r['rated_courses']:>5} rated  "
                  f"{r['abbrev']:<12} {r['name']}")
        print(f"\nranked {n} universities")
        return 0

    if args.command == "run":
        run(settings, conn,
            limit_unis=args.limit_unis,
            limit_courses=args.limit_courses,
            start_rank=args.start_rank,
            only_uni=args.uni,
            resume=not args.no_resume)
        return 0

    if args.command == "status":
        c = store.counts(conn)
        print("counts: " + ", ".join(f"{k}={v}" for k, v in c.items()))
        print("\nrank  rated  abbrev       university            status")
        for r in store.universities_by_rank(conn)[:args.top]:
            name = r["name"] or ""
            print(f"{r['rank']:>4}  {r['rated_courses']:>5}  "
                  f"{r['abbrev']:<12} {name[:22]:<22} {r['status']}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
