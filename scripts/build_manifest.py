#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = ROOT / "SOURCE_MANIFEST.tsv"
EXCLUDED_MANIFEST = ROOT / "EXCLUDED_LARGE_OUTPUTS.tsv"
EXCLUDED_OUTPUTS = (
    Path("/home/b-ysonale/slidefetch/rated70"),
    Path("/home/b-ysonale/slidefetch/rated4ccr"),
    Path("/home/b-ysonale/slidefetch/rated4rmp"),
    Path("/home/b-ysonale/slidefetch/rated4rmp.bak_aggregated"),
    Path("/home/b-ysonale/slidefetch/downloads"),
    Path("/home/b-ysonale/rmp_scrape/downloads"),
    Path("/home/b-ysonale/scraper/dataset"),
    Path("/home/b-ysonale/slidehunt/data"),
    Path("/home/b-ysonale/slidehunt/work/http_cache"),
    Path("/home/b-ysonale/slideratings/data"),
)
SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def build_source_manifest() -> None:
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path in {SOURCE_MANIFEST, EXCLUDED_MANIFEST}:
            continue
        files.append(path)

    with SOURCE_MANIFEST.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("path", "bytes", "mtime_utc", "sha256"))
        for path in sorted(files):
            writer.writerow(
                (path.relative_to(ROOT), path.stat().st_size, utc_mtime(path), sha256(path))
            )


def build_excluded_manifest() -> None:
    with EXCLUDED_MANIFEST.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("source_path", "bytes", "files", "newest_mtime_utc"))
        for directory in EXCLUDED_OUTPUTS:
            files = [path for path in directory.rglob("*") if path.is_file()]
            total_bytes = sum(path.stat().st_size for path in files)
            newest = max((path.stat().st_mtime for path in files), default=0)
            newest_utc = (
                datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest else ""
            )
            writer.writerow((directory, total_bytes, len(files), newest_utc))


if __name__ == "__main__":
    build_source_manifest()
    build_excluded_manifest()