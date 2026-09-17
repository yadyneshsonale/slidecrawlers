"""Tests for the store: dedup scan and deck recording."""
import sqlite3

from slidegrab import store as store_mod
from slidegrab.store import Store


def _make_store(tmp_path, monkeypatch):
    db = tmp_path / "index.sqlite"
    monkeypatch.setattr(store_mod.settings, "index_db", db)
    return Store(path=db)


def test_scan_dataset_registers_existing_files(tmp_path, monkeypatch):
    data = tmp_path / "dataset"
    course = data / "iit_madras" / "cs7015"
    course.mkdir(parents=True)
    (course / "lecture_01.pdf").write_bytes(b"%PDF-1.4 fake")
    (course / "lecture_02.pdf").write_bytes(b"%PDF-1.4 fake")
    (course / "readme.txt").write_text("ignore me")
    monkeypatch.setattr(store_mod.settings, "data_dir", data)

    st = _make_store(tmp_path, monkeypatch)
    added = st.scan_dataset()
    assert added == 2  # only the two PDFs
    courses, decks = st.counts()
    assert courses == 1
    assert decks == 2
    # Re-scan must not double-count.
    assert st.scan_dataset() == 0
    st.close()


def test_record_deck_is_idempotent_on_path(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod.settings, "data_dir", tmp_path / "dataset")
    st = _make_store(tmp_path, monkeypatch)
    cid = st.upsert_course("iit_madras", "cs7015", "http://x")
    st.record_deck(cid, "/p/lecture_01.pdf", "http://a", "sha1", "pdf", 1)
    st.record_deck(cid, "/p/lecture_01.pdf", "http://a", "sha1", "pdf", 1, status="verified")
    _, decks = st.counts()
    assert decks == 1
    st.close()
