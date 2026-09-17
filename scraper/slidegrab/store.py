"""SQLite index of discovered/downloaded decks, with filesystem dedup.

On startup the store scans the existing `dataset/` tree so previously
downloaded files are treated as known (never re-downloaded). The index DB is
separate from the legacy `dataset.sqlite` (kept untouched as history).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from config.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    university  TEXT NOT NULL,
    course      TEXT NOT NULL,
    page_url    TEXT,
    UNIQUE(university, course)
);
CREATE TABLE IF NOT EXISTS decks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   INTEGER NOT NULL REFERENCES courses(id),
    url         TEXT,
    file_path   TEXT NOT NULL,
    sha256      TEXT,
    file_type   TEXT,
    status      TEXT NOT NULL DEFAULT 'downloaded',  -- downloaded|verified|rejected
    lecture_number INTEGER,
    UNIQUE(file_path)
);
"""


@dataclass
class Store:
    path: Path = settings.index_db

    def __post_init__(self) -> None:
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- courses ----
    def upsert_course(self, university: str, course: str, page_url: str | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO courses(university, course, page_url) VALUES (?,?,?) "
            "ON CONFLICT(university, course) DO UPDATE SET "
            "page_url=COALESCE(excluded.page_url, courses.page_url)",
            (university, course, page_url),
        )
        self.conn.commit()
        if cur.lastrowid:
            row = self.conn.execute(
                "SELECT id FROM courses WHERE university=? AND course=?",
                (university, course),
            ).fetchone()
            return row[0]
        return cur.lastrowid

    # ---- decks ----
    def record_deck(self, course_id: int, file_path: str, url: str | None,
                    sha256: str | None, file_type: str | None,
                    lecture_number: int | None, status: str = "downloaded") -> None:
        self.conn.execute(
            "INSERT INTO decks(course_id, url, file_path, sha256, file_type, "
            "lecture_number, status) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(file_path) DO UPDATE SET "
            "sha256=COALESCE(excluded.sha256, decks.sha256), status=excluded.status",
            (course_id, url, file_path, sha256, file_type, lecture_number, status),
        )
        self.conn.commit()

    def known_sha(self, sha256: str) -> bool:
        if not sha256:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM decks WHERE sha256=? LIMIT 1", (sha256,)
        ).fetchone()
        return row is not None

    def counts(self) -> tuple[int, int]:
        c = self.conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        d = self.conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
        return c, d

    def scan_dataset(self) -> int:
        """Register existing files under dataset/ so they count as downloaded."""
        added = 0
        data_dir = settings.data_dir
        if not data_dir.exists():
            return 0
        for uni_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
            for course_dir in sorted(p for p in uni_dir.iterdir() if p.is_dir()):
                files = [
                    f for f in course_dir.iterdir()
                    if f.suffix.lower() in (".pdf", ".ppt", ".pptx") and f.is_file()
                ]
                if not files:
                    continue
                course_id = self.upsert_course(uni_dir.name, course_dir.name, None)
                for f in files:
                    exists = self.conn.execute(
                        "SELECT 1 FROM decks WHERE file_path=?", (str(f),)
                    ).fetchone()
                    if exists:
                        continue
                    self.record_deck(
                        course_id, str(f), None, None,
                        f.suffix.lstrip(".").lower(), None, status="downloaded",
                    )
                    added += 1
        return added

    def close(self) -> None:
        self.conn.close()
