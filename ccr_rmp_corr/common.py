#!/usr/bin/env python3
"""Shared paths + helpers for the CCR-vs-RMP correlation build."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

ROOT = Path("/home/b-ysonale/ccr_rmp_corr")
WORK = ROOT / "work"
CACHE = WORK / "http_cache"

MANIFEST = Path("/home/b-ysonale/slidefetch/rated70/manifest.csv")
RATED70_DB = "/home/b-ysonale/slidefetch/rated70/rated70.db"
CCR_RMP_DB = "/home/b-ysonale/ccr_rmp/ccr_rmp.db"
CCR_COURSES_DB = "/home/b-ysonale/slidefetch/ccr_courses.db"
SLIDE_SUMMARY = Path("/home/b-ysonale/RateMySlides/output_trapi/summary.json")


def ro(path: str) -> sqlite3.Connection:
    """Open a SQLite DB read-only with Row access."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def norm(s: str | None) -> str:
    """Uppercase alphanumeric-only key for fuzzy code/college matching."""
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def load_manifest() -> list[dict]:
    with open(MANIFEST, encoding="utf-8") as f:
        return list(csv.DictReader(f))
