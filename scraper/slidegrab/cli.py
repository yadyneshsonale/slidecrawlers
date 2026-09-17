"""Command-line entry point for slidegrab.

Examples:
    export TAVILY_API_KEY=your_key      # or SERPER_API_KEY / BRAVE_API_KEY
    python -m slidegrab.cli run --region india
    python -m slidegrab.cli run --region india --dry-run   # search only
    python -m slidegrab.cli run --region global --limit-unis 3
    python -m slidegrab.cli index                           # rebuild index from dataset/
"""
from __future__ import annotations

import argparse
import asyncio

from slidegrab.pipeline import run
from slidegrab.search import SearchKeyMissing
from slidegrab.store import Store


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        stats = asyncio.run(
            run(
                region=args.region,
                limit_unis=args.limit_unis,
                max_queries_per_uni=args.max_queries,
                max_pages_per_query=args.max_pages,
                per_course_cap=args.per_course_cap,
                dry_run=args.dry_run,
                discover_codes=not args.no_discover_codes,
                max_code_discovery_queries=args.max_code_queries,
                max_codes_per_uni=args.max_codes,
            )
        )
    except SearchKeyMissing as err:
        print(f"\nERROR: {err}")
        return 2

    print("\n==== summary ====")
    print(f"queries run     : {stats.queries_run}")
    print(f"codes discovered: {stats.codes_discovered}")
    print(f"pages fetched   : {stats.pages_fetched}")
    print(f"courses w/ decks: {len(stats.courses)}")
    print(f"decks downloaded: {stats.total_downloaded}")
    for c in stats.courses:
        print(f"  {c.university}/{c.course}: +{c.downloaded} "
              f"(skip {c.skipped}, reject {c.rejected})")
    return 0


def _cmd_index(_: argparse.Namespace) -> int:
    store = Store()
    added = store.scan_dataset()
    c, d = store.counts()
    store.close()
    print(f"indexed dataset: +{added} new files (courses={c}, decks={d})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slidegrab")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="search and download slide decks")
    p_run.add_argument("--region", default="india",
                       choices=["india", "global", "all"])
    p_run.add_argument("--limit-unis", type=int, default=None)
    p_run.add_argument("--max-queries", type=int, default=8,
                       help="max search queries per university")
    p_run.add_argument("--max-pages", type=int, default=4,
                       help="max course pages to visit per query")
    p_run.add_argument("--per-course-cap", type=int, default=40,
                       help="max decks to download per course")
    p_run.add_argument("--no-discover-codes", action="store_true",
                       help="skip course-code discovery; use seed codes only")
    p_run.add_argument("--max-code-queries", type=int, default=3,
                       help="max catalog searches per university for code discovery")
    p_run.add_argument("--max-codes", type=int, default=24,
                       help="max discovered course codes to keep per university")
    p_run.add_argument("--dry-run", action="store_true",
                       help="search only; print course-page URLs, download nothing")
    p_run.set_defaults(func=_cmd_run)

    p_index = sub.add_parser("index", help="(re)build index.sqlite from dataset/")
    p_index.set_defaults(func=_cmd_index)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
