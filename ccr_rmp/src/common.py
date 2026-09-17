"""Shared helpers: config, DB schema, the xlsx source-of-truth loader, printing.

The `courses` table is keyed by `course_college` (the raw "<code> - <College>"
string), which is unique per course in the source workbook.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import codes
from .xlsx import read_rows

PROJECT_DIR = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else PROJECT_DIR / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    # Resolve relative paths against the project directory.
    for key in ("db_path", "artifacts_dir"):
        val = cfg.get(key)
        if val and not os.path.isabs(val):
            cfg[key] = str(PROJECT_DIR / val)
    return cfg


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# Source of truth: dataset_ccr_new.xlsx ONLY
# --------------------------------------------------------------------------- #
def load_courses_from_xlsx(xlsx_path: str | Path) -> list[dict[str, Any]]:
    """Return the populated courses from the workbook (empty rows dropped).

    Each dict has: row_index, course_college, course_code, college_name,
    num_ratings, course_slide_links.
    """
    out: list[dict[str, Any]] = []
    for i, row in enumerate(read_rows(str(xlsx_path)), start=2):  # row 1 = header
        link = (row.get("course_slide_links") or "").strip()
        course_college = (row.get("course_college") or "").strip()
        if not link and not course_college:
            continue  # empty padding row
        _, college = codes.split_course_college(course_college)
        out.append(
            {
                "row_index": i,
                "course_college": course_college or None,
                "course_code": codes.clean_code(row.get("course_code"), course_college),
                "college_name": college,
                "num_ratings": codes.parse_num_ratings(row.get("num_ratings")),
                "course_slide_links": link or None,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    course_college              TEXT PRIMARY KEY,   -- "<code> - <College>" (source key)
    row_index                   INTEGER,
    course_code                 TEXT,
    college_name                TEXT,               -- parsed university
    num_ratings                 REAL,               -- CCR rating count (from xlsx)
    course_slide_links          TEXT,               -- raw cell (may hold >1 URL)

    -- Stage 1: fetched slide artifact
    slide_url_used              TEXT,
    slide_kind                  TEXT,               -- pdf | ppt | html | gh_repo | ...
    slide_artifact              TEXT,               -- path to image / cached text
    slide_status                TEXT,               -- ok | empty | error | no_link

    -- Stage 2: instructor (LLM)
    instructor                  TEXT,
    instructor_confidence       REAL,
    instructor_source           TEXT,               -- 'llm'
    instructor_status           TEXT,               -- found | no_instructor | fetch_failed
    instructor_debug            TEXT,               -- JSON: pages sent, cues, raw reply

    -- Stage 3: RMP professor
    rmp_status                  TEXT,                -- found | not_found | school_not_found | no_instructor | error
    rmp_school_id               TEXT,
    rmp_school_name             TEXT,
    rmp_legacy_id               TEXT,
    rmp_matched_name            TEXT,
    rmp_profile_url             TEXT,
    rmp_avg_rating_overall      REAL,
    rmp_avg_difficulty_overall  REAL,
    rmp_num_ratings_overall     INTEGER,
    rmp_would_take_again_overall REAL,

    -- Stage 4: THIS course's ratings
    course_num_ratings          INTEGER,
    course_avg_quality          REAL,
    course_avg_difficulty       REAL,
    course_would_take_again     REAL,               -- % "yes" among matched ratings
    matched_class_values        TEXT,               -- JSON list of RMP class strings kept

    needs_review                INTEGER DEFAULT 0,
    review_reasons              TEXT,
    stage                       TEXT,                -- loaded | fetched | instructor | rmp | ratings
    updated_at                  TEXT
);

CREATE TABLE IF NOT EXISTS course_ratings_raw (
    rating_id        TEXT PRIMARY KEY,
    course_college   TEXT,
    legacy_id        TEXT,
    class            TEXT,
    quality          REAL,
    difficulty       REAL,
    clarity          REAL,
    helpful          REAL,
    would_take_again INTEGER,
    grade            TEXT,
    attendance       TEXT,               -- mandatory | non mandatory | NULL
    for_credit       INTEGER,            -- 1 | 0 | NULL
    online_class     INTEGER,            -- 1 | 0 | NULL
    textbook_use     INTEGER,            -- RMP textbook-use scale | NULL
    thumbs_up        INTEGER,
    thumbs_down      INTEGER,
    date             TEXT,
    comment          TEXT,
    tags             TEXT
);
CREATE INDEX IF NOT EXISTS idx_ratings_course ON course_ratings_raw(course_college);

CREATE TABLE IF NOT EXISTS school_cache (
    college_name TEXT PRIMARY KEY,   -- as parsed from the xlsx
    school_id    TEXT,               -- RMP GraphQL node id (base64)
    legacy_id    TEXT,
    school_name  TEXT,               -- RMP's canonical school name
    city         TEXT,
    state        TEXT,
    status       TEXT,               -- found | not_found
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    course_college        TEXT PRIMARY KEY,   -- course this feedback is about
    instructor_ok         INTEGER,            -- 1 correct | 0 wrong | NULL unset
    instructor_correction TEXT,               -- the right name, if user supplied
    rmp_ok                INTEGER,            -- 1 correct | 0 wrong | NULL unset
    notes                 TEXT,
    needs_review          INTEGER DEFAULT 0,
    updated_at            TEXT
);

-- One row PER (course, instructor): a course may be co-taught, and each
-- instructor has their own RMP profile + this-course ratings (feedback CSE325).
CREATE TABLE IF NOT EXISTS course_professors (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    course_college           TEXT,
    instructor_name          TEXT,            -- one name out of the (co-)instructors
    rmp_status               TEXT,            -- found | not_found | school_not_found | ambiguous | error
    match_type               TEXT,            -- unique | given | ambiguous | ...
    school_id                TEXT,
    school_name              TEXT,
    legacy_id                TEXT,
    matched_name             TEXT,
    profile_url              TEXT,
    department               TEXT,
    avg_rating_overall       REAL,
    avg_difficulty_overall   REAL,
    num_ratings_overall      INTEGER,
    would_take_again_overall REAL,
    -- Stage 4 (this professor, this course):
    course_num_ratings       INTEGER,
    course_avg_quality       REAL,
    course_avg_difficulty    REAL,
    course_would_take_again  REAL,
    matched_class_values     TEXT,            -- JSON list of RMP `class` strings kept
    updated_at               TEXT,
    UNIQUE(course_college, instructor_name)
);
CREATE INDEX IF NOT EXISTS idx_prof_course ON course_professors(course_college);
"""

# Columns added after the first schema shipped (kept for old DBs).
_COURSE_MIGRATIONS = {
    "instructor_debug": "TEXT",
}

# Per-rating detail columns added for individual-rating extraction.
_RATINGS_MIGRATIONS = {
    "clarity": "REAL",
    "helpful": "REAL",
    "attendance": "TEXT",
    "for_credit": "INTEGER",
    "online_class": "INTEGER",
    "textbook_use": "INTEGER",
    "thumbs_up": "INTEGER",
    "thumbs_down": "INTEGER",
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, migrations in (
        ("courses", _COURSE_MIGRATIONS),
        ("course_ratings_raw", _RATINGS_MIGRATIONS),
    ):
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in migrations.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def open_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    return conn


def update_course(conn: sqlite3.Connection, course_college: str, **fields: Any) -> None:
    """Patch one course row by its key; always bumps updated_at."""
    fields["updated_at"] = now()
    assignments = ", ".join(f"{col} = :{col}" for col in fields)
    fields["_key"] = course_college
    conn.execute(
        f"UPDATE courses SET {assignments} WHERE course_college = :_key", fields
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Pretty printing (for --example output)
# --------------------------------------------------------------------------- #
def rule(title: str = "", width: int = 88) -> str:
    if not title:
        return "-" * width
    pad = width - len(title) - 4
    return f"-- {title} " + "-" * max(pad, 0)


def fmt_kv(data: dict[str, Any], keys: list[str] | None = None, width: int = 26) -> str:
    keys = keys or list(data)
    lines = []
    for k in keys:
        val = data.get(k)
        if val is None:
            val = "·"
        lines.append(f"  {k:<{width}} {val}")
    return "\n".join(lines)


def print_table(rows: list[dict[str, Any]], cols: list[tuple[str, int]]) -> None:
    """Print rows as a fixed-width table. `cols` = list of (key, width)."""
    header = "  ".join(f"{name:<{w}}" for name, w in cols)
    print(header)
    print("  ".join("-" * w for _, w in cols))
    for row in rows:
        cells = []
        for key, w in cols:
            text = "" if row.get(key) is None else str(row.get(key))
            if len(text) > w:
                text = text[: w - 1] + "…"
            cells.append(f"{text:<{w}}")
        print("  ".join(cells))
