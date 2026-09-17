"""SQLite storage for slidehunt.

One database (``slidehunt.db``) records each search, the universities/courses
discovered from the resulting links, the downloaded slide decks, RMP professor
ratings and CCR course ratings, plus every relevant external link (RMP / CCR /
official roster / slide source).
"""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT,
    query      TEXT,
    n_hits     INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS universities (
    slug            TEXT PRIMARY KEY,
    name            TEXT,
    host            TEXT,
    ccr_slug        TEXT,
    rmp_school_id   TEXT,
    rmp_school_name TEXT
);

CREATE TABLE IF NOT EXISTS courses (
    course_id     TEXT PRIMARY KEY,            -- <uni_slug>-<code>
    code          TEXT,
    uni_slug      TEXT NOT NULL,
    page_url      TEXT,
    reduced_url   TEXT,
    slug          TEXT,                        -- download-folder slug
    title         TEXT,
    status        TEXT DEFAULT 'pending',      -- pending|done|error
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS professors (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT,
    uni_slug         TEXT,
    rmp_legacy_id    INTEGER,
    matched_name     TEXT,
    department       TEXT,
    avg_rating       REAL,
    avg_difficulty   REAL,
    num_ratings      INTEGER,
    would_take_again REAL,
    rmp_url          TEXT,
    match_type       TEXT,
    UNIQUE(uni_slug, name)
);

CREATE TABLE IF NOT EXISTS course_professors (
    course_id    TEXT NOT NULL REFERENCES courses(course_id),
    professor_id INTEGER NOT NULL REFERENCES professors(id),
    source       TEXT,
    PRIMARY KEY (course_id, professor_id)
);

CREATE TABLE IF NOT EXISTS slide_decks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id      TEXT NOT NULL REFERENCES courses(course_id),
    source_url     TEXT,
    page_url       TEXT,
    file_path      TEXT UNIQUE,
    sha256         TEXT,
    file_type      TEXT,
    lecture_number INTEGER,
    status         TEXT DEFAULT 'verified'
);

CREATE TABLE IF NOT EXISTS ccr_ratings (
    course_id             TEXT PRIMARY KEY REFERENCES courses(course_id),
    ccr_url               TEXT,
    star_rating           REAL,
    num_reviews           INTEGER,
    difficulty            REAL,
    student_satisfaction  REAL,
    challenge_level       REAL,
    grade_accessibility   REAL,
    time_investment       REAL,
    attendance_importance REAL,
    recommendation_rate   REAL
);

CREATE TABLE IF NOT EXISTS links (
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    kind      TEXT NOT NULL,                   -- rmp|ccr|official|slide_source
    url       TEXT NOT NULL,
    PRIMARY KEY (course_id, kind, url)
);
"""


def connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _upsert(conn, table: str, cols, conflict: str, row: dict) -> None:
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {updates}",
        [row.get(c) for c in cols],
    )
    conn.commit()


def record_search(conn, code: str, query: str, n_hits: int, created_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO searches (code, query, n_hits, created_at) VALUES (?, ?, ?, ?)",
        (code, query, n_hits, created_at),
    )
    conn.commit()
    return int(cur.lastrowid)


def upsert_university(conn, row: dict) -> None:
    cols = ("slug", "name", "host", "ccr_slug", "rmp_school_id", "rmp_school_name")
    _upsert(conn, "universities", cols, "slug", row)


def upsert_course(conn, row: dict) -> None:
    cols = ("course_id", "code", "uni_slug", "page_url", "reduced_url", "slug",
            "title", "status", "created_at")
    _upsert(conn, "courses", cols, "course_id", row)


def set_course_status(conn, course_id: str, status: str) -> None:
    conn.execute("UPDATE courses SET status=? WHERE course_id=?", (status, course_id))
    conn.commit()


def upsert_professor(conn, row: dict) -> int | None:
    cols = ("name", "uni_slug", "rmp_legacy_id", "matched_name", "department",
            "avg_rating", "avg_difficulty", "num_ratings", "would_take_again",
            "rmp_url", "match_type")
    _upsert(conn, "professors", cols, "uni_slug, name", row)
    r = conn.execute("SELECT id FROM professors WHERE uni_slug=? AND name=?",
                     (row.get("uni_slug"), row.get("name"))).fetchone()
    return r["id"] if r else None


def link_course_professor(conn, course_id: str, professor_id: int, source: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO course_professors (course_id, professor_id, source) "
        "VALUES (?, ?, ?)",
        (course_id, professor_id, source),
    )
    conn.commit()


def upsert_ccr_ratings(conn, row: dict) -> None:
    cols = ("course_id", "ccr_url", "star_rating", "num_reviews", "difficulty",
            "student_satisfaction", "challenge_level", "grade_accessibility",
            "time_investment", "attendance_importance", "recommendation_rate")
    _upsert(conn, "ccr_ratings", cols, "course_id", row)


def record_deck(conn, row: dict) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO slide_decks "
        "(course_id, source_url, page_url, file_path, sha256, file_type, "
        " lecture_number, status) "
        "VALUES (:course_id, :source_url, :page_url, :file_path, :sha256, "
        ":file_type, :lecture_number, :status)",
        row,
    )
    conn.commit()


def add_link(conn, course_id: str, kind: str, url: str) -> None:
    if not url:
        return
    conn.execute(
        "INSERT OR IGNORE INTO links (course_id, kind, url) VALUES (?, ?, ?)",
        (course_id, kind, url),
    )
    conn.commit()


def known_sha(conn) -> set[str]:
    rows = conn.execute("SELECT sha256 FROM slide_decks WHERE sha256 IS NOT NULL")
    return {r["sha256"] for r in rows}


def seen_codes(conn) -> set[str]:
    rows = conn.execute("SELECT DISTINCT code FROM searches WHERE code IS NOT NULL")
    return {r["code"] for r in rows}


def counts(conn) -> dict:
    out = {}
    for table in ("searches", "universities", "courses", "ccr_ratings",
                  "professors", "slide_decks", "links"):
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return out


def downloaded_course_count(conn) -> int:
    """Number of distinct courses with at least one verified deck on disk."""
    return conn.execute(
        "SELECT COUNT(DISTINCT course_id) FROM slide_decks WHERE status='verified'"
    ).fetchone()[0]
