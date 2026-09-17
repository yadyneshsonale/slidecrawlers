"""Command-line interface for slidehunt.

Commands:
  run        Search random course codes, download slides, fetch RMP/CCR ratings.
  status     Print row counts from the database.
  gen-codes  Preview randomly-generated course codes (no network).
"""
from __future__ import annotations

import argparse
import sys

from . import pipeline, store
from .codes import generate_codes
from .config import load_settings


def _cmd_run(args) -> int:
    settings = load_settings(args.config)
    pipeline.run(
        settings,
        count=args.count,
        seed=args.seed,
        max_hits=args.max_hits,
        resume=not args.no_resume,
        until_downloaded=args.until_downloaded,
    )
    return 0


def _cmd_status(args) -> int:
    settings = load_settings(args.config)
    settings.ensure_dirs()
    conn = store.connect(settings.db_path)
    try:
        for table, n in store.counts(conn).items():
            print(f"{table:16} {n}")
    finally:
        conn.close()
    return 0


def _cmd_gen_codes(args) -> int:
    for code in generate_codes(args.count, args.seed):
        print(code)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slidehunt")
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run the search/download/ratings pipeline")
    r.add_argument("--count", type=int, default=None,
                   help="number of codes to process (default 50 if no target)")
    r.add_argument("--until-downloaded", type=int, default=None,
                   help="keep searching new codes until this many courses are downloaded")
    r.add_argument("--max-hits", type=int, default=None,
                   help="override max search hits considered per code")
    r.add_argument("--seed", type=int, default=None, help="RNG seed for codes")
    r.add_argument("--no-resume", action="store_true",
                   help="do not skip codes already searched in the db")
    r.set_defaults(func=_cmd_run)

    s = sub.add_parser("status", help="print database counts")
    s.set_defaults(func=_cmd_status)

    g = sub.add_parser("gen-codes", help="preview generated course codes")
    g.add_argument("--count", type=int, default=20)
    g.add_argument("--seed", type=int, default=None)
    g.set_defaults(func=_cmd_gen_codes)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
