"""Tests for keyword-query generation."""
from config.settings import University, load_topics
from slidegrab.queries import build_queries

UNI = University(
    name="IIT Madras", slug="iit_madras", region="india", priority=100,
    aliases=("IIT Madras", "IITM"), domains=("iitm.ac.in",),
)


def test_code_queries_come_first_and_use_alias():
    qs = build_queries(UNI, load_topics())
    assert qs[0].kind == "code"
    assert "IIT Madras" in qs[0].text
    assert "CS7015" in " ".join(q.text for q in qs if q.kind == "code")


def test_topic_queries_present():
    qs = build_queries(UNI, load_topics())
    assert any(q.kind == "topic" and "deep learning" in q.text for q in qs)


def test_max_per_uni_caps_output():
    qs = build_queries(UNI, load_topics(), max_per_uni=3)
    assert len(qs) == 3


def test_no_duplicate_queries():
    qs = build_queries(UNI, load_topics())
    texts = [q.text.lower() for q in qs]
    assert len(texts) == len(set(texts))
