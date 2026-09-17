"""Tests for the course-pipeline knowledge layer (pure, no network)."""
from school_map import (
    extract_course_code,
    resolve_school,
    sibling_abbreviations,
)
from ccr_lookup import _has_ratings


def test_resolve_school_ucsd_is_not_university_of_san_diego():
    school = resolve_school("https://cseweb.ucsd.edu/classes/wi21/cse127-a/")
    assert school is not None
    assert school.canonical == "University of California, San Diego"
    assert school.ccr_slug == "uc-san-diego"
    assert school.rmp_keyword == "san diego"


def test_resolve_school_unknown_host():
    assert resolve_school("https://example.org/some/course") is None


def test_extract_cse127_from_url():
    code = extract_course_code("https://cseweb.ucsd.edu/classes/wi21/cse127-a/")
    assert code is not None
    assert code.prefix == "cse" and code.number == "127"
    assert code.ccr_code == "cse127"
    assert "cs" in code.disciplines


def test_extract_skips_term_segments():
    # 'wi21' must not be mistaken for a course code.
    code = extract_course_code("https://cseweb.ucsd.edu/classes/wi21/cse127-a/")
    assert code.raw != "wi21"


def test_extract_mit_numeric_code():
    code = extract_course_code(
        "https://ocw.mit.edu/courses/6-042j-mathematics-fall-2010/"
    )
    assert code is not None and code.prefix == "" and code.number == "6.042"


def test_sibling_abbreviations_prefers_exact_then_discipline():
    code = extract_course_code("https://x.edu/cse127/")
    sibs = sibling_abbreviations(code)
    assert sibs[0] == "cse"          # exact prefix first
    assert "cs" in sibs and "csci" in sibs   # discipline fallbacks present
    assert "mus" not in sibs and "poli" not in sibs


def test_has_ratings_detection():
    assert _has_ratings({"star_rating": 3.45}) is True
    assert _has_ratings({"num_reviews": 15}) is True
    assert _has_ratings({"challenge_level": 0.6}) is True
    empty = {k: None for k in (
        "star_rating", "num_reviews", "student_satisfaction", "challenge_level",
        "grade_accessibility", "time_investment", "attendance_importance",
        "recommendation_rate")}
    assert _has_ratings(empty) is False
