"""SQLite storage for slideratings.

One database holds universities (ranked by CCR rated-course count), courses,
CCR rating metrics, RMP professor ratings, the course<->professor links, the
downloaded slide decks and every relevant external link.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS universities (
    slug            TEXT PRIMARY KEY,
    name            TEXT,
    abbrev          TEXT,
    location        TEXT,
    students        INTEGER,
    total_courses   INTEGER,
    rated_courses   INTEGER,
    student_reviews INTEGER,
    rmp_school_id   TEXT,
    rmp_school_name TEXT,
    rank            INTEGER,
    ccr_url         TEXT,
    status          TEXT DEFAULT 'pending',   -- pending|done|error
    scraped_at      TEXT
);

CREATE TABLE IF NOT EXISTS courses (
    course_id     TEXT PRIMARY KEY,            -- e.g. cornell-cs1110
    uni_slug      TEXT NOT NULL,
    course_slug   TEXT,                        -- CCR slug, e.g. cs-1110
    course_code   TEXT,                        -- e.g. CS 1110
    course_number TEXT,                        -- e.g. 1110
    course_name   TEXT,
    department    TEXT,
    credits       TEXT,
    ccr_url       TEXT,
    official_url  TEXT,
    status        TEXT DEFAULT 'pending',      -- pending|done|error
    scraped_at    TEXT,
    UNIQUE(uni_slug, course_slug)
);

CREATE TABLE IF NOT EXISTS ccr_ratings (
    course_id             TEXT PRIMARY KEY REFERENCES courses(course_id),
    star_rating           REAL,
    num_reviews           INTEGER,
    difficulty            REAL,
    hours_per_week        REAL,
    recommend_pct         REAL,
    student_satisfaction  REAL,
    challenge_level       REAL,
    grade_accessibility   REAL,
    time_investment       REAL,
    attendance_importance REAL,
    recommendation_rate   REAL,
    scraped_at            TEXT
);

CREATE TABLE IF NOT EXISTS professors (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT,
    uni_slug         TEXT,
    rmp_legacy_id    INTEGER,
    matched_name     TEXT,
    department       TEXT,
    school           TEXT,
    avg_rating       REAL,
    avg_difficulty   REAL,
    num_ratings      INTEGER,
    would_take_again REAL,
    rmp_url          TEXT,
    match_type       TEXT,              -- unique|given|cs-dept|ambiguous|not_found|error
    scraped_at       TEXT,
    UNIQUE(uni_slug, name)
);

CREATE TABLE IF NOT EXISTS course_professors (
    course_id    TEXT NOT NULL REFERENCES courses(course_id),
    professor_id INTEGER NOT NULL REFERENCES professors(id),
    source       TEXT,                         -- ccr_review|catalog|websearch
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
    status         TEXT DEFAULT 'downloaded',  -- downloaded|verified|rejected
    reason         TEXT
);

CREATE TABLE IF NOT EXISTS links (
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    kind      TEXT NOT NULL,                   -- ccr|rmp|official|slide_source
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


def upsert_university(conn, row: dict) -> None:
    cols = ("slug", "name", "abbrev", "location", "students", "total_courses",
            "rated_courses", "student_reviews", "rmp_school_id", "rmp_school_name",
            "rank", "ccr_url", "status", "scraped_at")
    _upsert(conn, "universities", cols, "slug", row)


def set_university_status(conn, slug: str, status: str) -> None:
    conn.execute("UPDATE universities SET status=? WHERE slug=?", (status, slug))
    conn.commit()


def universities_by_rank(conn, only_pending: bool = False) -> list:
    sql = "SELECT * FROM universities"
    if only_pending:
        sql += " WHERE status != 'done'"
    sql += " ORDER BY rank ASC, rated_courses DESC"
    return list(conn.execute(sql))


def upsert_course(conn, row: dict) -> None:
    cols = ("course_id", "uni_slug", "course_slug", "course_code", "course_number",
            "course_name", "department", "credits", "ccr_url", "official_url",
            "status", "scraped_at")
    _upsert(conn, "courses", cols, "course_id", row)


def set_course_status(conn, course_id: str, status: str) -> None:
    conn.execute("UPDATE courses SET status=? WHERE course_id=?", (status, course_id))
    conn.commit()


def course_is_done(conn, course_id: str) -> bool:
    row = conn.execute("SELECT status FROM courses WHERE course_id=?",
                       (course_id,)).fetchone()
    if not row:
        return False
    return row["status"] == "done"


def upsert_ccr_ratings(conn, row: dict) -> None:
    cols = ("course_id", "star_rating", "num_reviews", "difficulty", "hours_per_week",
            "recommend_pct", "student_satisfaction", "challenge_level",
            "grade_accessibility", "time_investment", "attendance_importance",
            "recommendation_rate", "scraped_at")
    _upsert(conn, "ccr_ratings", cols, "course_id", row)


def upsert_professor(conn, row: dict) -> int | None:
    cols = ("name", "uni_slug", "rmp_legacy_id", "matched_name", "department",
            "school", "avg_rating", "avg_difficulty", "num_ratings",
            "would_take_again", "rmp_url", "match_type", "scraped_at")
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


def record_deck(conn, row: dict) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO slide_decks "
        "(course_id, source_url, page_url, file_path, sha256, file_type, "
        " lecture_number, status, reason) "
        "VALUES (:course_id, :source_url, :page_url, :file_path, :sha256, "
        ":file_type, :lecture_number, :status, :reason)",
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


def counts(conn) -> dict:
    out = {}
    for table in ("universities", "courses", "ccr_ratings", "professors", "slide_decks"):
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return out
