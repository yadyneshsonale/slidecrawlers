"""Tests for the rated4rmp RMP-course source selection (work/_dl_rated4rmp.py).

The resolve/gather/assess/download path is shared with rated4ccr and covered by
test_rated4ccr.py; here we only exercise the RMP per-(professor, course)
selection and the subagent search emit/ingest logic.
"""
import json
import sqlite3
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "work"))

import _dl_rated4rmp as rmp  # noqa: E402


def _make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE course_summary (legacy_id TEXT, prof_name TEXT, school TEXT, "
        "course TEXT, num_ratings INTEGER, avg_quality REAL, avg_difficulty REAL, "
        "profile_url TEXT)")
    con.executemany(
        "INSERT INTO course_summary (prof_name, school, course, num_ratings, "
        "avg_quality, avg_difficulty) VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
# norm_code
# --------------------------------------------------------------------------- #
def test_norm_code_uppercases_and_strips_whitespace():
    assert rmp.norm_code("cs 61a") == "CS61A"
    assert rmp.norm_code("  CS107 ") == "CS107"
    assert rmp.norm_code("6.042") == "6.042"
    assert rmp.norm_code(None) == ""


# --------------------------------------------------------------------------- #
# select_courses -- one row per (professor, course), keyed with the instructor
# --------------------------------------------------------------------------- #
def test_select_courses_one_row_per_professor(tmp_path, monkeypatch):
    db = tmp_path / "rmp.db"
    _make_db(db, [
        # same (school, code) taught by two profs -> TWO separate rows
        ("Ann Lee", "Stanford University", "CS106A", 10, 5.0, 2.0),
        ("Bob Roe", "Stanford University", "CS106A", 30, 4.0, 3.0),
        # a low-rated (prof, course) row -> filtered out
        ("Cy Poe", "Cornell University", "CS2110", 3, 3.0, 4.0),
    ])
    monkeypatch.setattr(rmp, "RMP_DB", db)

    courses = rmp.select_courses(min_ratings=4)
    assert len(courses) == 2                        # both CS106A profs, CS2110 dropped
    by_key = {c["course_school"]: c for c in courses}
    assert set(by_key) == {
        "CS106A - Stanford University - Ann Lee",
        "CS106A - Stanford University - Bob Roe",
    }
    bob = by_key["CS106A - Stanford University - Bob Roe"]
    assert bob["course_code"] == "CS106A"
    assert bob["school"] == "Stanford University"
    assert bob["instructor"] == "Bob Roe"
    assert bob["num_ratings"] == 30                 # per-prof, NOT summed
    assert bob["avg_quality"] == 4.0                # this prof's own rating
    assert bob["avg_difficulty"] == 3.0


def test_select_courses_normalises_code_variants_separately(tmp_path, monkeypatch):
    # RMP keeps "CS61A" and "61A" as distinct free-text codes; not merged.
    db = tmp_path / "rmp.db"
    _make_db(db, [
        ("De Nero", "University of California Berkeley", "CS61A", 12, 4.5, 3.0),
        ("De Nero", "University of California Berkeley", "61A", 9, 4.0, 3.0),
    ])
    monkeypatch.setattr(rmp, "RMP_DB", db)

    keys = {c["course_school"] for c in rmp.select_courses(min_ratings=4)}
    assert keys == {
        "CS61A - University of California Berkeley - De Nero",
        "61A - University of California Berkeley - De Nero"}


def test_select_courses_sorted_by_ratings_desc(tmp_path, monkeypatch):
    db = tmp_path / "rmp.db"
    _make_db(db, [
        ("P1", "MIT", "6.006", 5, 4.0, 4.0),
        ("P2", "MIT", "6.042", 20, 4.0, 4.0),
        ("P3", "MIT", "8.02", 11, 4.0, 4.0),
    ])
    monkeypatch.setattr(rmp, "RMP_DB", db)

    got = [c["num_ratings"] for c in rmp.select_courses(min_ratings=4)]
    assert got == [20, 11, 5]


def test_select_courses_skips_blank_course_and_prof(tmp_path, monkeypatch):
    db = tmp_path / "rmp.db"
    _make_db(db, [
        ("P1", "MIT", "", 8, 4.0, 4.0),
        ("P2", "MIT", None, 8, 4.0, 4.0),
        (None, "MIT", "6.041", 8, 4.0, 4.0),      # no instructor -> skipped
        ("P3", "MIT", "6.006", 8, 4.0, 4.0),
    ])
    monkeypatch.setattr(rmp, "RMP_DB", db)

    courses = rmp.select_courses(min_ratings=4)
    assert [c["course_code"] for c in courses] == ["6.006"]
    assert courses[0]["instructor"] == "P3"


# --------------------------------------------------------------------------- #
# subagent SEARCH: ingest links + resolve prefers them
# --------------------------------------------------------------------------- #
def test_ingest_search_stores_links_and_resolve_prefers_them(tmp_path):
    sdb = rmp.open_search_db(tmp_path)
    tsv = tmp_path / "links.tsv"
    tsv.write_text(
        "course_school\turl\treason\n"
        "CS61A - Cal\thttps://cs61a.org/\tofficial site\n"
        "Bad - X\tnot-a-url\tshould be skipped\n", encoding="utf-8")
    rmp.cmd_ingest_search(sdb, str(tsv))

    # only the valid http row was stored
    stored = {r[0]: r[1] for r in sdb.execute("SELECT course_school, url FROM resolved_links")}
    assert stored == {"CS61A - Cal": "https://cs61a.org/"}

    # resolve_link returns the subagent link without any network, even with --no-search
    args = types.SimpleNamespace(no_search=True, no_keyless=True, search_results=5)
    url, source, _ = rmp.resolve_link(
        sdb, {"course_school": "CS61A - Cal", "course_code": "CS61A", "school": "Cal"},
        args, None)
    assert url == "https://cs61a.org/" and source == "subagent-search"

    # a course with no stored link and --no-search resolves to nothing (no crash)
    url2, source2, _ = rmp.resolve_link(
        sdb, {"course_school": "Zzz - Q", "course_code": "Zzz", "school": "Q"},
        args, None)
    assert url2 is None and source2 == "no-search"


def test_emit_search_excludes_linked_and_settled(tmp_path, capsys):
    sdb = rmp.open_search_db(tmp_path)
    sdb.execute("INSERT INTO resolved_links VALUES (?,?,?,?,?)",
                ("A - S", "http://a", "", "subagent-search", "now"))
    sdb.execute("INSERT INTO progress (course_school, status) VALUES (?,?)",
                ("B - S", "complete"))
    sdb.commit()
    courses = [
        {"course_school": "A - S", "course_code": "A", "school": "S",
         "instructor": "Pa", "num_ratings": 9},
        {"course_school": "B - S", "course_code": "B", "school": "S",
         "instructor": "Pb", "num_ratings": 8},
        {"course_school": "C - S", "course_code": "C", "school": "S",
         "instructor": "Pc", "num_ratings": 7},
    ]
    rmp.cmd_emit_search(sdb, courses, 0, 0)
    out = json.loads(capsys.readouterr().out)
    # A has a link, B is settled(complete) -> only C is still pending
    assert [c["course_school"] for c in out] == ["C - S"]
    assert out[0]["instructor"] == "Pc"

